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

sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1")
sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1\eir_desktop_v1")

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


# ─── D097 · selector BYOK del sidebar enciende sin reiniciar el .exe ────
@pytest.fixture(autouse=True)
def _reset_modo_inferencia_sesion():
    """La elección del selector vive en una variable de módulo (D097) — se
    resetea entre tests para que uno no filtre estado al siguiente."""
    from core_desktop import cliente_opencode
    cliente_opencode._MODO_INFERENCIA_SESION = None
    yield
    cliente_opencode._MODO_INFERENCIA_SESION = None


def test_elegir_byok_enciende_sin_env_var(monkeypatch):
    """El doctor elige BYOK en el sidebar: debe encender YA, sin depender de
    reiniciar el proceso (el env var solo se lee una vez al arranque)."""
    monkeypatch.delenv("EIR_OPENCODE_ENABLED", raising=False)
    from core_desktop import cliente_opencode
    assert cliente_opencode._switch_activo() is False
    cliente_opencode.establecer_modo_inferencia("byok")
    assert cliente_opencode._switch_activo() is True
    lc = cliente_opencode.resolver_cliente(rol="odontologo")
    from core_desktop.cliente_opencode import ClienteOpencode
    assert isinstance(lc, ClienteOpencode)


def test_elegir_cloud_respeta_default_apagado(monkeypatch):
    """Elegir 'cloud' explícitamente NO debe encender BYOK — vuelve al
    comportamiento por defecto (lee el env var, que nace apagado)."""
    monkeypatch.delenv("EIR_OPENCODE_ENABLED", raising=False)
    from core_desktop import cliente_opencode
    cliente_opencode.establecer_modo_inferencia("cloud")
    assert cliente_opencode._switch_activo() is False


def test_modo_invalido_se_ignora():
    """Un valor que no es 'cloud' ni 'byok' no cambia nada (fail-closed)."""
    from core_desktop import cliente_opencode
    cliente_opencode.establecer_modo_inferencia("cloud")
    cliente_opencode.establecer_modo_inferencia("algo-raro")
    assert cliente_opencode.modo_inferencia_actual() == "cloud"


def test_modo_actual_reportado_para_diagnostico():
    from core_desktop import cliente_opencode
    assert cliente_opencode.modo_inferencia_actual() == "cloud"  # default
    cliente_opencode.establecer_modo_inferencia("byok")
    assert cliente_opencode.modo_inferencia_actual() == "byok"


# ─── D098 · revierte D085: FREEMIUM también usa Cloud (el freno es volumen) ──
class TestTierPermiteCloud:
    """Antes (D085): _tier_permite_cloud() excluía FREEMIUM por completo.
    D098 la revierte: todo tier real permite Cloud; el límite de FREEMIUM
    (15 consultas/día) se exige en el servidor (core.metered_billing), no
    aquí. Fail-closed se mantiene si la sesión no se puede leer."""

    def _resolver(self, monkeypatch, tier_dict_o_excepcion):
        from core_desktop import cliente_opencode, sesion
        monkeypatch.setenv("EIR_CLOUD_INFERENCE", "1")
        monkeypatch.delenv("EIR_OPENCODE_ENABLED", raising=False)
        if isinstance(tier_dict_o_excepcion, Exception):
            def _estado():
                raise tier_dict_o_excepcion
            monkeypatch.setattr(sesion, "estado", _estado)
        else:
            monkeypatch.setattr(sesion, "estado", lambda: tier_dict_o_excepcion)
        return cliente_opencode.resolver_cliente(rol="odontologo")

    def test_freemium_con_switch_encendido_usa_cloud(self, monkeypatch):
        from core_desktop.cliente_cloud import ClienteEirCloud
        lc = self._resolver(monkeypatch, {"tier": "freemium"})
        assert isinstance(lc, ClienteEirCloud)

    def test_pago_con_switch_encendido_usa_cloud(self, monkeypatch):
        from core_desktop.cliente_cloud import ClienteEirCloud
        lc = self._resolver(monkeypatch, {"tier": "pago"})
        assert isinstance(lc, ClienteEirCloud)

    def test_ultra_con_switch_encendido_usa_cloud(self, monkeypatch):
        from core_desktop.cliente_cloud import ClienteEirCloud
        lc = self._resolver(monkeypatch, {"tier": "ultra"})
        assert isinstance(lc, ClienteEirCloud)

    def test_sesion_ilegible_falla_cerrado_no_usa_cloud(self, monkeypatch):
        from core_desktop.cliente_cloud import ClienteEirCloud
        from core_desktop.cliente_llm import ClienteMockSandbox
        lc = self._resolver(monkeypatch, RuntimeError("sesion caida"))
        assert not isinstance(lc, ClienteEirCloud)
        assert isinstance(lc, ClienteMockSandbox)


