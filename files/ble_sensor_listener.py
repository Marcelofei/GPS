"""
Listener BLE multi-dispositivo: conecta na cinta de FC (Heart Rate Service)
e no rolo/trainer (FTMS Indoor Bike Data, com fallback pra Cycling Power +
CSC separados) e alimenta o WorkoutState em tempo real.

Diferença crítica vs. ANT+: BLE normalmente é conexão exclusiva 1-para-1.
Isso significa que, se o relógio também estiver conectado por BLE na cinta
de FC ao mesmo tempo, pode haver conflito — a maioria das cintas modernas
suporta múltiplas conexões BLE simultâneas, mas não é garantido em todos os
modelos. Teste isso especificamente: suba o app com o relógio já gravando
a atividade e confira se ambos recebem dado.

Todo o ciclo de scan+conexão é reexecutado automaticamente se cair (sensor
fora de alcance, bateria, etc.) — sem isso, um único BLE drop no meio de um
treino de 45-90min mataria a leitura pro resto da sessão.

Requer: bleak (`pip install bleak`)
"""
import asyncio
import logging
import threading

from bleak import BleakScanner, BleakClient

from ble_parsers import (
    HEART_RATE_SERVICE, HEART_RATE_MEASUREMENT,
    CYCLING_POWER_SERVICE, CYCLING_POWER_MEASUREMENT,
    CSC_SERVICE, CSC_MEASUREMENT,
    FTMS_SERVICE, FTMS_INDOOR_BIKE_DATA,
    parse_heart_rate_measurement,
    parse_cycling_power_measurement,
    parse_csc_measurement,
    parse_ftms_indoor_bike_data,
    CadenceTracker,
)
from state import WorkoutState

_logger = logging.getLogger(__name__)

SCAN_TIMEOUT_SECONDS = 10.0
RECONNECT_DELAY_SECONDS = 5.0


class BleSensorListener:
    def __init__(self, state: WorkoutState, hr_name_hint: str = "", trainer_name_hint: str = ""):
        """
        hr_name_hint / trainer_name_hint: substring do nome do dispositivo
        BLE, pra facilitar identificar qual é qual no scan (ex: "Wahoo",
        "KICKR", "Polar", "Garmin"). Deixe vazio pra listar tudo e escolher
        pelo primeiro que anunciar o serviço certo.
        """
        self.state = state
        self.hr_name_hint = hr_name_hint.lower()
        self.trainer_name_hint = trainer_name_hint.lower()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        while True:
            try:
                await self._scan_and_connect()
            except Exception:
                _logger.exception("Erro no ciclo BLE — retomando em %.0fs", RECONNECT_DELAY_SECONDS)
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _scan_and_connect(self):
        _logger.info("Escaneando dispositivos BLE por %.0fs...", SCAN_TIMEOUT_SECONDS)
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_SECONDS, return_adv=True)

        hr_device = None
        trainer_device = None

        for addr, (dev, adv) in devices.items():
            name = (dev.name or "").lower()
            uuids = [u.lower() for u in (adv.service_uuids or [])]

            is_hr = HEART_RATE_SERVICE in uuids
            is_trainer = FTMS_SERVICE in uuids or CYCLING_POWER_SERVICE in uuids

            if is_hr and hr_device is None:
                if not self.hr_name_hint or self.hr_name_hint in name:
                    hr_device = dev
                    _logger.info("Cinta de FC encontrada: %s (%s)", dev.name, addr)

            if is_trainer and trainer_device is None:
                if not self.trainer_name_hint or self.trainer_name_hint in name:
                    trainer_device = dev
                    _logger.info("Rolo/trainer encontrado: %s (%s)", dev.name, addr)

        if not hr_device:
            _logger.warning("Nenhuma cinta de FC BLE encontrada no scan.")
        if not trainer_device:
            _logger.warning("Nenhum rolo/trainer BLE encontrado no scan.")

        tasks = []
        if hr_device:
            tasks.append(asyncio.create_task(self._connect_hr(hr_device)))
        if trainer_device:
            tasks.append(asyncio.create_task(self._connect_trainer(trainer_device)))

        if not tasks:
            return

        # se qualquer sensor cair, TODAS as conexões desta rodada são
        # encerradas antes do loop externo tentar de novo — sem isso, a
        # task do sensor que não caiu continua viva e conflita com a nova
        # tentativa de conexão no próximo ciclo (BLE normalmente só aceita
        # 1 conexão por dispositivo).
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            exc = task.exception()
            if exc:
                raise exc

    async def _connect_hr(self, device):
        async with BleakClient(device) as client:
            _logger.info("Conectado à cinta de FC.")

            def callback(_, data: bytearray):
                parsed = parse_heart_rate_measurement(bytes(data))
                if parsed.get("heart_rate"):
                    self.state.update_heart_rate(parsed["heart_rate"])

            await client.start_notify(HEART_RATE_MEASUREMENT, callback)
            while client.is_connected:
                await asyncio.sleep(1)

    async def _connect_trainer(self, device):
        # instanciado por conexão, não por listener: se a BLE cair e
        # reconectar, o cálculo de cadência recomeça do zero em vez de usar
        # um baseline de revoluções anterior à queda (que geraria um delta
        # espúrio na primeira leitura pós-reconexão).
        power_cadence_tracker = CadenceTracker()

        async with BleakClient(device) as client:
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

                await client.start_notify(FTMS_INDOOR_BIKE_DATA, ftms_callback)

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

                    await client.start_notify(CYCLING_POWER_MEASUREMENT, power_callback)

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

                    await client.start_notify(CSC_MEASUREMENT, csc_callback)

                if not power_char and not csc_char:
                    _logger.error("Rolo não expôs FTMS, Cycling Power nem CSC — verifique o dispositivo.")

            while client.is_connected:
                await asyncio.sleep(1)

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
