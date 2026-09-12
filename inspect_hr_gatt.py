"""
Diagnóstico: conecta especificamente na cinta de FC e imprime TODA a
árvore de serviços/características que o bleak consegue ver depois de
conectar — sem tentar assinar nenhuma notificação, só listar.

Isso separa duas causas bem diferentes pro erro
'BleakCharacteristicNotFoundError: Characteristic 00002a37... was not found':

  (A) O serviço de FC (0x180D) nem aparece na lista -> a cinta genuinamente
      não expõe esse serviço nessa conexão (possível trava de bonding/
      pareamento, ou o "Broadcast HR" desse modelo não funciona como GATT
      conectável completo).

  (B) O serviço aparece, mas com um característico de UUID diferente do
      padrão 0x2A37 -> firmware não-padrão, precisa ajustar o UUID usado
      no ble_parsers.py.

Uso:
    python inspect_hr_gatt.py
"""
import asyncio
from bleak import BleakScanner, BleakClient

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"


async def main():
    print("Escaneando por 10s — deixe a cinta/relógio com o broadcast de FC ativado...\n")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)

    hr_device = None
    for addr, (dev, adv) in devices.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if HEART_RATE_SERVICE in uuids:
            hr_device = dev
            print(f"Encontrado dispositivo anunciando Heart Rate Service: {dev.name} ({addr})\n")
            break

    if not hr_device:
        print("Nenhum dispositivo anunciando Heart Rate Service foi encontrado no scan.")
        print("Confirme que o broadcast de FC está ativado e tente de novo.")
        return

    print(f"Conectando em {hr_device.name} (forçando pareamento/bonding — pair=True)...\n")
    print("ATENÇÃO: pode aparecer uma notificação do Windows pedindo pra confirmar")
    print("o pareamento — se aparecer, clique/confirme rapidamente, o script está")
    print("esperando a conexão terminar.\n")
    async with BleakClient(hr_device, pair=True) as client:
        print("Conectado. Listando TODOS os serviços e características encontrados:\n")
        print("=" * 70)

        found_hr_service = False
        for service in client.services:
            marker = " <-- Heart Rate Service (0x180D)" if service.uuid.lower() == HEART_RATE_SERVICE else ""
            if marker:
                found_hr_service = True
            print(f"Serviço: {service.uuid}  ({service.description}){marker}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                is_standard_hr_char = char.uuid.lower() == "00002a37-0000-1000-8000-00805f9b34fb"
                char_marker = " <-- Heart Rate Measurement PADRÃO (0x2A37)" if is_standard_hr_char else ""
                print(f"    Característico: {char.uuid}  [{props}]  ({char.description}){char_marker}")
        print("=" * 70)

        print()
        if found_hr_service:
            print("RESULTADO: o serviço 0x180D FOI encontrado nessa conexão.")
            print("Confira acima se algum característico dentro dele tem UUID")
            print("diferente de 0x2A37 — pode ser esse o característico real de FC")
            print("nesse firmware, e o código precisaria usar esse UUID em vez do padrão.")
        else:
            print("RESULTADO: o serviço 0x180D NÃO apareceu na lista de serviços")
            print("dessa conexão, mesmo o dispositivo tendo anunciado ele no scan.")
            print("Isso sugere que o 'Broadcast Heart Rate' desse relógio pode exigir")
            print("pareamento/bonding a nível de Windows antes de expor o serviço de")
            print("verdade via GATT — ou que esse modo não suporta conexão GATT")
            print("completa de terceiros, só o anúncio (advertisement).")


if __name__ == "__main__":
    asyncio.run(main())
