"""
Parser de arquivo .fit de treino planejado (workout), extraindo a lista de
etapas (nome, duração, alvo) para uso no dashboard.

Suporta o formato de workout exportado pelo Garmin Connect (.fit do tipo
'workout', não a atividade gravada).

IMPORTANTE — decisões baseadas em dado real, não suposição:
  - Alvo de FC vem em `custom_target_heart_rate_low/high`, não em
    `custom_target_value_low/high` genérico (nome diferente por tipo de
    alvo). O valor bruto tem offset de +100 (ex: raw=273 → 173 bpm real).
    Confirmado comparando a saída do fitparse com os valores reais
    exibidos pelo Garmin Connect para o mesmo treino.
  - Blocos de repetição ("repetir 5x") vêm como uma mensagem separada de
    controle (duration_type='repeat_until_steps_cmplt', com
    `duration_step` = índice de volta e `repeat_steps` = quantas vezes)
    referenciando um intervalo de steps anteriores — não vêm expandidos.
    Expandimos isso aqui pra virar uma lista plana de etapas reais.
  - Alvo de POTÊNCIA vem em `custom_target_power_low/high`, com o mesmo
    padrão de offset do FC — só que +1000 em vez de +100 (ex: raw=1087 →
    87W real; raw=1000/"watts_offset" → 0W, sentinela de "sem limite
    inferior"). Confirmado com dado real de um .fit com etapas em watts.
"""
from dataclasses import dataclass
from typing import Optional
import fitparse


DURATION_TYPES_TIME = {"time", "repeat_until_time"}
REPEAT_DURATION_TYPES = {
    "repeat_until_steps_cmplt",
    "repeat_until_time",
    "repeat_until_distance",
    "repeat_until_calories",
    "repeat_until_hr_less_than",
    "repeat_until_hr_greater_than",
    "repeat_until_power_less_than",
    "repeat_until_power_greater_than",
}


@dataclass
class WorkoutStep:
    index: int
    name: str
    duration_type: str          # "time", "distance", "open", etc.
    duration_seconds: Optional[float]   # None se não for baseado em tempo
    duration_value_raw: Optional[float] # valor cru (ex: metros, se distância)
    target_type: str            # "heart_rate", "power", "cadence", "open", etc.
    target_low: Optional[int]
    target_high: Optional[int]
    intensity: str = "active"   # "active", "rest", "warmup", "cooldown", "recovery"

    @property
    def label(self) -> str:
        if self.name and self.name.strip():
            return self.name
        return f"Etapa {self.index + 1} ({self.intensity})"


def _extract_target(target_type: Optional[str], fields: dict, fields_raw: dict) -> tuple:
    """Devolve (target_low, target_high) já convertidos pra unidade real,
    de acordo com o target_type. Cada tipo usa um campo e uma codificação
    diferentes no FIT — só o de heart_rate está confirmado com dado real."""
    if target_type == "heart_rate":
        # zona pré-definida (1-5) tem prioridade se estiver setada; senão,
        # usa o range customizado em bpm (raw - 100 = bpm real, confirmado).
        zone = fields.get("target_hr_zone")
        low_raw = fields_raw.get("custom_target_heart_rate_low")
        high_raw = fields_raw.get("custom_target_heart_rate_high")
        low = (low_raw - 100) if isinstance(low_raw, (int, float)) else None
        high = (high_raw - 100) if isinstance(high_raw, (int, float)) else None
        if zone:
            return zone, zone
        return low, high

    if target_type == "power":
        # confirmado com dado real: mesmo padrão de offset do FC, só que
        # +1000 em vez de +100 (raw=1087 → 87W; raw=1000/"watts_offset" → 0W,
        # sentinela de "sem limite inferior", igual ao 'bpm_offset' do FC).
        zone = fields.get("target_power_zone")
        low_raw = fields_raw.get("custom_target_power_low")
        high_raw = fields_raw.get("custom_target_power_high")
        low = (low_raw - 1000) if isinstance(low_raw, (int, float)) else None
        high = (high_raw - 1000) if isinstance(high_raw, (int, float)) else None
        if zone:
            return zone, zone
        return low, high

    if target_type == "cadence":
        # também não verificado com dado real ainda.
        low_raw = fields_raw.get("custom_target_cadence_low")
        high_raw = fields_raw.get("custom_target_cadence_high")
        return low_raw, high_raw

    if target_type == "speed":
        low_raw = fields_raw.get("custom_target_speed_low")
        high_raw = fields_raw.get("custom_target_speed_high")
        return low_raw, high_raw

    return None, None


