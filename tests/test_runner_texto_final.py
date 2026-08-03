"""Arnés D097 — el runner ya no expone el resumen técnico crudo cuando el LLM
responde directo (sin herramientas).

Verifica la integración completa (runner -> agente_loop -> cable_local) con un
cliente fake que controla si el LLM pide o no una tool call, cubriendo:
  1. El LLM responde directo (sin tool_calls) -> el resumen es SU texto real,
     no "No se ejecutó ninguna herramienta." (el bug de D097).
  2. El LLM pide una tool que falla, y en el siguiente turno responde directo
     -> el resumen narra el paso fallido (comportamiento histórico, intacto).

Se corren con:
    python -m pytest eir_desktop_v1/tests/test_runner_texto_final.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1")
sys.path.insert(0, r"C:\PROYECTOS_DEV\DEV_DR_EIR_V1\eir_desktop_v1")

import pytest


class _Choice:
    def __init__(self, content=None, tool_call=None):
        self.content = content
        self.tool_calls = [tool_call] if tool_call else None
        self.message = self

    def __getattr__(self, item):
        # getattr(choice, "tool_calls", None) en agente_loop lee del message
        raise AttributeError(item)


class _ToolCall:
    def __init__(self, name, args):
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class _ClienteGuionado:
    """Cliente fake sin ``ultimo_texto_final`` (estilo mock/OpenCode). Cada
    llamada consume la siguiente respuesta programada de la lista."""

    def __init__(self, respuestas: list):
        self._respuestas = list(respuestas)
        self.llamadas = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.llamadas += 1
        contenido, tool_call = self._respuestas.pop(0)
        resp = type("Resp", (), {})()
        msg = type("Msg", (), {})()
        msg.content = contenido
        msg.tool_calls = [tool_call] if tool_call else None
        resp.choices = [type("Choice", (), {"message": msg})()]
        return resp


@pytest.fixture(autouse=True)
def _matriz_lectura(monkeypatch):
    """El runner exige que las tools usadas estén declaradas en la matriz de
    autonomía; se usan las 6 tools de zona=lectura ya existentes del web, sin
    necesidad de tocar nada — este fixture solo evita ruido de red."""
    yield


def _runner_odontologo():
    import importlib
    mod = importlib.import_module("core_desktop.runner_odontologo")
    importlib.reload(mod)
    return mod


class TestRespuestaDirectaSinTools:
    """El caso que D097 arregla: el LLM no pide ninguna tool."""

    def test_saludo_real_no_es_resumen_generico(self, monkeypatch):
        mod = _runner_odontologo()
        # 2 respuestas: la 1a la consume el propio loop (turno 1, sin
        # tool_calls -> corta ahí, r.pasos queda vacío); la 2a es la llamada
        # EXTRA que resolver_texto_final() hace para pedir el texto real.
        saludo = "¡Hola! ¿En qué puedo ayudarte hoy?"
        cliente = _ClienteGuionado([(saludo, None), (saludo, None)])
        monkeypatch.setattr(mod, "resolver_cliente", lambda rol=None, **kw: cliente)

        out = mod.ejecutar_local("hola", historial=[])

        assert out["resumen"] == saludo
        assert out["resumen"] != "No se ejecutó ninguna herramienta."
        # 1 llamada del loop (sin tool_calls -> corta ahí) + 1 llamada extra
        # de resolver_texto_final pidiendo el texto conversacional real.
        assert cliente.llamadas == 2


class TestTrazaConHerramientaSigueIntacta:
    """Comportamiento histórico: si SÍ hubo pasos, se narra la traza — sin
    cambios respecto a antes de D097."""

    def test_tool_call_fallido_se_narra_no_se_re_consulta(self, monkeypatch):
        mod = _runner_odontologo()
        tc = _ToolCall("buscar_evidencia_cientifica", {"consulta": "x"})
        cliente = _ClienteGuionado([
            (None, tc),           # turno 1: pide una tool
            (None, None),         # turno 2: sin tool_calls, sin content -> fin del loop
        ])
        monkeypatch.setattr(mod, "resolver_cliente", lambda rol=None, **kw: cliente)

        out = mod.ejecutar_local("dame evidencia de algo", historial=[])

        assert out["pasos"], "debia haber al menos un paso ejecutado"
        assert out["resumen"] != "No se ejecutó ninguna herramienta."
        # exactamente 2 llamadas del propio loop (turno1 + turno2) — la traza
        # NO está vacía, así que resolver_texto_final no debe agregar una
        # tercera llamada extra.
        assert cliente.llamadas == 2


class TestWizardPreguntaEnRunner:
    """M-063 — si el LLM pide preguntar_aclaracion en fase plan, el runner debe
    exponerla TAL CUAL como resumen visible: sin re-consultar al LLM (eso
    perdería la pregunta) y sin pasar por resolver_texto_final."""

    def test_pregunta_aclaracion_es_el_resumen_visible(self, monkeypatch):
        from core import modo_plan as MP
        mod = _runner_odontologo()
        tc = _ToolCall(MP.PLAN_TOOL_PREGUNTA, {"pregunta": "¿Para qué red social es la campaña?"})
        cliente = _ClienteGuionado([(None, tc)])
        monkeypatch.setattr(mod, "resolver_cliente", lambda rol=None, **kw: cliente)

        out = mod.ejecutar_local("ayúdame con una campaña", historial=[], paradigma="plan")

        assert out["resumen"] == "¿Para qué red social es la campaña?"
        assert out["motivo_fin"] == "pregunta_aclaracion"
        assert cliente.llamadas == 1               # NO hay una 2ª llamada "texto real"
