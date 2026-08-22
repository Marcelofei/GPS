"""
Parser de arquivo .fit de treino planejado (workout), extraindo a lista de
etapas (nome, duração, alvo) para uso no dashboard.

Suporta o formato de workout exportado pelo Garmin Connect (.fit do tipo
'workout', não a atividade gravada).
"""
from dataclasses import dataclass
from typing import Optional
import fitparse


DURATION_TYPES_TIME = {"time", "repeat_until_time"}


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


def parse_workout_fit(path: str) -> list[WorkoutStep]:
    """Lê um .fit de workout e retorna a lista ordenada de WorkoutStep."""
    fitfile = fitparse.FitFile(path)
    steps: list[WorkoutStep] = []

    for i, msg in enumerate(fitfile.get_messages("workout_step")):
        fields = {f.name: f.value for f in msg}

        duration_type = str(fields.get("duration_type") or "open")
        duration_value = fields.get("duration_value")
        duration_seconds = None
        if duration_type in DURATION_TYPES_TIME and duration_value is not None:
            # duration_value em milissegundos para steps baseados em tempo
            duration_seconds = duration_value / 1000.0

        target_type = str(fields.get("target_type") or "open")
        target_low = fields.get("custom_target_value_low") or fields.get("target_hr_zone")
        target_high = fields.get("custom_target_value_high") or fields.get("target_hr_zone")

        intensity = str(fields.get("intensity") or "active")
        name = fields.get("wkt_step_name") or ""

        steps.append(
            WorkoutStep(
                index=i,
                name=name,
                duration_type=duration_type,
                duration_seconds=duration_seconds,
                duration_value_raw=duration_value,
                target_type=target_type,
                target_low=target_low,
                target_high=target_high,
                intensity=intensity,
            )
        )

    return steps


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
        print(f"[{s.index}] {s.label} — {dur} — alvo: {s.target_type} ({s.target_low}-{s.target_high})")
    print(f"Total planejado (etapas com tempo definido): {total_planned_seconds(ws):.0f}s")