def _parse_raw_messages(path: str) -> list[dict]:
    """Lê o .fit e devolve os workout_step em ordem de message_index, cada
    um como dict cru com 'fields' (valores decodificados) e 'fields_raw'
    (valores brutos, necessários pro offset de FC)."""
    fitfile = fitparse.FitFile(path)
    records = []
    for msg in fitfile.get_messages("workout_step"):
        fields = {f.name: f.value for f in msg}
        fields_raw = {f.name: f.raw_value for f in msg}
        records.append({"fields": fields, "fields_raw": fields_raw})
    return records


def _build_step(fields: dict, fields_raw: dict, index: int) -> WorkoutStep:
    duration_type = str(fields.get("duration_type") or "open")
    duration_value = fields.get("duration_value")
    duration_seconds = None
    if duration_type in DURATION_TYPES_TIME:
        # Garmin Connect exporta o campo já decodificado em segundos como
        # 'duration_time' (visto no dado real); 'duration_value' cru vem
        # em ms em alguns exports. Prioriza duration_time se presente.
        if fields.get("duration_time") is not None:
            duration_seconds = float(fields["duration_time"])
        elif duration_value is not None:
            duration_seconds = duration_value / 1000.0

    target_type = fields.get("target_type")
    target_type_str = str(target_type) if target_type is not None else "open"
    target_low, target_high = _extract_target(target_type, fields, fields_raw)

    intensity = str(fields.get("intensity") or "active")
    name = fields.get("wkt_step_name") or ""

    return WorkoutStep(
        index=index,
        name=name,
        duration_type=duration_type,
        duration_seconds=duration_seconds,
        duration_value_raw=duration_value,
        target_type=target_type_str,
        target_low=target_low,
        target_high=target_high,
        intensity=intensity,
    )


def _expand_repeats(raw_records: list[dict]) -> list[WorkoutStep]:
    """Expande blocos de repetição em uma lista plana de etapas reais.

    Mensagens de controle de repetição (duration_type em
    REPEAT_DURATION_TYPES) referenciam um intervalo anterior de steps via
    'duration_step' (índice de volta) e 'repeat_steps' (quantas vezes) —
    o bloco referenciado NÃO deve aparecer como etapa isolada na posição
    natural dele, só expandido aqui.

    Limitação conhecida: não trata repetição aninhada (um bloco de repeat
    dentro de outro bloco de repeat) — não observado em nenhum .fit real
    testado até agora, mas não implementado se aparecer.
    """
    n = len(raw_records)

    # 1) identifica quais índices são "consumidos" por algum bloco de repeat
    #    (não devem ser emitidos na posição natural deles)
    consumed: set[int] = set()
    control_indices: dict[int, dict] = {}  # idx -> {'start':, 'count':}

    for i, rec in enumerate(raw_records):
        dtype = str(rec["fields"].get("duration_type") or "")
        if dtype in REPEAT_DURATION_TYPES:
            start = rec["fields"].get("duration_step")
            count = rec["fields"].get("repeat_steps")
            if start is None or count is None:
                continue
            control_indices[i] = {"start": int(start), "count": int(count)}
            for j in range(int(start), i):
                consumed.add(j)

    # 2) percorre em ordem, expandindo os blocos de repeat e pulando o que
    #    já está marcado como consumido (só sai na expansão)
    output: list[WorkoutStep] = []
    next_index = 0

    for i, rec in enumerate(raw_records):
        if i in control_indices:
            info = control_indices[i]
            block_records = raw_records[info["start"]:i]
            for _ in range(info["count"]):
                for block_rec in block_records:
                    step = _build_step(block_rec["fields"], block_rec["fields_raw"], next_index)
                    output.append(step)
                    next_index += 1
            continue

        if i in consumed:
            continue

        step = _build_step(rec["fields"], rec["fields_raw"], next_index)
        output.append(step)
        next_index += 1

    return output


def parse_workout_fit(path: str) -> list[WorkoutStep]:
    """Lê um .fit de workout e retorna a lista ordenada e expandida (sem
    blocos de repeat não-expandidos) de WorkoutStep."""
    raw_records = _parse_raw_messages(path)
    return _expand_repeats(raw_records)


def total_planned_seconds(steps: list[WorkoutStep]) -> float:
    """Soma a duração de todas as etapas com duração baseada em tempo.
    Etapas 'open' (ex: até o usuário apertar lap) não entram na soma."""
    return sum(s.duration_seconds for s in steps if s.duration_seconds)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("uso: python workout_parser.py caminho_do_treino.fit")
        sys.exit(1)
    ws = parse_workout_fit(sys.argv[1])
    for s in ws:
        dur = f"{s.duration_seconds:.0f}s" if s.duration_seconds else s.duration_type
        print(f"[{s.index}] {s.label} — {dur} — alvo: {s.target_type} ({s.target_low}-{s.target_high}) — {s.intensity}")
    print(f"Total planejado (etapas com tempo definido): {total_planned_seconds(ws):.0f}s")
