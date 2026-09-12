"""
Diagnóstico: verifica se a cinta/relógio transmite FC embutida no próprio
pacote de advertisement (campo "Service Data"), em vez de exigir conexão
GATT + notificação — técnica real de BLE usada por dispositivos em modo
"broadcast", que evita o custo de bateria/latência de manter uma conexão.

Se isso confirmar dado presente, o fix é trocar a abordagem de FC no
projeto: em vez de BleakClient + start_notify, usar BleakScanner
continuamente e ler service_data[HEART_RATE_SERVICE] de cada advertisement
— sem NUNCA se conectar ao dispositivo.

Uso:
    python inspect_hr_broadcast_advertisement.py
"""
import asyncio
from bleak import BleakScanner

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"

# reaproveita o mesmo parser que o projeto já usa pra notificação GATT —
# o formato de bytes (flags + valor) é o mesmo definido pela Bluetooth SIG,
# então deve funcionar igual se o dado realmente vier no service_data.
def parse_heart_rate_measurement(data: bytes) -> dict:
    flags = data[0]
    hr_is_uint16 = bool(flags & 0x01)
    if hr_is_uint16:
        hr = int.from_bytes(data[1:3], "little")
    else:
        hr = data[1]
    return {"heart_rate": hr}


async def main():
    print("Escaneando continuamente por 20s — pedale/deixe a cinta ativa,")
    print("procurando FC dentro do próprio pacote de anúncio (sem conectar)...\n")

    found_any_service_data = False
    seen_macs = set()

    def callback(device, advertisement_data):
        nonlocal found_any_service_data
        uuids = [u.lower() for u in (advertisement_data.service_uuids or [])]
        if HEART_RATE_SERVICE not in uuids:
            return

        if device.address not in seen_macs:
            seen_macs.add(device.address)
            print(f"Dispositivo anunciando Heart Rate Service: {device.name} ({device.address})")

        raw = advertisement_data.service_data.get(HEART_RATE_SERVICE)
        if raw:
            found_any_service_data = True
            parsed = parse_heart_rate_measurement(raw)
            print(f"  >>> FC encontrada DIRETO no advertisement: {parsed['heart_rate']} bpm  (bytes crus: {raw.hex()})")

    scanner = BleakScanner(callback)
    await scanner.start()
    await asyncio.sleep(20.0)
    await scanner.stop()

    print()
    print("=" * 70)
    if found_any_service_data:
        print("RESULTADO: FC chegou pelo advertisement (service_data), sem conexão GATT.")
        print("Isso confirma que o dispositivo transmite em modo broadcast puro —")
        print("o fix é reescrever a leitura de FC pra usar scan contínuo em vez de")
        print("conectar via BleakClient.")
    else:
        print("RESULTADO: nenhum dado de FC apareceu no service_data do advertisement,")
        print("mesmo o dispositivo anunciando o serviço. Precisa investigar mais —")
        print("pode ser bonding/pareamento obrigatório, ou outro formato de transmissão.")


if __name__ == "__main__":
    asyncio.run(main())
