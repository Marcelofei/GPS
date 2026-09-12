"""
Servidor principal. Uso:

    python server.py caminho/do/treino.fit

Abre http://localhost:5000 — abrir essa URL no navegador do monitor
conectado ao PC do rolo, em fullscreen (F11).

Fluxo:
  1. Carrega as etapas do .fit planejado.
  2. Abre a escuta BLE (FC, potência, cadência) num thread separado.
  3. Uma thread de tick avança etapas e grava amostras a cada 1s.
  4. O frontend faz polling em GET /snapshot a cada 1s (sem WebSocket —
     mais leve e mais simples de depurar que Flask-SocketIO/eventlet).
"""
import sys
import time
import threading
import logging
import tempfile
import os
import json

from flask import Flask, jsonify, render_template, send_file

from workout_parser import parse_workout_fit
from state import WorkoutState
from ble_sensor_listener import BleSensorListener
from tcx_export import export_tcx_to_file
from xlsx_export import export_xlsx
from snapshot_persistence import load_snapshot, clear_snapshot

# Rodando via pythonw.exe (sem console), sys.stdout/stderr podem vir como
# None. Em vez de tentar adivinhar se isso quebra alguma coisa silenciosa
# no meio do caminho, redireciona pra um arquivo de log sempre que isso
# acontecer — assim, se o servidor morrer antes de abrir a porta, sobra
# uma evidência real em vez de mais uma teoria.
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.log")
if sys.stdout is None or sys.stderr is None:
    _log_file = open(_LOG_PATH, "a", buffering=1, encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Pasta persistente pra exports automáticos (no /shutdown) — NÃO usa
# tempfile.gettempdir() aqui de propósito: o Windows limpa %TEMP%
# periodicamente, e um export automático de segurança precisa sobreviver
# além da sessão atual pro usuário conseguir recuperar depois.
EXPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")

# populado em main() antes do app.run — as rotas abaixo dependem
# desse global já estar setado quando uma requisição chegar.
state: WorkoutState | None = None
listener: BleSensorListener | None = None


def tick_loop(state: WorkoutState):
    SNAPSHOT_EVERY_N_TICKS = 10  # ~10s, já que cada tick é 1s (sugestão, ajustável)
    tick_count = 0
    while True:
        state.advance_step_if_needed()
        state.record_sample()
        tick_count += 1
        if tick_count % SNAPSHOT_EVERY_N_TICKS == 0:
            state.save_incremental_snapshot()
        time.sleep(1)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/snapshot", methods=["GET"])
def snapshot():
    return jsonify(state.snapshot())


@app.route("/next_step", methods=["POST"])
def next_step():
    """Endpoint para avançar manualmente etapas 'open' (sem duração fixa)."""
    state.next_step_manual()
    return {"ok": True}


@app.route("/start", methods=["POST"])
def start():
    state.start_workout()
    return {"ok": True}


@app.route("/pause", methods=["POST"])
def pause():
    state.pause()
    return {"ok": True}


@app.route("/resume", methods=["POST"])
def resume():
    state.resume()
    return {"ok": True}


@app.route("/stop", methods=["POST"])
def stop():
    """Encerra o treino direto pela UI — não precisa mais ir no terminal
    apertar Ctrl+C. O processo do servidor continua rodando (pra ainda dar
    pra exportar .tcx/.xlsx depois), só o treino em si é marcado como
    finalizado."""
    state.stop()
    state.save_incremental_snapshot()  # checkpoint final imediato, não espera o próximo tick de 10s
    return {"ok": True}


@app.route("/reconnect", methods=["POST"])
def reconnect():
    """Pede pro listener BLE tentar reconectar agora, sem esperar o timer
    normal de retry — usado pelo botão 'Reconectar' que aparece quando um
    sensor cai durante o treino."""
    if listener is not None:
        listener.request_reconnect()
    return {"ok": True}


@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Chamado via navigator.sendBeacon quando o navegador fecha/recarrega
    a aba do dashboard (evento 'pagehide') — mata o próprio processo,
    sem precisar rodar o PararDashboard.vbs manualmente toda vez.

    LIMITAÇÃO CONHECIDA: o navegador dispara o mesmo evento 'pagehide'
    tanto ao FECHAR a aba quanto ao dar F5/recarregar — não tem como
    diferenciar os dois de forma confiável só com APIs do navegador. Um F5
    acidental também mata o servidor. Aceito como trade-off consciente a
    pedido do usuário; se isso incomodar no uso real, o ajuste é remover
    esta rota e voltar a usar o PararDashboard.vbs manual.

    AUTO-EXPORT DE SEGURANÇA: antes de morrer, se existirem amostras não
    exportadas, salva automaticamente .tcx + .xlsx numa pasta persistente
    (EXPORTS_DIR, não %TEMP%) — isso cobre o incidente real que motivou
    essa mudança (usuário trocou de aba sem clicar em exportar, perdendo
    o treino). Não depende de nenhuma confirmação do navegador — acontece
    aqui no servidor, de forma síncrona, antes do os._exit.

    Não mata na hora (senão a resposta HTTP nem chegaria a sair) — agenda
    a morte do processo pra daqui a 0.3s, tempo suficiente pro
    sendBeacon/resposta saírem antes do processo sumir.
    """
    try:
        if state is not None and state.samples and not state.exported:
            os.makedirs(EXPORTS_DIR, exist_ok=True)
            ts = int(time.time())
            try:
                export_tcx_to_file(state.samples, os.path.join(EXPORTS_DIR, f"auto_export_{ts}.tcx"))
                export_xlsx(state.samples, os.path.join(EXPORTS_DIR, f"auto_export_{ts}.xlsx"))
                state.mark_exported()
                clear_snapshot()
                logging.info("Auto-export de segurança salvo em %s (auto_export_%d.*)", EXPORTS_DIR, ts)
            except Exception:
                logging.exception("Falha no auto-export de segurança do /shutdown — dado pode ter ficado só no snapshot incremental")
    except Exception:
        logging.exception("Erro inesperado no /shutdown antes do auto-export")

    try:
        pid_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.pid")
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except OSError:
        pass
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.route("/export/tcx", methods=["GET"])
def export_tcx_route():
    # tempfile.gettempdir() em vez de "/tmp" fixo: "/tmp" só existe por
    # padrão em Linux/Mac. No Windows não existe, e a gravação falhava
    # com um erro não tratado (500 genérico), fora do "except ValueError"
    # que só cobre a falta de amostras.
    out_path = os.path.join(tempfile.gettempdir(), "treino_export.tcx")
    try:
        export_tcx_to_file(state.samples, out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        logging.exception("Falha inesperada ao exportar .tcx")
        return {"error": f"Falha ao gerar .tcx: {e}"}, 500
    state.mark_exported()
    clear_snapshot()  # já foi exportado manualmente, não é mais uma sessão órfã
    return send_file(out_path, as_attachment=True, download_name="treino.tcx")


@app.route("/export/xlsx", methods=["GET"])
def export_xlsx_route():
    out_path = os.path.join(tempfile.gettempdir(), "treino_export.xlsx")
    try:
        export_xlsx(state.samples, out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        logging.exception("Falha inesperada ao exportar .xlsx")
        return {"error": f"Falha ao gerar .xlsx: {e}"}, 500
    state.mark_exported()
    clear_snapshot()
    return send_file(out_path, as_attachment=True, download_name="treino.xlsx")


@app.route("/export/samples_json", methods=["GET"])
def export_samples_json_route():
    """Exporta as amostras brutas (com wall_time absoluto) em JSON — não
    é pra abrir no Garmin Connect nem no TrainingPeaks, é matéria-prima
    pro script merge_watch_hr.py mesclar com a FC gravada pelo relógio
    depois do treino (quando o dashboard não conseguiu capturar a FC ao
    vivo, mas o relógio gravou a atividade dele sozinho)."""
    if not state.samples:
        return {"error": "Nenhuma amostra gravada — treino não foi iniciado ou terminou sem dados."}, 400
    out_path = os.path.join(tempfile.gettempdir(), "treino_samples.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state.samples, f)
    return send_file(out_path, as_attachment=True, download_name="treino_samples.json")


@app.route("/recover", methods=["GET"])
def recover_check():
    """Checa se existe um snapshot órfão em disco de uma sessão anterior
    que morreu sem exportar (kill forçado, queda de energia, etc). O
    frontend só chama isso enquanto a sessão atual ainda não começou
    (started=false) — uma vez iniciado um treino novo, não faz sentido
    misturar com dado de sessão antiga."""
    snap = load_snapshot()
    if snap is None:
        return {"has_orphan": False}

    samples = snap.get("samples", [])
    return {
        "has_orphan": True,
        "sample_count": len(samples),
        "session_status": snap.get("session_status"),
        "workout_start_ts": snap.get("workout_start_ts"),
    }


@app.route("/recover/export/tcx", methods=["GET"])
def recover_export_tcx():
    snap = load_snapshot()
    if snap is None:
        return {"error": "Nenhuma sessão órfã encontrada."}, 400
    out_path = os.path.join(tempfile.gettempdir(), "treino_recuperado.tcx")
    try:
        export_tcx_to_file(snap.get("samples", []), out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        logging.exception("Falha ao exportar .tcx da sessão recuperada")
        return {"error": f"Falha ao gerar .tcx: {e}"}, 500
    clear_snapshot()
    return send_file(out_path, as_attachment=True, download_name="treino_recuperado.tcx")


@app.route("/recover/export/xlsx", methods=["GET"])
def recover_export_xlsx():
    snap = load_snapshot()
    if snap is None:
        return {"error": "Nenhuma sessão órfã encontrada."}, 400
    out_path = os.path.join(tempfile.gettempdir(), "treino_recuperado.xlsx")
    try:
        export_xlsx(snap.get("samples", []), out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        logging.exception("Falha ao exportar .xlsx da sessão recuperada")
        return {"error": f"Falha ao gerar .xlsx: {e}"}, 500
    clear_snapshot()
    return send_file(out_path, as_attachment=True, download_name="treino_recuperado.xlsx")


@app.route("/recover/dismiss", methods=["POST"])
def recover_dismiss():
    """Descarta a sessão órfã sem exportar — decisão explícita do usuário,
    não forçamos exportação obrigatória."""
    clear_snapshot()
    return {"ok": True}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python server.py caminho/do/treino.fit")
        sys.exit(1)

    # Checa se a porta já está ocupada ANTES de gravar dashboard.pid — se
    # checássemos depois, um processo novo que falha ao dar bind (porta já
    # em uso por um servidor zumbi anterior) sobrescreveria o PID do
    # processo velho no dashboard.pid, deixando esse zumbi órfão e sem
    # jeito do PararDashboard.vbs conseguir matá-lo depois. Isso já
    # aconteceu na prática — servidor travado num treino "finished" há
    # muito tempo, respondendo em localhost:5000 pra sempre, enquanto cada
    # tentativa nova de iniciar morria em silêncio por trás.
    import socket
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        _probe.bind(("0.0.0.0", 5000))
    except OSError:
        msg = (
            "ERRO: a porta 5000 já está em uso por outro processo — "
            "provavelmente um servidor anterior que não foi encerrado "
            "corretamente. Abra o Gerenciador de Tarefas, mate qualquer "
            "python.exe/pythonw.exe, e tente de novo. (Ou rode no cmd: "
            "netstat -ano | findstr :5000  — o número no final da linha "
            "LISTENING é o PID a matar com taskkill /F /PID <numero>)"
        )
        print(msg)
        sys.exit(1)
    finally:
        _probe.close()

    # Grava o próprio PID assim que inicia. Usado pelo PararDashboard.vbs
    # pra encerrar exatamente este processo quando rodando escondido via
    # pythonw (sem janela de console pra apertar Ctrl+C). Só chega aqui se
    # a porta estava realmente livre.
    pid_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.pid")
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))

    fit_path = sys.argv[1]
    steps = parse_workout_fit(fit_path)
    print(f"{len(steps)} etapas carregadas de {fit_path}")

    state = WorkoutState(steps=steps)  # atribui ao global do módulo

    # dicas de nome opcionais pra facilitar o scan (ex: "polar", "wahoo",
    # "kickr", "garmin") — deixe "" pra pegar o primeiro que anunciar o
    # serviço certo. Ajuste conforme seus dispositivos reais.
    listener = BleSensorListener(state, trainer_name_hint="")
    listener.start()

    tick_thread = threading.Thread(target=tick_loop, args=(state,), daemon=True)
    tick_thread.start()

    # NÃO inicia o cronômetro automaticamente — fica esperando o usuário
    # clicar "Iniciar Treino" na tela, depois de conferir que os sensores
    # conectaram. Dispara via POST /start (rota já existente).

    app.run(host="0.0.0.0", port=5000, threaded=True)
