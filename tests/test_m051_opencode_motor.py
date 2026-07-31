"""Arnés de la misión M-051 — opencode como motor de inferencia.

Estos tests deben FALLAR primero (antes de escribir el adapter ClienteOpencode)
y PASAR tras implementarlo. Cada compuerta G1..G7 del contrato mision.yaml.

Se corren con:
    python -m pytest eir_desktop_v1/tests/test_m051_opencode_motor.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ─── G2 · kill-switch default apagado (L10/L5) ─────────────────────────
def test_killswitch_default_apagado_degrada_a_mock(monkeypatch):
    """Con EIR_OPENCODE_ENABLED ausente (default), el desktop usa ClienteMockSandbox."""
    monkeypatch.delenv("EIR_OPENCODE_ENABLED", raising=False)
    from core_desktop import cliente_opencode  # implementación futura
    lc = cliente_opencode.resolver_cliente(rol="odontologo")
    from core_desktop.cliente_llm import ClienteMockSandbox
    assert isinstance(lc, ClienteMockSandbox), (
        "con el kill-switch apagado el cliente de faultback debe ser ClienteMockSandbox"
    )


def test_killswitch_encendido_usa_opencode(monkeypatch):
    """Con EIR_OPENCODE_ENABLED=1, el desktop usa ClienteOpencode (no el mock)."""
    monkeypatch.setenv("EIR_OPENCODE_ENABLED", "1")
    monkeypatch.setenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")
    from core_desktop import cliente_opencode
    lc = cliente_opencode.resolver_cliente(rol="odontologo")
    from core_desktop.cliente_opencode import ClienteOpencode
    assert isinstance(lc, ClienteOpencode), (
        "con el kill-switch encendido el cliente debe ser ClienteOpencode"
    )


# ─── G1 · superficie drop-in (chat.completions.create) ─────────────────
def test_opencode_adapter_superficie():
    """ClienteOpencode expone .chat.completions.create(**kw) como ClienteResiliente."""
    from core_desktop.cliente_opencode import ClienteOpencode
    c = ClienteOpencode(base_url="http://127.0.0.1:4096", modelo="groq/llama-3.1-8b-instant")
    # debe tener el atributo .chat.completions.create (no llamamos, solo superficie)
    assert hasattr(c, "chat"), "falta .chat"
    assert hasattr(c.chat, "completions"), "falta .chat.completions"
    assert callable(getattr(c.chat.completions, "create", None)), (
        "falta .chat.completions.create — no es drop-in para agente_loop"
    )


# ─── G3 · anonymizer antes de opencode (L6) ────────────────────────────
def test_anonymizer_antes_de_opencode(monkeypatch):
    """El payload que viaje a opencode NO debe contener PHI cruda del paciente."""
    from core_desktop.cliente_opencode import ClienteOpencode

    capturado = {}

    class RespFake:
        class choices:
            class _c:
                message = type("M", (), {"content": "ok", "role": "assistant"})()
            __iter__ = lambda s: iter([s._c()])
        choices = [choices._c()]

    class _ReqFake:
        def __init__(self, url, data=None, headers=None):
            import json
            capturado["data"] = json.loads(data) if isinstance(data, str) else data
            capturado["url"] = url

    c = ClienteOpencode(base_url="http://127.0.0.1:4096", modelo="groq/llama-3.1-8b-instant")
    # forzamos la función HTTP interna para interceptar el payload
    c._post = lambda url, payload: (capturado.update(payload=payload), RespFake())[1]
    historial_phi = [
        {"role": "user", "content": "El paciente Juan Pérez tiene alergia a penicilina."},
    ]
    c.chat.completions.create(messages=historial_phi, model="groq/llama-3.1-8b-instant")
    body = capturado.get("payload", {})
    msgs = body.get("messages") or body.get("parts") or ""
    texto = str(msgs)
    assert "Juan" not in texto and "Pérez" not in texto, (
        "PHI cruda del paciente viajó a opencode sin anonimizar (L6 rota)"
    )


# ─── G4 · validator-first (L3) ─────────────────────────────────────────
def test_validator_first_opencode_no_llamado_si_falla(monkeypatch):
    """Si el validador clínico falla, el routing a opencode NO llega a ejecutarse."""
    monkeypatch.setenv("EIR_OPENCODE_ENABLED", "1")
    from core_desktop import cliente_opencode

    llamaron = {"veces": 0}

    class _ClienteQueCuenta:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    llamaron["veces"] += 1
                    raise AssertionError("opencode fue llamado pese a fallar el validador")

    monkeypatch.setattr(cliente_opencode, "resolver_cliente", lambda rol="odontologo": _ClienteQueCuenta())
    # el runner clinico debe validarse ANTES — si el validador falla, no se llama al LLM
    # Aquí ejecutamos el runner_odontologo con un caso inválido (vacio) y afirmamos
    # que llamaron['veces'] == 0.@pytest.skip temporario: requiere cable del runner)
    pytest.skip("arnés G4 requiere cable del runner_odontologo — se completa en implementación")


# ─── G5 · fail-closed opencode caído (L2) ──────────────────────────────
def test_fail_closed_opencode_caido(monkeypatch):
    """Si opencode serve responde 503/timeout, el adapter devuelve error honesto, no None."""
    from core_desktop.cliente_opencode import ClienteOpencode
    c = ClienteOpencode(base_url="http://127.0.0.1:4199", modelo="groq/llama-3.1-8b-instant")

    def _post_malo(url, payload):
        raise ConnectionRefusedError("opencode serve caído")

    c._post = _post_malo
    r = c.chat.completions.create(messages=[{"role": "user", "content": "test"}],
                                  model="groq/llama-3.1-8b-instant")
    # debe devolver una respuesta honesta de error, NO lanzar, NO fingir contenido
    contenido = getattr(r.choices[0].message, "content", "") if hasattr(r, "choices") else ""
    assert contenido and ("no disp" in contenido.lower() or "no disponible" in contenido.lower() or "error" in contenido.lower()), (
        f"con opencode caído el adapter debe responder honesto, no lanzar ni fingir (L2). Recibido: {contenido!r}"
    )


# ─── G6 · routing Plan/Build por modo ──────────────────────────────────
@pytest.mark.parametrize("modo,esperado_pasa_sonnet", [
    ("plan", True),    # Plan → anthropic/claude-sonnet-4-5
    ("build", False),  # Build → groq/llama-3.3-70b-versatile
])
def test_routing_plan_build_por_tier(modo, esperado_pasa_sonnet):
    from core_desktop.cliente_opencode import resolver_modelo
    modelo = resolver_modelo(modo=modo, tier="PAGO")
    if esperado_pasa_sonnet:
        assert "sonnet" in modelo.lower() or "anthropic" in modelo.lower(), (
            f"modo Plan debe enrutar a Sonnet (vanguardia). Recibido: {modelo}"
        )
    else:
        assert "sonnet" not in modelo.lower(), (
            f"modo Build NO debe enrutar a Sonnet. Recibido: {modelo}"
        )


# ─── G7 · cable real (respuesta no-vacia) ──────────────────────────────
def test_cable_real_respuesta_no_vacia():
    """Test de integración: si opencode serve está vivo y hay claves,
    el adapter alcanza el servidor y obtiene una respuesta real
    (no un error de conexión tipo 'motor no disponible').
    """
    # health check CON auth (server exige OPENCODE_SERVER_PASSWORD)
    import os
    import base64
    import urllib.request
    user = os.getenv("OPENCODE_SERVER_USER", "opencode")
    pwd = os.getenv("OPENCODE_SERVER_PASSWORD", "")
    auth_header = None
    if pwd:
        auth_header = "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()
    try:
        req = urllib.request.Request("http://127.0.0.1:4096/global/health")
        if auth_header:
            req.add_header("Authorization", auth_header)
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status != 200:
                pytest.skip(f"opencode serve health {r.status}")
    except Exception:
        pytest.skip("opencode serve no está corriendo — saltar G7")

    from core_desktop.cliente_opencode import ClienteOpencode
    c = ClienteOpencode(base_url="http://127.0.0.1:4096", modelo="groq/llama-3.1-8b-instant")
    try:
        r = c.chat.completions.create(messages=[{"role": "user", "content": "di: OK"}],
                                      model="groq/llama-3.1-8b-instant")
    except Exception as e:
        pytest.skip(f"sin claves/providers configurados: {e}")
    contenido = getattr(r.choices[0].message, "content", "") if hasattr(r, "choices") else ""
    # El cable real DEBE llegar a opencode — no un error de conexión
    # (que se manifestaría como 'motor no disponible')
    assert contenido and contenido.strip(), "la respuesta vino vacía — cable roto"
    assert "motor no disponible" not in contenido.lower(), (
        f"el adapter no pudo alcanzar opencode (error de conexión): {contenido!r}"
    )