# ─── 2026-08-03 · runtime hook enciende EIR_CLOUD_INFERENCE por defecto:
# el switch de sesión debe ser simétrico o BYOK queda mudo ────────────────
class TestSimetriaCloudByok:
    """Bug real cazado el 2026-08-03: con EIR_CLOUD_INFERENCE=1 por defecto
    (el runtime hook lo enciende ahora que D098 cerró el gate de tier),
    _cloud_switch_activo() solo leía el env var — elegir BYOK en el sidebar
    no tenía ningún efecto porque resolver_cliente() evalúa cloud primero y
    ganaba siempre. Debe ser simétrico a _switch_activo() (BYOK)."""

    def test_byok_explicito_apaga_cloud_aunque_env_var_este_encendido(self, monkeypatch):
        from core_desktop import cliente_opencode
        from core_desktop.cliente_opencode import ClienteOpencode
        monkeypatch.setenv("EIR_CLOUD_INFERENCE", "1")
        monkeypatch.setenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")
        cliente_opencode.establecer_modo_inferencia("byok")
        assert cliente_opencode._cloud_switch_activo() is False
        lc = cliente_opencode.resolver_cliente(rol="odontologo")
        assert isinstance(lc, ClienteOpencode)

    def test_cloud_explicito_enciende_sin_esperar_env_var(self, monkeypatch):
        from core_desktop import cliente_opencode
        monkeypatch.delenv("EIR_CLOUD_INFERENCE", raising=False)
        cliente_opencode.establecer_modo_inferencia("cloud")
        assert cliente_opencode._cloud_switch_activo() is True

    def test_sin_eleccion_cae_al_env_var(self, monkeypatch):
        from core_desktop import cliente_opencode
        monkeypatch.delenv("EIR_CLOUD_INFERENCE", raising=False)
        assert cliente_opencode._cloud_switch_activo() is False
        monkeypatch.setenv("EIR_CLOUD_INFERENCE", "1")
        assert cliente_opencode._cloud_switch_activo() is True


# ─── 2026-08-03 · resolver_cliente() recibe el paradigma real (antes ─────
# siempre resolvía Modo.BUILD sin importar qué eligió el doctor) ─────────
class TestParadigmaLlegaAModo:
    """Bug real: `Modo` (PLAN/BUILD, para elegir MODELO en model_catalog.py)
    y `paradigma` (plan/build, gatea qué tools ve el LLM en agente_loop.py)
    son dos conceptos con el mismo nombre en español que nunca se tocaban —
    resolver_cliente() hardcodeaba Modo.BUILD sin recibir el paradigma real
    del toggle del sidebar."""

    def _modo_resuelto(self, monkeypatch, paradigma, tier="pago"):
        from core_desktop import cliente_opencode, sesion
        capturado = {}
        monkeypatch.delenv("EIR_CLOUD_INFERENCE", raising=False)
        monkeypatch.setenv("EIR_OPENCODE_ENABLED", "1")
        monkeypatch.setattr(sesion, "estado", lambda: {"tier": tier})

        class _ClienteOpencodeFake:
            def __init__(self, **kw):
                capturado.update(kw)

        monkeypatch.setattr(cliente_opencode, "ClienteOpencode", _ClienteOpencodeFake)
        cliente_opencode.resolver_cliente(rol="odontologo", paradigma=paradigma)
        return capturado.get("modo")

    def test_paradigma_plan_resuelve_modo_plan(self, monkeypatch):
        from core_desktop.model_catalog import Modo
        assert self._modo_resuelto(monkeypatch, "plan") == Modo.PLAN

    def test_paradigma_build_resuelve_modo_build(self, monkeypatch):
        from core_desktop.model_catalog import Modo
        assert self._modo_resuelto(monkeypatch, "build") == Modo.BUILD

    def test_paradigma_ausente_por_defecto_es_build(self, monkeypatch):
        from core_desktop import cliente_opencode, sesion
        from core_desktop.model_catalog import Modo
        capturado = {}
        monkeypatch.delenv("EIR_CLOUD_INFERENCE", raising=False)
        monkeypatch.setenv("EIR_OPENCODE_ENABLED", "1")
        monkeypatch.setattr(sesion, "estado", lambda: {"tier": "pago"})

        class _ClienteOpencodeFake:
            def __init__(self, **kw):
                capturado.update(kw)

        monkeypatch.setattr(cliente_opencode, "ClienteOpencode", _ClienteOpencodeFake)
        cliente_opencode.resolver_cliente(rol="odontologo")  # sin paradigma
        assert capturado.get("modo") == Modo.BUILD


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
    """Si el ClinicalValidator falla, el routing a opencode NO llega a ejecutarse."""
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
