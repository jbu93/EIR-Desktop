"""cliente_llm.py · Mock LLM 100% offline para el sandbox.

Decision del Soberano (charla con Gemini, 2026-07-30): inferencia CERRADA
estilo Anthropic Desktop. El cliente de escritorio NO ve claves de Groq,
NVIDIA ni Google. La inferencia ocurre en ``eirdr.com/api/v1/inference``
(Backend de Orquestacion Agentica SaaS, no implementado todavia).

El sandbox NO llama a eirdr.com (mock 100% offline). Este modulo:

  1. documenta el CONTRATO JSON que el backend cloud devolvera cuando exista
  2. devuelve respuestas deterministas prefijadas para que los 4 runners
     puedan encadenar ``core.agente_loop`` sin consumir creditos reales
  3. deja lista la transicion: en Fase 1 real, sustituir ``ClienteMockSandbox``
     por un HTTP client a ``eirdr.com/api/v1/inference`` con el mismo contract

NOTA: NO usa la cadena resiliente Groq->NVIDIA->Google del producto propietario.
El doc 02-arquitectura sugiere reusar esa cadena, pero la decision posterior del
Soberano fue CERRAR la inferencia al backend cloud de EIR; por tanto el
cliente local ya no replica la cadena, sino que habla contra el gateway EIR
(siempre). El doc 02 queda supersedeado por el doc 09 en este punto.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


# ── CONTRATO JSON esperado de eirdr.com/api/v1/inference (Fase futura) ────────
# Lo dejamos aqui, en el modulo, como fuente unica. docs/09 lo referenciara.
CONTRATO_INFERENCE = {
    "endpoint": "https://eirdr.com/api/v1/inference",
    "metodo": "POST",
    "auth": {"Bearer": "JWT de sesion EIR DR. (no API key del proveedor LLM)"},
    "request": {
        "rol": "odontologo|recepcion|laboratorio|marketing",
        "mensaje": "string",
        "historial": ["lista de mensajes previos"],
        "tools_locales_disponibles": ["leer_stl_local", "listar_archivos_paciente", "..."],
        "sesion_id": "uuid",
    },
    "response": {
        "tool_call": {
            "name": "string",
            "arguments": {"object": "any"},
        },
        "final_text": "string | null  (cuando el rol termino sin llamar tools)",
        "traza_pasos": [
            {"n": 1, "herramienta": "...", "ok": True, "resumen": "..."},
        ],
        "credito_restante_hoy": 14,
        "tier": "FREEMIUM|PAGO",
        "version_prompt": "2026.07.30-odontologo",
    },
    "errores": {
        401: "sesion expirada - relogar",
        402: "tier freemium agoto cupo diario - upsell a Pro",
        429: "rate-limit - reintentar en N segundos",
        503: "backend cloud caido - fallback a tools locales",
    },
}


class _MockChoice:
    """Mima ``chat.completions.create(...).choices[0].message``."""

    def __init__(self, mensaje: dict):
        # el dict llega con claves 'content' y 'tool_calls'
        self.message = _MockMessage(
            content=mensaje.get("content"),
            tool_calls=mensaje.get("tool_calls"),
        )


@dataclass
class _MockFunction:
    name: str
    arguments: str = "{}"


class _MockToolCall:
    def __init__(self, name: str, args: dict):
        self.function = _MockFunction(name, json.dumps(args, ensure_ascii=False))


@dataclass
class _MockMessage:
    """Mima ``Message`` del SDK OpenAI/Groq."""
    content: str | None = None
    tool_calls: list[Any] | None = None


class _MockResponse:
    def __init__(self, mensaje: dict):
        self.choices = [_MockChoice(mensaje)]


class _Completions:
    """Mima ``client.chat.completions``."""

    def __init__(self, rol: str):
        self.rol = rol
        self._contador = 0

    def create(self, *, model: str, messages: list, tools: list,
               tool_choice: str = "auto", temperature: float = 0.3,
               max_tokens: int = 512, **_):
        self._contador += 1
        mensaje = _PLANILLA_RESPUESTA.get(self.rol, {}).get(self._contador, {})
        if not mensaje:
            # 3a llamada: el rol "termina" sin pedir herramientas -> texto final
            return _MockResponse({"content": _SALIDA_FINAL.get(self.rol, "[rol desconocido]"),
                                   "tool_calls": None})
        return _MockResponse(mensaje)


class _Chat:
    def __init__(self, rol: str):
        self.completions = _Completions(rol)


class ClienteMockSandbox:
    """Cliente LLM 100% offline para el sandbox.

    Mima la superficie del cliente OpenAI/Groq usada por ``agente_loop._pedir_siguiente_herramienta``:
        ``cliente.chat.completions.create(...)``
    para que el loop no distinga entre mock y proveedor real sin tocar el loop.

    En Fase 1 real, sustituir esta clase por un HTTP client al gateway EIR
    (``eirdr.com/api/v1/inference``). El contract devuelto debe mima el de
    ``CONTRATO_INFERENCE`` arriba.
    """

    def __init__(self, rol: str = "odontologo"):
        if rol not in ("odontologo", "recepcion", "laboratorio", "marketing"):
            raise ValueError(f"rol desconocido: {rol!r}")
        self.rol = rol
        self.chat = _Chat(rol)

    def __repr__(self) -> str:
        return f"ClienteMockSandbox(rol={self.rol!r}, offline=True)"


# ── Planilla determinista de respuestas por rol y paso ───────────────────────
# Cada entrada mima un tool_call real devuelto por un LLM de backend cloud.
# Restrictivo: solo tools ya registradas en data/autonomia_zonas.json
# (ver本书-tools zona=lectura, fail-closed en el resto).

_PLANILLA_RESPUESTA = {
    "odontologo": {
        1: {"content": None, "tool_calls": [
            _MockToolCall("ver_protocolo", {"especialidad": "odontologia_general"})
        ]},
    },
    "recepcion": {
        1: {"content": None, "tool_calls": [
            _MockToolCall("buscar_doctor", {"especialidad": "endodoncia"})
        ]},
    },
    "laboratorio": {
        1: {"content": None, "tool_calls": [
            _MockToolCall("info_cad_protesis", {"tooth": "11"})
        ]},
    },
    "marketing": {
        1: {"content": None, "tool_calls": [
            # Ojo bug cazado por el sandbox: el JSON usa 'pulpo_ecosistema'
            # (typo) y autonomia lo deniega por 'no_declarada'. Usamos el
            # nombre REAL del modulo: pulso_ecosistema. Ver docs/09.
            _MockToolCall("pulso_ecosistema", {"tipo": "social_media"})
        ]},
    },
}

_SALIDA_FINAL = {
    "odontologo":  "Plan de tratamiento armado. Recomiendo validar consentimiento informado.",
    "recepcion":   "Disponibilidad del Dr. Lopez confirmada para manana 9am. Agenda bloqueada.",
    "laboratorio": "Pedido CAD enviado al cliente. Se estiman 5 dias habiles.",
    "marketing":   "Contenido cronograma 14 dias listo. Recordatorios programados.",
}


def contrato_inference() -> dict:
    """Devuelve el contrato JSON documentado del gateway cloud (future)."""
    return json.loads(json.dumps(CONTRATO_INFERENCE, ensure_ascii=False))


def main() -> int:
    """Auto-test del mock (corrible directamente)."""
    c = ClienteMockSandbox("odontologo")
    r1 = c.chat.completions.create(model="mock", messages=[], tools=[])
    tc = r1.choices[0].message.tool_calls[0] if r1.choices[0].message.tool_calls else None
    print(f"mock ok: primera tool-call = {tc.function.name if tc else None}")
    r2 = c.chat.completions.create(model="mock", messages=[], tools=[])
    print(f"mock ok: segunda llamada = {r2.choices[0].message.content!r}")
    print(f"mock ok: tool_call agotado tras 2 vueltas -> texto final")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
