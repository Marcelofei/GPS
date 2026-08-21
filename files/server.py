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

from flask import Flask, jsonify, render_template, send_file

from workout_parser import parse_workout_fit
from state import WorkoutState
from ble_sensor_listener import BleSensorListener
from tcx_export import export_tcx_to_file
from xlsx_export import export_xlsx

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# populado em main() antes do app.run — as rotas abaixo dependem
# desse global já estar setado quando uma requisição chegar.
state: WorkoutState | None = None


def tick_loop(state: WorkoutState):
    while True:
        state.advance_step_if_needed()
        state.record_sample()
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


@app.route("/export/tcx", methods=["GET"])
def export_tcx_route():
    out_path = "/tmp/treino_export.tcx"
    try:
        export_tcx_to_file(state.samples, out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    return send_file(out_path, as_attachment=True, download_name="treino.tcx")


@app.route("/export/xlsx", methods=["GET"])
def export_xlsx_route():
    out_path = "/tmp/treino_export.xlsx"
    try:
        export_xlsx(state.samples, out_path)
    except ValueError as e:
        return {"error": str(e)}, 400
    return send_file(out_path, as_attachment=True, download_name="treino.xlsx")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python server.py caminho/do/treino.fit")
        sys.exit(1)

    fit_path = sys.argv[1]
    steps = parse_workout_fit(fit_path)
    print(f"{len(steps)} etapas carregadas de {fit_path}")

    state = WorkoutState(steps=steps)  # atribui ao global do módulo

    # dicas de nome opcionais pra facilitar o scan (ex: "polar", "wahoo",
    # "kickr", "garmin") — deixe "" pra pegar o primeiro que anunciar o
    # serviço certo. Ajuste conforme seus dispositivos reais.
    listener = BleSensorListener(state, hr_name_hint="", trainer_name_hint="")
    listener.start()

    tick_thread = threading.Thread(target=tick_loop, args=(state,), daemon=True)
    tick_thread.start()

    # inicia o cronômetro imediatamente; troque por chamada via /start
    # se quiser apertar um botão na tela antes de começar a pedalar.
    state.start_workout()

    app.run(host="0.0.0.0", port=5000, threaded=True)
