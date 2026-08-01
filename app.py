"""app.py · Shell de escritorio del sandbox EIR Desktop v1.

Flask minimo en 127.0.0.1:5175 + ventana nativa pywebview. Aislado del
proyecto principal EIR DR. (no importa el app.py de raiz).

Mitigacion de fail-closed silencioso (riesgo #2 del doc 08):
  al arrancar, loguea ``core.autonomia.resumen()`` si ``core/`` es
  importable. Si ``core/`` no estuviera en PYTHONPATH (sandbox aislado),
  lo declara honestamente: el sandbox sabe que si ``core.autonomia``
  no carga, todas las tools quedarian denegadas en el loop real.

Incluye gestión automática del servidor OpenCode (opencode serve) como
dependencia de inferencia LLM (BYOK).
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static_desktop"

PORT = int(os.getenv("EIR_DESKTOP_PORT", "5175"))
HOST = "127.0.0.1"


def _crear_app():
    """Construye Flask con el blueprint aislado ``bp_desktop``."""
    try:
        from flask import Flask
    except Exception as exc:
        raise RuntimeError(f"Flask no disponible: {exc!r}") from exc

    app = Flask(__name__,
                template_folder=str(TEMPLATES_DIR),
                static_folder=str(STATIC_DIR),
                static_url_path="/static_desktop")

    # Importa bp_desktop: como paquete (python -m routes_desktop) o
    # como script directo (python app.py).
    try:
        from .routes_desktop import bp_desktop
    except ImportError:
        from routes_desktop import bp_desktop
    app.register_blueprint(bp_desktop)

    @app.get("/")
    def _index():
        from flask import render_template
        return render_template("desktop_chat.html")

    @app.get("/health")
    def _health():
        from flask import jsonify
        return jsonify(status="ok", port=PORT, sandbox="eir_desktop_v1")

    return app


def _loguear_autonomia(app) -> None:
    """Mitigacion fail-closed silencioso (doc 08, riesgo #2).

    Si ``core.autonomia`` carga, logueamos ``resumen()``. Si no carga,
    logueamos aviso claro. En ningun caso dejamos al usuario sin pista
    de por que toda tool diria 'no disponible' si fallara la matriz.
    """
    log = logging.getLogger("eir_desktop")
    try:
        from core import autonomia
        r = autonomia.resumen()
        log.warning("[eir_desktop] autonomia resumen: %s", r)
        app.config["EIR_AUTONOMIA_OK"] = True
        app.config["EIR_AUTONOMIA_RESUMEN"] = r
    except Exception as exc:              # noqa: BLE001
        log.warning("[eir_desktop] core.autonomia NO cargado: %r. "
                    "Las tools quedarian fail-closed denegadas en loop real.", exc)
        app.config["EIR_AUTONOMIA_OK"] = False


def _arrancar_opencode_server(app) -> bool:
    """Arranca el servidor OpenCode (opencode serve) y lo registra para apagado."""
    log = logging.getLogger("eir_desktop")

    try:
        from core_desktop.opencode_server import iniciar_opencode_server, detener_opencode_server
    except ImportError as e:
        log.warning("[eir_desktop] No se pudo importar opencode_server: %s. OpenCode no disponible.", e)
        return False

    log.info("Arrancando servidor OpenCode...")
    if not iniciar_opencode_server():
        log.error("No se pudo arrancar el servidor OpenCode. Verifica que 'opencode' esté instalado (npm install -g opencode-ai).")
        return False

    atexit.register(detener_opencode_server)

    import signal
    def _signal_handler(signum, frame):
        log.info("Señal %s recibida, deteniendo OpenCode server...", signum)
        detener_opencode_server()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    log.info("Servidor OpenCode listo y registrado para apagado automático")
    return True


def _arrancar_pywebview(app) -> int:
    """Levanta Flask en hilo daemon + abre ventana pywebview."""
    import webview

    def _servir():
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)

    servidor = threading.Thread(target=_servir, daemon=True)
    servidor.start()
    time.sleep(1.5)  # damn Flask arrancar

    webview.create_window(
        "EIR DR. Desktop - Sandbox",
        f"http://{HOST}:{PORT}/",
        width=960, height=680,
    )
    webview.start()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    log = logging.getLogger("eir_desktop")

    # Permite import como script o como ``python -m eir_desktop_v1.app``:
    if str(HERE.parent) not in sys.path:
        sys.path.insert(0, str(HERE.parent))

    log.info("arrancando sandbox EIR Desktop v1 en %s:%d", HOST, PORT)
    app = _crear_app()
    _loguear_autonomia(app)

    if not _arrancar_opencode_server(app):
        log.warning("Continuando SIN servidor OpenCode (modo offline/mock)")

    log.info("routes: / · /health · /api/shell/conversar · /api/shell/roles · /api/shell/contrato")
    return _arrancar_pywebview(app)


if __name__ == "__main__":
    raise SystemExit(main())
