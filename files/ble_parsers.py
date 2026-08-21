"""
Parsers dos characteristics BLE GATT padrão usados por sensores fitness.
Cada função recebe bytes crus (o que a notificação BLE entrega) e devolve
um dict com os campos decodificados. Sem dependência de hardware — só
matemática de bits, conforme especificação Bluetooth SIG — então é
testável isoladamente.

UUIDs padrão (Bluetooth SIG):
  Heart Rate Service:            0000180d-...
  Heart Rate Measurement:        00002a37-...
  Cycling Power Service:         00001818-...
  Cycling Power Measurement:     00002a63-...
  Cycling Speed and Cadence Svc: 00001816-...
  CSC Measurement:                00002a5b-...
  Fitness Machine Service (FTMS): 00001826-...
  Indoor Bike Data:               00002ad2-...
"""
from dataclasses import dataclass
from typing import Optional

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"

CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"
CSC_MEASUREMENT = "00002a5b-0000-1000-8000-00805f9b34fb"

FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
FTMS_INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"


def parse_heart_rate_measurement(data: bytes) -> dict:
    """Heart Rate Measurement (0x2A37). Bit 0 dos flags define se o valor
    de FC vem em UINT8 ou UINT16."""
    flags = data[0]
    hr_is_uint16 = bool(flags & 0x01)
    if hr_is_uint16:
        hr = int.from_bytes(data[1:3], "little")
    else:
        hr = data[1]
    return {"heart_rate": hr}


def parse_cycling_power_measurement(data: bytes) -> dict:
    """Cycling Power Measurement (0x2A63).
    Bytes 0-1: flags (little-endian, 16 bits)
    Bytes 2-3: instantaneous power (sint16, watts) — sempre presente.
    Campos seguintes são condicionais aos flags; aqui extraímos apenas
    Crank Revolution Data (bit 5), necessário para calcular cadência.
    """
    flags = int.from_bytes(data[0:2], "little")
    power = int.from_bytes(data[2:4], "little", signed=True)

    result = {"power": power}

    offset = 4
    # bit 0: Pedal Power Balance Present (1 byte)
    if flags & (1 << 0):
        offset += 1
    # bit 2: Accumulated Torque Present (2 bytes)
    if flags & (1 << 2):
        offset += 2
    # bit 4: Wheel Revolution Data Present (6 bytes: uint32 + uint16)
    if flags & (1 << 4):
        offset += 6
    # bit 5: Crank Revolution Data Present (4 bytes: uint16 cumulative revs + uint16 last event time 1/1024s)
    if flags & (1 << 5):
        cumulative_crank_revs = int.from_bytes(data[offset:offset + 2], "little")
        last_crank_event_time = int.from_bytes(data[offset + 2:offset + 4], "little")
        result["cumulative_crank_revs"] = cumulative_crank_revs
        result["last_crank_event_time"] = last_crank_event_time  # unidade: 1/1024 s

    return result


def parse_csc_measurement(data: bytes) -> dict:
    """CSC Measurement (0x2A5B) — Cycling Speed and Cadence.
    Bytes 0: flags. bit0: Wheel Revolution Data Present. bit1: Crank Revolution Data Present.
    """
    flags = data[0]
    offset = 1
    result = {}

    if flags & 0x01:
        cumulative_wheel_revs = int.from_bytes(data[offset:offset + 4], "little")
        last_wheel_event_time = int.from_bytes(data[offset + 4:offset + 6], "little")
        result["cumulative_wheel_revs"] = cumulative_wheel_revs
        result["last_wheel_event_time"] = last_wheel_event_time
        offset += 6

    if flags & 0x02:
        cumulative_crank_revs = int.from_bytes(data[offset:offset + 2], "little")
        last_crank_event_time = int.from_bytes(data[offset + 2:offset + 4], "little")
        result["cumulative_crank_revs"] = cumulative_crank_revs
        result["last_crank_event_time"] = last_crank_event_time
        offset += 4

    return result


def parse_ftms_indoor_bike_data(data: bytes) -> dict:
    """Indoor Bike Data (0x2AD2) do Fitness Machine Service.
    Flags de 16 bits (little-endian) definem quais campos seguem, na ordem:
      bit0=0 -> Instantaneous Speed presente (uint16, 0.01 km/h)   [nota: bit0=1 = "More Data", ou seja ausente]
      bit1   -> Average Speed presente (uint16, 0.01 km/h)
      bit2   -> Instantaneous Cadence presente (uint16, 0.5 rpm)
      bit3   -> Average Cadence presente (uint16, 0.5 rpm)
      bit4   -> Total Distance presente (uint24, metros)
      bit5   -> Resistance Level presente (sint16)
      bit6   -> Instantaneous Power presente (sint16, watts)
      bit7   -> Average Power presente (sint16, watts)
      bit8   -> Expended Energy presente (3 campos)
      bit9   -> Heart Rate presente (uint8, bpm)
      bit10  -> Metabolic Equivalent presente (uint8)
      bit11  -> Elapsed Time presente (uint16, s)
      bit12  -> Remaining Time presente (uint16, s)
    """
    flags = int.from_bytes(data[0:2], "little")
    offset = 2
    result = {}

    more_data = bool(flags & (1 << 0))  # se True, speed instantânea NÃO está presente
    if not more_data:
        speed_raw = int.from_bytes(data[offset:offset + 2], "little")
        result["instantaneous_speed_kmh"] = speed_raw * 0.01
        offset += 2

    if flags & (1 << 1):
        offset += 2  # average speed (ignorado)

    if flags & (1 << 2):
        cadence_raw = int.from_bytes(data[offset:offset + 2], "little")
        result["instantaneous_cadence"] = cadence_raw * 0.5
        offset += 2

    if flags & (1 << 3):
        offset += 2  # average cadence (ignorado)

    if flags & (1 << 4):
        offset += 3  # total distance (ignorado)

    if flags & (1 << 5):
        offset += 2  # resistance level (ignorado)

    if flags & (1 << 6):
        power_raw = int.from_bytes(data[offset:offset + 2], "little", signed=True)
        result["instantaneous_power"] = power_raw
        offset += 2

    if flags & (1 << 7):
        offset += 2  # average power (ignorado)

    if flags & (1 << 8):
        offset += 5  # expended energy: total(2) + per hour(2) + per min(1) (ignorado)

    if flags & (1 << 9):
        result["heart_rate"] = data[offset]
        offset += 1

    return result


@dataclass
class CadenceTracker:
    """Cadência via characteristics de revolução (Cycling Power ou CSC) exige
    delta entre duas leituras — não vem pronta em rpm. Esta classe guarda a
    última leitura e calcula o delta a cada nova notificação."""
    last_revs: Optional[int] = None
    last_event_time: Optional[int] = None  # em unidades de 1/1024s

    def update(self, cumulative_revs: int, last_event_time: int) -> Optional[float]:
        if self.last_revs is None:
            self.last_revs, self.last_event_time = cumulative_revs, last_event_time
            return None

        delta_revs = (cumulative_revs - self.last_revs) & 0xFFFF
        delta_time_ticks = (last_event_time - self.last_event_time) & 0xFFFF

        self.last_revs, self.last_event_time = cumulative_revs, last_event_time

        if delta_time_ticks == 0:
            return None

        delta_time_seconds = delta_time_ticks / 1024.0
        rpm = (delta_revs / delta_time_seconds) * 60.0
        return rpm
