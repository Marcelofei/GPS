"""
Listener BLE do rolo/trainer (FTMS Indoor Bike Data, com fallback pra
Cycling Power + CSC separados).

FC REMOVIDA DE PROPÓSITO (não é omissão, é decisão definitiva): o relógio
Garmin deste projeto não expõe o Heart Rate Service via GATT pra
conexões de terceiros — confirmado com DUAS pilhas Bluetooth totalmente
diferentes (bleak/Windows E nRF Connect/iOS), mesmo com atividade ativa
rodando no relógio. Ele aparece em "advertised services" mas nunca em
"connected services" nas duas. Ficar tentando conectar só gerava ciclo
infinito de reconexão sem chance real de sucesso — daí a remoção.

Fluxo atual pra FC: o relógio grava a própria FC internamente durante a
atividade dele; depois do treino, `merge_watch_hr.py` mescla essa FC (do
.fit exportado do Garmin Connect) com potência/cadência capturadas aqui
pelo dashboard, gerando um .tcx único pra upload. Ver esse módulo e
`MesclarTreino.vbs` pro fluxo completo.

Se um dia trocar o relógio por uma cinta de FC dedicada (Polar, Wahoo,
Garmin HRM-Pro etc.), essas cintas SÃO desenhadas pra expor o serviço
corretamente — nesse caso, reintroduzir um supervisor de FC aqui seria
simples (o padrão já existe no histórico do projeto), só que não vale a
pena manter esse código rodando à toa enquanto não tem uma cinta de
verdade.

Requer: bleak (`pip install bleak`)
"""
import asyncio
import logging
import threading

from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError, BleakCharacteristicNotFoundError

from ble_parsers import (
    CYCLING_POWER_SERVICE, CYCLING_POWER_MEASUREMENT,
    CSC_SERVICE, CSC_MEASUREMENT,
    FTMS_SERVICE, FTMS_INDOOR_BIKE_DATA,
    parse_cycling_power_measurement,
    parse_csc_measurement,
    parse_ftms_indoor_bike_data,
    CadenceTracker,
)
from state import WorkoutState

_logger = logging.getLogger(__name__)

SCAN_TIMEOUT_SECONDS = 10.0
RECONNECT_DELAY_SECONDS = 5.0

# Race condition conhecida do bleak no Windows: a conexão BLE completa
# (client.is_connected = True) antes da descoberta de serviços/GATT ter
# terminado de verdade — start_notify então falha achando que o
# characteristic não existe, mesmo o dispositivo suportando normalmente.
NOTIFY_RETRY_ATTEMPTS = 2
NOTIFY_RETRY_DELAY_SECONDS = 1.5


