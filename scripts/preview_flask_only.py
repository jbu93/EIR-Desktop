"""scripts/preview_flask_only.py · §UI-2026-08-02

`app.py` siempre abre una ventana nativa pywebview — imposible en un entorno
sin GUI (CI, un agente, un servidor headless). Este script sirve la MISMA
app Flask real (mismo blueprint, mismos templates, mismo static_desktop/)
sin abrir esa ventana, para poder verificar cambios de UI en un navegador.

No reemplaza `app.py` ni se usa para producción — solo para desarrollo/QA
visual de este template.

    python eir_desktop_v1/scripts/preview_flask_only.py
    # o vía .claude/launch.json → "eir-desktop-preview" (puerto 5175)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from app import _crear_app  # noqa: E402

app = _crear_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5175, debug=False, use_reloader=False)
