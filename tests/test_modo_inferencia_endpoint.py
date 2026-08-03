"""Arnés D097 — POST /api/shell/modo-inferencia (selector BYOK del sidebar).

Se corren con:
    python -m pytest eir_desktop_v1/tests/test_modo_inferencia_endpoint.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1")
sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1\eir_desktop_v1")

import pytest


@pytest.fixture(autouse=True)
def _reset_estado():
    from core_desktop import cliente_opencode
    cliente_opencode._MODO_INFERENCIA_SESION = None
    yield
    cliente_opencode._MODO_INFERENCIA_SESION = None


@pytest.fixture
def cliente_flask():
    from app import _crear_app
    return _crear_app().test_client()


def test_modo_invalido_devuelve_400(cliente_flask):
    r = cliente_flask.post("/api/shell/modo-inferencia", json={"modo": "algo-raro"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "modo_invalido"


def test_elegir_cloud_no_intenta_arrancar_opencode(cliente_flask):
    r = cliente_flask.post("/api/shell/modo-inferencia", json={"modo": "cloud"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["modo"] == "cloud"
    assert data["opencode"]["motivo"] == "no_solicitado"

    from core_desktop import cliente_opencode
    assert cliente_opencode.modo_inferencia_actual() == "cloud"


def test_elegir_byok_reporta_diagnostico_honesto_si_no_esta_instalado(cliente_flask, monkeypatch):
    """Si OpenCode no está instalado/corriendo, el endpoint debe decirlo —
    nunca fingir que arrancó."""
    from core_desktop import opencode_server

    def _iniciar_falso():
        return False  # simula que no pudo arrancar

    monkeypatch.setattr(opencode_server, "iniciar_opencode_server", _iniciar_falso)

    r = cliente_flask.post("/api/shell/modo-inferencia", json={"modo": "byok"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["modo"] == "byok"
    # el diagnóstico viene de get_server_manager().estado(), que nunca miente
    assert "disponible" in data["opencode"]

    from core_desktop import cliente_opencode
    assert cliente_opencode.modo_inferencia_actual() == "byok"
    assert cliente_opencode._switch_activo() is True


# ─── 2026-08-03 · GET /api/shell/modelos (selector del sidebar) ──────────
def test_modelos_devuelve_catalogo_del_tier_de_la_sesion(cliente_flask, monkeypatch):
    from core_desktop import sesion
    monkeypatch.setattr(sesion, "estado", lambda: {"tier": "freemium"})

    r = cliente_flask.get("/api/shell/modelos")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["tier"] == "freemium"
    assert data["paradigma"] == "build"
    assert len(data["modelos"]) > 0
    assert all("modelo" in m and "proveedor" in m for m in data["modelos"])


def test_modelos_respeta_paradigma_de_query(cliente_flask, monkeypatch):
    from core_desktop import sesion
    monkeypatch.setattr(sesion, "estado", lambda: {"tier": "pago"})

    r = cliente_flask.get("/api/shell/modelos?paradigma=plan")
    assert r.status_code == 200
    assert r.get_json()["paradigma"] == "plan"


def test_modelos_sin_sesion_cae_a_freemium(cliente_flask, monkeypatch):
    from core_desktop import sesion
    monkeypatch.setattr(sesion, "estado", lambda: {})

    r = cliente_flask.get("/api/shell/modelos")
    assert r.status_code == 200
    assert r.get_json()["tier"] == "freemium"


# ─── 2026-08-03 · POST /api/shell/modelo (elegir modelo del selector) ────
@pytest.fixture(autouse=True)
def _reset_modelo_elegido():
    from core_desktop import cliente_opencode
    cliente_opencode._MODELO_ELEGIDO_SESION = None
    yield
    cliente_opencode._MODELO_ELEGIDO_SESION = None


def test_elegir_modelo_valido_para_el_tier(cliente_flask, monkeypatch):
    from core_desktop import sesion, cliente_opencode
    monkeypatch.setattr(sesion, "estado", lambda: {"tier": "freemium"})

    catalogo = cliente_flask.get("/api/shell/modelos").get_json()["modelos"]
    modelo_real = catalogo[0]["modelo"]

    r = cliente_flask.post("/api/shell/modelo", json={"modelo": modelo_real})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "modelo": modelo_real}
    assert cliente_opencode.modelo_elegido_actual() == modelo_real


def test_elegir_modelo_invalido_para_el_tier_rechazado(cliente_flask, monkeypatch):
    from core_desktop import sesion
    monkeypatch.setattr(sesion, "estado", lambda: {"tier": "freemium"})

    r = cliente_flask.post("/api/shell/modelo", json={"modelo": "no-existe/inventado"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "modelo_invalido"


def test_elegir_modelo_vacio_vuelve_a_auto(cliente_flask, monkeypatch):
    from core_desktop import sesion, cliente_opencode
    monkeypatch.setattr(sesion, "estado", lambda: {"tier": "freemium"})
    cliente_opencode.establecer_modelo("algo-previo")

    r = cliente_flask.post("/api/shell/modelo", json={"modelo": ""})
    assert r.status_code == 200
    assert r.get_json()["modelo"] == "auto"
    assert cliente_opencode.modelo_elegido_actual() == "auto"