async def _start_notify_with_retry(client, char_uuid, callback, logger, label):
    """Tenta start_notify, e se falhar (característico não encontrado —
    descoberta de serviço incompleta —, OU qualquer outro BleakError tipo
    'Unreachable' — falha de conectividade BLE no exato momento de ativar
    a notificação), espera um pouco e tenta de novo antes de desistir.

    Se depois de uma falha o client.is_connected virar False, a conexão
    em si morreu — continuar chamando start_notify no MESMO client não
    adianta nada. Desiste na hora nesse caso, deixando o supervisor
    externo fazer um reconnect completo mais rápido."""
    last_exc = None
    for attempt in range(1, NOTIFY_RETRY_ATTEMPTS + 2):
        try:
            await client.start_notify(char_uuid, callback)
            return
        except BleakError as exc:
            last_exc = exc
            kind = "characteristic não encontrado" if isinstance(exc, BleakCharacteristicNotFoundError) else f"falha BLE ({exc})"

            if not client.is_connected:
                logger.warning(
                    "%s: %s na tentativa %d — conexão já morreu de verdade "
                    "(client.is_connected=False), desistindo já em vez de "
                    "continuar tentando num client morto.",
                    label, kind, attempt,
                )
                raise

            logger.warning(
                "%s: %s na tentativa %d — tentando de novo em %.1fs...",
                label, kind, attempt, NOTIFY_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(NOTIFY_RETRY_DELAY_SECONDS)
    raise last_exc


class BleSensorListener:
    def __init__(self, state: WorkoutState, trainer_name_hint: str = ""):
        """
        trainer_name_hint: substring do nome do dispositivo BLE do rolo,
        pra facilitar identificar qual é qual no scan (ex: "Wahoo",
        "KICKR", "Elite"). Deixe vazio pra pegar o primeiro que anunciar
        o serviço certo.
        """
        self.state = state
        self.trainer_name_hint = trainer_name_hint.lower()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_requested_trainer = threading.Event()

    def request_reconnect(self):
        """Chamado pela rota /reconnect — acorda o supervisor do rolo imediatamente."""
        self._reconnect_requested_trainer.set()

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        await self._trainer_supervisor()

    async def _wait_before_retry(self, reconnect_event: threading.Event):
        """Espera RECONNECT_DELAY_SECONDS antes da próxima tentativa, MAS
        acorda na hora se alguém pedir reconexão manual pelo botão da UI."""
        step = 0.25
        waited = 0.0
        while waited < RECONNECT_DELAY_SECONDS:
            if reconnect_event.is_set():
                reconnect_event.clear()
                return
            await asyncio.sleep(step)
            waited += step
        reconnect_event.clear()

    async def _trainer_supervisor(self):
        while True:
            try:
                await self._scan_and_connect_trainer()
            except asyncio.CancelledError:
                _logger.warning(
                    "Ciclo BLE do rolo recebeu CancelledError inesperado "
                    "(não é cancelamento intencional nosso) — tratando como falha "
                    "transitória, retomando em até %.0fs.",
                    RECONNECT_DELAY_SECONDS,
                )
            except Exception:
                _logger.exception(
                    "Erro no ciclo BLE do rolo — retomando em até %.0fs",
                    RECONNECT_DELAY_SECONDS,
                )
            await self._wait_before_retry(self._reconnect_requested_trainer)

    async def _scan_and_connect_trainer(self):
        _logger.info("Escaneando rolo/trainer por %.0fs...", SCAN_TIMEOUT_SECONDS)
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SECONDS, return_adv=True)

        trainer_device = None
        for addr, (dev, adv) in devices.items():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if FTMS_SERVICE in uuids or CYCLING_POWER_SERVICE in uuids:
                name = (dev.name or "").lower()
                if not self.trainer_name_hint or self.trainer_name_hint in name:
                    trainer_device = dev
                    _logger.info("Rolo/trainer encontrado: %s (%s)", dev.name, addr)
                    break

        if not trainer_device:
            _logger.warning("Nenhum rolo/trainer BLE encontrado no scan.")
            self.state.set_trainer_connected(False)
            return

        await self._connect_trainer(trainer_device)

    async def _connect_trainer(self, device):
        # instanciado por conexão, não por listener: se a BLE cair e
        # reconectar, o cálculo de cadência recomeça do zero em vez de usar
        # um baseline de revoluções anterior à queda (que geraria um delta
        # espúrio na primeira leitura pós-reconexão).
        power_cadence_tracker = CadenceTracker()

        async with BleakClient(device) as client:
            self.state.set_trainer_connected(True)
            try:
                services = client.services

                # tenta FTMS primeiro (potência + cadência + velocidade num único characteristic)
                ftms_char = services.get_characteristic(FTMS_INDOOR_BIKE_DATA)
                power_char = services.get_characteristic(CYCLING_POWER_MEASUREMENT)
                csc_char = services.get_characteristic(CSC_MEASUREMENT)

                if ftms_char:
                    _logger.info("Conectado ao rolo via FTMS Indoor Bike Data.")

                    def ftms_callback(_, data: bytearray):
                        parsed = parse_ftms_indoor_bike_data(bytes(data))
                        if "instantaneous_power" in parsed:
                            self.state.update_power(int(parsed["instantaneous_power"]))
                        if "instantaneous_cadence" in parsed:
                            self.state.update_cadence(int(round(parsed["instantaneous_cadence"])))

                    await _start_notify_with_retry(client, FTMS_INDOOR_BIKE_DATA, ftms_callback, _logger, "Rolo (FTMS)")

                else:
                    # fallback: potência via Cycling Power Service, cadência via
                    # crank revolution data do próprio characteristic de potência,
                    # ou via CSC separado se disponível.
                    if power_char:
                        _logger.info("Conectado ao rolo via Cycling Power Measurement.")

                        def power_callback(_, data: bytearray):
                            parsed = parse_cycling_power_measurement(bytes(data))
                            if "power" in parsed:
                                self.state.update_power(int(parsed["power"]))
                            if "cumulative_crank_revs" in parsed:
                                rpm = power_cadence_tracker.update(
                                    parsed["cumulative_crank_revs"],
                                    parsed["last_crank_event_time"],
                                )
                                if rpm is not None:
                                    self.state.update_cadence(int(round(rpm)))

                        await _start_notify_with_retry(client, CYCLING_POWER_MEASUREMENT, power_callback, _logger, "Rolo (Cycling Power)")

                    if csc_char:
                        _logger.info("Cadência adicional via CSC Measurement.")
                        csc_tracker = CadenceTracker()

                        def csc_callback(_, data: bytearray):
                            parsed = parse_csc_measurement(bytes(data))
                            if "cumulative_crank_revs" in parsed:
                                rpm = csc_tracker.update(
                                    parsed["cumulative_crank_revs"],
                                    parsed["last_crank_event_time"],
                                )
                                if rpm is not None:
                                    self.state.update_cadence(int(round(rpm)))

                        await _start_notify_with_retry(client, CSC_MEASUREMENT, csc_callback, _logger, "Rolo (CSC)")

                    if not power_char and not csc_char:
                        _logger.error("Rolo não expôs FTMS, Cycling Power nem CSC — verifique o dispositivo.")

                while client.is_connected:
                    await asyncio.sleep(1)
            finally:
                self.state.set_trainer_connected(False)
                _logger.warning("Rolo/trainer desconectado.")

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
