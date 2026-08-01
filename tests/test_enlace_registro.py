"""Arnés · Opción A — enlace de creación de cuenta en el modal de login del desktop.

El desktop solo permite login; un usuario nuevo que descarga la app y abre el
modal no tiene cómo crear su cuenta desde ahí. Esta compuerta afirma la PROPIEDAD
(L9): el modal de sesión incluye un enlace que abre el registro del web
(/atelier) en el NAVEGADOR EXTERNO (target=_blank, pywebview), nunca dentro del
webview local.

Se verifica sobre el HTML real servido por la app del sandbox, no sobre texto
de un archivo (estilo CEN-F5-08).

Se corre con:
    python -m pytest tests/test_enlace_registro.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _html_modal():
    from app import _crear_app
    with _crear_app().test_client() as c:
        html = c.get("/").get_data(as_text=True)
    # Aislar SOLO el bloque del modal de sesión (entre su id y el siguiente bloque).
    inicio = html.index('id="login-modal"')
    fin = html.index("<!-- Visor 3D STL", inicio)
    return html[inicio:fin]


def _enlaces(bloque: str):
    return re.findall(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", bloque, re.S)


def test_modal_de_sesion_guia_al_registro_web_externo():
    """Propiedad: el modal de login contiene un enlace a /atelier del web que se
    abre en navegador externo (target=_blank + rel=noopener)."""
    modal = _html_modal()
    enlaces = _enlaces(modal)
    assert enlaces, "el modal de login no tiene ningún enlace"

    coincide = [h for h, txt in enlaces
                if "atelier" in h and h.startswith("https://eirdr.com")]
    assert coincide, (
        f"ningún enlace del modal apunta al registro web (hrefs: "
        f"{[h for h, _ in enlaces]})"
    )

    # La misma compuerta exige que sea navegación externa, no navegación del webview.
    etiqueta = next(txt.strip() for h, txt in enlaces if h in coincide)
    assert re.search(r"crea\s+una|crear\s+cuenta", etiqueta, re.I), (
        f"el enlace no invita a crear cuenta (texto: {etiqueta!r})"
    )


def test_enlace_de_registro_usa_navegacion_externa():
    """Propiedad: target=_blank + rel=noopener (pywebview abre el navegador del
    sistema; sin esto el enlace navegaría dentro del webview y rompería la app)."""
    modal = _html_modal()
    targets = [h for h, _t in _enlaces(modal) if "atelier" in h]
    assert targets, "no hay enlace al registro web sobre el que exigir navegación externa"
    for _h in targets:
        tag = re.search(r"<a\b[^>]*href=\"" + re.escape(_h) + r"\"[^>]*>", modal).group(0)
        assert 'target="_blank"' in tag, "el enlace al web NO abre en navegador externo"
        assert 'rel="noopener"' in tag, "falta rel=noopener (seguridad del webview)"
