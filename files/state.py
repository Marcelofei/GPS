"""
Estado compartilhado do treino: valores instantâneos dos sensores +
cronômetro total + progressão pela lista de etapas do .fit carregado.

Este módulo não sabe nada de ANT+ nem de Flask — é puro estado e lógica de
tempo, para ser testável isoladamente.
"""
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from workout_parser import WorkoutStep


@dataclass
class SensorSnapshot:
    heart_rate: int = 0
    power: int = 0
    cadence: int = 0
    last_hr_update: float = 0.0
    last_power_update: float = 0.0
    last_cadence_update: float = 0.0


class WorkoutState:
    def __init__(self, steps: list[WorkoutStep] | None = None):
        self._lock = Lock()
        self.sensors = SensorSnapshot()
        self.steps: list[WorkoutStep] = steps or []
        self.current_step_idx: int = 0
        self.workout_start_ts: Optional[float] = None
        self.step_start_ts: Optional[float] = None
        self.finished: bool = False
        self.samples: list[dict] = []

    # ---------- ingestão de sensores (chamado pelo listener BLE) ----------
    def update_heart_rate(self, bpm: int):
        with self._lock:
            self.sensors.heart_rate = bpm
            self.sensors.last_hr_update = time.time()

    def update_power(self, watts: int):
        with self._lock:
            self.sensors.power = watts
            self.sensors.last_power_update = time.time()

    def update_cadence(self, rpm: int):
        with self._lock:
            self.sensors.cadence = rpm
            self.sensors.last_cadence_update = time.time()

    # ---------- controle do treino ----------
    def start_workout(self):
        with self._lock:
            now = time.time()
            self.workout_start_ts = now
            self.step_start_ts = now
            self.current_step_idx = 0
            self.finished = False

    def advance_step_if_needed(self):
        """Verifica se a etapa atual (baseada em tempo) expirou e avança.
        Etapas sem duração definida ('open') exigem avanço manual via
        next_step_manual()."""
        with self._lock:
            if self.finished or self.workout_start_ts is None:
                return
            if self.current_step_idx >= len(self.steps):
                self.finished = True
                return

            step = self.steps[self.current_step_idx]
            if step.duration_seconds:
                elapsed_in_step = time.time() - self.step_start_ts
                if elapsed_in_step >= step.duration_seconds:
                    self._advance_locked()

    def next_step_manual(self):
        """Avanço manual de etapa (para steps 'open' ou correção manual)."""
        with self._lock:
            self._advance_locked()

    def _advance_locked(self):
        self.current_step_idx += 1
        self.step_start_ts = time.time()
        if self.current_step_idx >= len(self.steps):
            self.finished = True

    # ---------- gravação de série temporal (para exportação posterior) ----------
    def record_sample(self):
        """Registra uma amostra do instante atual — chamado a cada tick do
        push_loop. Necessário para exportar .tcx / .xlsx depois do treino."""
        with self._lock:
            if self.workout_start_ts is None:
                return
            now = time.time()
            self.samples.append({
                "wall_time": now,
                "elapsed_seconds": now - self.workout_start_ts,
                "heart_rate": self.sensors.heart_rate,
                "power": self.sensors.power,
                "cadence": self.sensors.cadence,
                "step_index": self.current_step_idx,
                "step_name": self.steps[self.current_step_idx].label
                    if 0 <= self.current_step_idx < len(self.steps) else None,
            })

    # ---------- leitura para o frontend ----------
    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            total_elapsed = (now - self.workout_start_ts) if self.workout_start_ts else 0.0

            current_step = None
            step_elapsed = 0.0
            step_remaining = None
            if 0 <= self.current_step_idx < len(self.steps):
                current_step = self.steps[self.current_step_idx]
                step_elapsed = now - self.step_start_ts if self.step_start_ts else 0.0
                if current_step.duration_seconds:
                    step_remaining = max(0.0, current_step.duration_seconds - step_elapsed)

            return {
                "heart_rate": self.sensors.heart_rate,
                "power": self.sensors.power,
                "cadence": self.sensors.cadence,
                "total_elapsed_seconds": total_elapsed,
                "finished": self.finished,
                "current_step_index": self.current_step_idx,
                "total_steps": len(self.steps),
                "current_step_name": current_step.label if current_step else None,
                "current_step_intensity": current_step.intensity if current_step else None,
                "current_step_target_type": current_step.target_type if current_step else None,
                "current_step_target_low": current_step.target_low if current_step else None,
                "current_step_target_high": current_step.target_high if current_step else None,
                "step_elapsed_seconds": step_elapsed,
                "step_remaining_seconds": step_remaining,
                "step_duration_seconds": current_step.duration_seconds if current_step else None,
            }
