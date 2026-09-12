"""
Smoke test isolado: escaneia BLE por 10s e lista todo dispositivo
encontrado, indicando se ele anuncia os serviços que o app espera
(Heart Rate, Cycling Power, CSC, FTMS).

Rode isso ANTES de tentar o server.py inteiro. Se o rolo ou a cinta não
aparecerem aqui com o serviço certo, o problema é de pareamento/hardware,
não do código do app — não adianta depurar o resto até isso passar.

Uso:
    python tools/ble_scan_check.py
"""
import asyncio
from bleak import BleakScanner

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"
FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"

KNOWN = {
    HEART_RATE_SERVICE: "Heart Rate Service (cinta de FC)",
    CYCLING_POWER_SERVICE: "Cycling Power Service (potência)",
    CSC_SERVICE: "Cycling Speed and Cadence Service (cadência)",
    FTMS_SERVICE: "Fitness Machine Service / FTMS (rolo smart — potência+cadência+velocidade)",
}


async def main():
    print("Escaneando por 10s... deixe o rolo e a cinta de FC ligados e por perto.\n")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)

    if not devices:
        print("NENHUM dispositivo BLE encontrado. Verifique se:")
        print("  - o Bluetooth do PC está ligado")
        print("  - o rolo/cinta estão ligados e não conectados em outro app (Zwift, Garmin Connect, etc.)")
        return

    print(f"{len(devices)} dispositivo(s) BLE encontrado(s):\n")

    for addr, (dev, adv) in devices.items():
        name = dev.name or "(sem nome)"
        uuids = [u.lower() for u in (adv.service_uuids or [])]

        matched = [label for uuid, label in KNOWN.items() if uuid in uuids]

        print(f"- {name}  [{addr}]")
        if matched:
            for m in matched:
                print(f"    ✓ {m}")
        else:
            print(f"    (nenhum serviço conhecido do app — UUIDs anunciados: {uuids or 'nenhum'})")
        print()

    print("Se o rolo aparecer sem nenhum serviço conhecido marcado, ele pode estar")
    print("anunciando um serviço proprietário do fabricante — nesse caso, confirme")
    print("com um app genérico tipo 'nRF Connect' (Android/iOS) quais UUIDs ele expõe de verdade.")


if __name__ == "__main__":
    asyncio.run(main())
