"""test_app_pywebview.py · Bug reportado: copiar/pegar no funciona en el
desktop. pywebview desactiva la selección de texto por defecto
(`text_select`); `_arrancar_pywebview()` debe pasarlo explícito en
`webview.create_window()`. Sin esto, Ctrl+C en el chat no copia nada.

Se corre con:
    python -m pytest eir_desktop_v1/tests/test_app_pywebview.py -q
"""
from __future__ import annotations

import sys
import time
import types

sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1")
sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1\eir_desktop_v1")

import pytest


class _AppFalso:
    """Flask falso: .run() no abre ningún puerto real."""

    def run(self, **kwargs):
        return None


def test_create_window_habilita_seleccion_de_texto(monkeypatch):
    import app as appmod

    llamadas = {}

    def _create_window(*args, **kwargs):
        llamadas["kwargs"] = kwargs
        return None

    webview_falso = types.SimpleNamespace(
        create_window=_create_window,
        start=lambda *a, **k: None,
    )
    monkeypatch.setitem(sys.modules, "webview", webview_falso)
    monkeypatch.setattr(time, "sleep", lambda *_: None)  # evita el sleep(1.5) real

    appmod._arrancar_pywebview(_AppFalso())

    assert llamadas["kwargs"].get("text_select") is True
