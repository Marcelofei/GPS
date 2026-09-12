"""
Estado compartilhado do treino: valores instantâneos dos sensores +
cronômetro total + progressão pela lista de etapas do .fit carregado +
controle de pausa/stop manual pela UI.

Este módulo não sabe nada de BLE nem de Flask — é puro estado e lógica de
tempo, para ser testável isoladamente.
"""
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from workout_parser import WorkoutStep
from snapshot_persistence import save_snapshot


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

        # ---- controle de pausa ----
        # Pausa "congela" os cronômetros (total e da etapa) sem perder o
        # progresso já feito. Implementado como acumulador de tempo pausado:
        # o tempo decorrido real é sempre (agora - início) menos o tempo
        # total em que ficou pausado.
        self.paused: bool = False
        self.pause_start_ts: Optional[float] = None
        self.total_paused_seconds: float = 0.0     # acumulado desde o início do treino
        self.step_paused_seconds: float = 0.0       # acumulado desde o início da etapa atual (zera a cada avanço)

        # ---- médias da etapa atual (FC/potência/cadência), zeram a cada avanço ----
        self._step_hr_sum: float = 0.0
        self._step_hr_count: int = 0
        self._step_power_sum: float = 0.0
        self._step_power_count: int = 0
        self._step_cadence_sum: float = 0.0
        self._step_cadence_count: int = 0

        # ---- status de conexão do rolo BLE (atualizado pelo listener) ----
        # FC removida de propósito — ver docstring do ble_sensor_listener.py.
        self.trainer_connected: bool = False

        # ---- persistência incremental (sobrevive a kill do processo) ----
        # session_status: "not_started" | "running" | "paused" | "finished"
        # exported: True depois de qualquer export bem-sucedido (manual ou
        # automático no /shutdown) — usado pra decidir se um snapshot órfão
        # em disco ainda precisa ser oferecido pra recuperação.
        self.session_status: str = "not_started"
        self.exported: bool = False

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

    # ---------- status de conexão (chamado pelo listener BLE) ----------
    def set_trainer_connected(self, connected: bool):
        with self._lock:
            self.trainer_connected = connected

    # ---------- controle do treino ----------
    def start_workout(self):
        with self._lock:
            now = time.time()
            self.workout_start_ts = now
            self.step_start_ts = now
            self.current_step_idx = 0
            self.finished = False
            self.paused = False
            self.pause_start_ts = None
            self.total_paused_seconds = 0.0
            self.step_paused_seconds = 0.0
            self.session_status = "running"
            self.exported = False

    def pause(self):
        """Congela os cronômetros. Sensores continuam sendo lidos/atualizados
        normalmente (FC/potência/cadência), só o tempo do treino para."""
        with self._lock:
            if self.paused or self.workout_start_ts is None or self.finished:
                return
            self.paused = True
            self.pause_start_ts = time.time()
            self.session_status = "paused"

    def resume(self):
        with self._lock:
            # sem o check de finished, um /resume perdido depois do /stop
            # (ex: clique duplo-rápido em "Pausar" -> "Encerrar" antes do
            # próximo poll esconder os botões) destravava o cronômetro de
            # um treino já encerrado, e "Tempo Total" voltava a correr pra
            # sempre.
            if not self.paused or self.pause_start_ts is None or self.finished:
                return
            now = time.time()
            delta = now - self.pause_start_ts
            self.total_paused_seconds += delta
            self.step_paused_seconds += delta
            self.paused = False
            self.pause_start_ts = None
            self.session_status = "running"

    def stop(self):
        """Encerra o treino manualmente pela UI (equivalente a chegar no
        fim natural das etapas, mas disparado a qualquer momento). Congela
        o cronômetro como um pause implícito, pra 'Tempo Total' não seguir
        correndo depois do encerramento."""
        with self._lock:
            if self.workout_start_ts is None:
                return
            if not self.paused:
                self.paused = True
                self.pause_start_ts = time.time()
            self.finished = True
            self.session_status = "finished"

    def _current_pause_extra(self, now: float) -> float:
        """Duração da pausa em andamento neste exato instante (0 se não pausado)."""
        return (now - self.pause_start_ts) if (self.paused and self.pause_start_ts) else 0.0

    def advance_step_if_needed(self):
        """Verifica se a etapa atual (baseada em tempo) expirou e avança.
        Não avança nada enquanto pausado ou já finalizado. Etapas sem
        duração definida ('open') exigem avanço manual via next_step_manual()."""
        stepped = False
        with self._lock:
            if self.finished or self.paused or self.workout_start_ts is None:
                return
            if self.current_step_idx >= len(self.steps):
                self.finished = True
                self.session_status = "finished"
                return

            step = self.steps[self.current_step_idx]
            if step.duration_seconds:
                now = time.time()
                elapsed_in_step = (now - self.step_start_ts) - self.step_paused_seconds
                if elapsed_in_step >= step.duration_seconds:
                    self._advance_locked()
                    stepped = True
        # snapshot salvo FORA do "with self._lock" — to_snapshot_dict()
        # pega o mesmo lock, e Lock() do Python não é reentrante (chamar
        # de dentro do bloco travado seria deadlock).
        if stepped:
            self.save_incremental_snapshot()

    def next_step_manual(self):
        """Avanço manual de etapa (para steps 'open' ou correção manual).
        Ignorado se pausado, já finalizado, ou se o treino ainda nem foi
        iniciado (workout_start_ts None) — sem essa última checagem, um
        clique acidental antes de apertar 'Iniciar Treino' avançaria a
        etapa sem o cronômetro sequer ter começado."""
        stepped = False
        with self._lock:
            if self.paused or self.finished or self.workout_start_ts is None:
                return
            self._advance_locked()
            stepped = True
        if stepped:
            self.save_incremental_snapshot()

    def _advance_locked(self):
        self.current_step_idx += 1
        self.step_start_ts = time.time()
        self.step_paused_seconds = 0.0
        self._step_hr_sum = 0.0
        self._step_hr_count = 0
        self._step_power_sum = 0.0
        self._step_power_count = 0
        self._step_cadence_sum = 0.0
        self._step_cadence_count = 0
        if self.current_step_idx >= len(self.steps):
            self.finished = True
            self.session_status = "finished"

    # ---------- gravação de série temporal (para exportação posterior) ----------
    def record_sample(self):
        """Registra uma amostra do instante atual — chamado a cada tick do
        push_loop. Não grava nada enquanto pausado ou finalizado, pra não
        poluir o .tcx/.xlsx com um platô de amostras paradas. Também
        acumula as médias da etapa atual (FC/potência/cadência) aqui, no
        mesmo ponto — mesma regra de não contar durante pausa."""
        with self._lock:
            if self.workout_start_ts is None or self.paused or self.finished:
                return
            now = time.time()
            elapsed = (now - self.workout_start_ts) - self.total_paused_seconds

            # médias da etapa atual — só conta leitura > 0 (sensor sem dado
            # ainda manda 0, não queremos que isso puxe a média pra baixo
            # artificialmente logo no início de cada etapa)
            if self.sensors.heart_rate:
                self._step_hr_sum += self.sensors.heart_rate
                self._step_hr_count += 1
            if self.sensors.power:
                self._step_power_sum += self.sensors.power
                self._step_power_count += 1
            if self.sensors.cadence:
                self._step_cadence_sum += self.sensors.cadence
                self._step_cadence_count += 1

            self.samples.append({
                "wall_time": now,
                "elapsed_seconds": elapsed,
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
            active_pause = self._current_pause_extra(now)

            total_elapsed = 0.0
            if self.workout_start_ts:
                total_elapsed = (now - self.workout_start_ts) - self.total_paused_seconds - active_pause

            current_step = None
            step_elapsed = 0.0
            step_remaining = None
            if 0 <= self.current_step_idx < len(self.steps):
                current_step = self.steps[self.current_step_idx]
                if self.step_start_ts:
                    step_elapsed = (now - self.step_start_ts) - self.step_paused_seconds - active_pause
                if current_step.duration_seconds:
                    step_remaining = max(0.0, current_step.duration_seconds - step_elapsed)

            # lista de próximas etapas (a partir da atual), pro painel lateral.
            # Nota: hoje é uma lista PLANA — se o .fit tiver blocos de repetição
            # (ex: "repetir 5x"), cada repetição aparece como entrada separada,
            # sem agrupamento visual tipo TrainingPeaks. Agrupar repeats exige
            # ler uma estrutura adicional do .fit (mensagens de repeat/loop)
            # que o workout_parser.py ainda não extrai — não implementado.
            upcoming = []
            for s in self.steps[self.current_step_idx:]:
                upcoming.append({
                    "index": s.index,
                    "label": s.label,
                    "duration_seconds": s.duration_seconds,
                    "intensity": s.intensity,
                    "target_type": s.target_type,
                    "target_low": s.target_low,
                    "target_high": s.target_high,
                })

            # status do alvo da etapa atual (acima/dentro/abaixo), pra alertar
            # visualmente na tela. Só calcula se a etapa tem alvo definido
            # (target_type relevante + low/high setados) e o sensor
            # correspondente já tem alguma leitura.
            target_status = None
            if current_step and current_step.target_type in ("power", "heart_rate", "cadence"):
                low = current_step.target_low
                high = current_step.target_high
                if low is not None and high is not None:
                    sensor_value = {
                        "power": self.sensors.power,
                        "heart_rate": self.sensors.heart_rate,
                        "cadence": self.sensors.cadence,
                    }[current_step.target_type]
                    if sensor_value:  # 0 = sem leitura ainda, não avalia
                        if sensor_value < low:
                            target_status = "below"
                        elif sensor_value > high:
                            target_status = "above"
                        else:
                            target_status = "in_range"

            step_avg_hr = (self._step_hr_sum / self._step_hr_count) if self._step_hr_count else None
            step_avg_power = (self._step_power_sum / self._step_power_count) if self._step_power_count else None
            step_avg_cadence = (self._step_cadence_sum / self._step_cadence_count) if self._step_cadence_count else None

            return {
                "heart_rate": self.sensors.heart_rate,
                "power": self.sensors.power,
                "cadence": self.sensors.cadence,
                "step_avg_heart_rate": round(step_avg_hr) if step_avg_hr is not None else None,
                "step_avg_power": round(step_avg_power) if step_avg_power is not None else None,
                "step_avg_cadence": round(step_avg_cadence) if step_avg_cadence is not None else None,
                "target_status": target_status,
                "trainer_connected": self.trainer_connected,
                "total_elapsed_seconds": total_elapsed,
                "finished": self.finished,
                "started": self.workout_start_ts is not None,
                "paused": self.paused,
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
                "upcoming_steps": upcoming,
            }

    # ---------- persistência incremental (sobrevive a kill do processo) ----------
    def to_snapshot_dict(self) -> dict:
        """Serializa o estado mínimo necessário pra reconstruir um export
        (.tcx/.xlsx) depois, caso o processo seja morto antes de exportar
        manualmente. NÃO tenta reconstruir a sessão "ao vivo" (retomar
        exatamente de onde parou com sensores reconectados) — isso é
        ambíguo demais (quanto tempo real passou? a etapa ainda faz
        sentido?). Escopo deliberadamente menor: recuperar os DADOS pra
        exportação, não a sessão em progresso."""
        with self._lock:
            return {
                "workout_start_ts": self.workout_start_ts,
                "current_step_idx": self.current_step_idx,
                "finished": self.finished,
                "session_status": self.session_status,
                "exported": self.exported,
                "samples": list(self.samples),  # cópia rasa, já são dicts simples
            }

    def save_incremental_snapshot(self):
        """Grava o snapshot em disco. Chamado a cada etapa concluída e
        periodicamente durante o tick — não precisa do lock externo, já
        pega tudo via to_snapshot_dict() que tem seu próprio lock."""
        if self.workout_start_ts is None:
            return  # nada pra salvar antes do treino começar
        try:
            save_snapshot(self.to_snapshot_dict())
        except OSError:
            pass  # disco cheio/sem permissão não deve derrubar o treino

    def mark_exported(self):
        with self._lock:
            self.exported = True
