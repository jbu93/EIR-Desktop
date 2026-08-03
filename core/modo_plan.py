# -*- coding: utf-8 -*-
"""
core/modo_plan.py · M-057 · Paradigma de ejecución plan/build/auto con pesos
═══════════════════════════════════════════════════════════════════════════
Separa, en el loop agéntico, "pensar" de "hacer" (estilo Claude Code):

  · ``build`` (default) — comportamiento histórico: el modelo elige y ejecuta
    dentro de la frontera, paso a paso.
  · ``plan`` — conservador: solo-lectura (la frontera se compone: normal ∩
    efecto==lectura) y un PLAN estructurado JSON que el doctor aprueba por
    HITL antes de ejecutar nada.
  · ``auto`` — fase plan (igual que ``plan``) y, con plan + token de plan
    válido, fase build que ejecuta EXACTAMENTE los pasos aprobados.

Los PESOS miden el riesgo de cada paso, derivados de la matriz de autonomía
(``data/autonomia_zonas.json``): lectura=1 · escritura_reversible=3 ·
escritura_irreversible=5. El modelo NUNCA aporta el peso (L4): el peso es
propiedad del harness, derivado del efecto declarado. El presupuesto se mide
en pesos acumulados (``max_pesos``), cota dura.

El token de aprobación del plan reusa la Capa 2 (M-055): se firma contra la
huella canónica de (tool, args, justificacion) de cada paso — sin contenido
sensible (L6), un solo uso, TTL. ``presentar_plan`` es una tool INTERNA del
loop (no del plugin_registry ni de la matriz): solo existe en la fase plan.
"""
from __future__ import annotations

from typing import Any

# Peso de riesgo por efecto declarado. Determinista, propiedad del harness.
PESOS_POR_EFECTO: dict[str, int] = {
    "lectura": 1,
    "escritura_reversible": 3,
    "escritura_irreversible": 5,
}

# Primer umbral de presupuesto en pesos para la fase plan (calibrar con uso real).
PESOS_DEFAULT = 10

# Nombre de la tool interna de presentación del plan. No vive en el registro
# de herramientas: el loop la intercepta en la fase plan, antes de la frontera.
PLAN_TOOL = "presentar_plan"

# M-063 · tool interna para preguntar antes de presentar el plan (wizard de
# aclaración). Mismo trato que PLAN_TOOL: solo existe en la fase plan, el loop
# la intercepta antes de la frontera.
PLAN_TOOL_PREGUNTA = "preguntar_aclaracion"

# Tope de preguntas de aclaración antes de forzar la presentación del plan —
# el doctor vino a delegar, no a rendir un interrogatorio sin fin.
MAX_PREGUNTAS_DEFAULT = 3

_MAX_JUSTIFICACION = 400
_MAX_PREGUNTA = 400


def peso_de_tool(tool: str) -> int | None:
    """Peso de riesgo de una tool, derivado de la matriz. None = no declarada
    (nunca un peso inventado: fail-closed)."""
    from core import autonomia as A

    decl = A.REGISTRO.get(tool)
    if not decl:
        return None
    return PESOS_POR_EFECTO.get(decl.get("efecto") or "")


def es_lectura(tool: str) -> bool:
    """¿La tool declarada tiene efecto de solo lectura? (fase plan)"""
    from core import autonomia as A

    decl = A.REGISTRO.get(tool)
    return bool(decl and decl.get("efecto") == "lectura")


def puede_ejecutar_plan(tool: str, modo: str) -> tuple[bool, str]:
    """Frontera de la fase plan: solo-lectura ∩ frontera normal.

    Primero el gate de paradigma (una escritura jamás pasa en plan, defensa en
    profundidad aunque el esquema se filtrara mal) y luego la frontera de
    autonomía con su modo (autonomo/asistido/firmado) intacta.
    """
    from core import autonomia as A

    if not es_lectura(tool):
        return False, "paradigma_plan_solo_lectura"
    return A.puede_ejecutar(tool, modo=modo)


_ESQUEMA_PLAN_TOOL: dict = {
    "type": "function",
    "function": {
        "name": PLAN_TOOL,
        "description": (
            "Presenta el plan estructurado de pasos que propones ejecutar. Cada "
            "paso lleva la tool, sus argumentos y una justificación breve. El "
            "doctor aprobará el plan antes de ejecutar nada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "args": {"type": "object"},
                            "justificacion": {"type": "string"},
                        },
                        "required": ["tool", "args", "justificacion"],
                    },
                },
            },
            "required": ["plan"],
        },
    },
}


_ESQUEMA_PREGUNTA_TOOL: dict = {
    "type": "function",
    "function": {
        "name": PLAN_TOOL_PREGUNTA,
        "description": (
            "Úsala SOLO si falta información para armar un plan razonable (el "
            "doctor pidió algo ambiguo, p. ej. 'ayúdame con una campaña'). Hace "
            "UNA pregunta de aclaración, en español y lenguaje natural; el "
            "doctor la responde escribiendo en el mismo chat, sin comandos. Si "
            "ya tienes lo suficiente, usa presentar_plan en vez de esta."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta": {
                    "type": "string",
                    "description": "La pregunta de aclaración, una sola, texto libre.",
                },
            },
            "required": ["pregunta"],
        },
    },
}


def esquema_plan(tools_schema: list[dict] | None) -> list[dict]:
    """El catálogo que ve el LLM en la fase plan: solo tools de lectura + las
    tools internas ``presentar_plan`` y ``preguntar_aclaracion``. Determinista;
    nada fuera de la matriz."""
    filtrados = [
        dict(e) for e in (tools_schema or [])
        if es_lectura(e.get("function", {}).get("name", ""))
    ]
    filtrados.append(dict(_ESQUEMA_PLAN_TOOL))
    filtrados.append(dict(_ESQUEMA_PREGUNTA_TOOL))
    return filtrados


def validar_pregunta(argumentos: Any) -> str | None:
    """Extrae la pregunta de los argumentos de ``preguntar_aclaracion``.

    None si viene vacía, malformada o excede el tope — fail-closed: no se
    narra basura al doctor."""
    if not isinstance(argumentos, dict):
        return None
    pregunta = argumentos.get("pregunta")
    if not isinstance(pregunta, str):
        return None
    pregunta = pregunta.strip()
    if not pregunta or len(pregunta) > _MAX_PREGUNTA:
        return None
    return pregunta


def _validar_args(tool: str, args: dict, sandbox_root) -> str | None:
    """Valida los argumentos de un paso del plan contra la Capa 1 cuando existe
    esquema; sin esquema se aceptan (se validarán en ejecución). Devuelve None
    si ok, o el motivo estable del rechazo."""
    try:
        from core.harness.layer1_schema.validator import (
            ToolCallInvalida,
            validar_tool_call,
        )
    except Exception:                                   # noqa: BLE001
        return None
    try:
        validar_tool_call(tool, dict(args or {}), sandbox_root)
    except ToolCallInvalida as e:
        if e.motivo == "tool_no_soportada":
            return None                                # sin Capa 1: en ejecución
        return e.motivo
    except Exception:                                  # noqa: BLE001
        return "argumentos_invalidos"
    return None


def validar_plan(entrada: Any, max_pesos: int,
                 sandbox_root) -> tuple[bool, str, dict | None]:
    """Valida el plan presentado por el modelo. Devuelve (ok, motivo, plan_obj).

    El plan_obj añade el peso DERIVADO por paso (nunca el del modelo) y el
    peso_total. Cualquier paso no declarado, mal formado, con args que no pasan
    la Capa 1 o que haga exceder el presupuesto → rechazo fail-closed."""
    if not isinstance(entrada, list):
        return False, "plan_no_estructurado", None
    pasos: list[dict] = []
    total = 0
    for item in entrada:
        if not isinstance(item, dict):
            return False, "plan_paso_malformado", None
        tool = item.get("tool")
        args = item.get("args") or {}
        justificacion = item.get("justificacion") or ""
        if not isinstance(tool, str) or not tool:
            return False, "plan_paso_sin_tool", None
        if not isinstance(args, dict):
            return False, "plan_paso_args_invalidos", None
        peso = peso_de_tool(tool)
        if peso is None:
            return False, "plan_paso_no_declarado", None
        motivo_args = _validar_args(tool, args, sandbox_root)
        if motivo_args:
            return False, f"plan_paso_args_{motivo_args}", None
        if not isinstance(justificacion, str) or len(justificacion) > _MAX_JUSTIFICACION:
            return False, "plan_paso_justificacion_invalida", None
        total += peso
        if total > max_pesos:
            return False, "plan_excede_presupuesto", None
        pasos.append({
            "tool": tool,
            "args": dict(args),
            "justificacion": justificacion,
            "peso": peso,
        })
    if not pasos:
        return False, "plan_vacio", None
    return True, "", {"pasos": pasos, "peso_total": total}


def _reducir(plan: dict) -> list[dict]:
    """Forma canónica firmada: (tool, args, justificacion), sin pesos — el
    peso es propiedad del harness y no debe entrar en el vínculo del token."""
    return [
        {"tool": p["tool"], "args": p["args"], "justificacion": p["justificacion"]}
        for p in (plan.get("pasos") or [])
    ]


def crear_solicitud_plan(plan: dict) -> dict:
    """Solicitud HITL de aprobación del plan (reusa la Capa 2, M-055)."""
    from core.harness.layer2_risk import hitl

    solicitud = hitl.crear_solicitud(
        "_plan", {"pasos": _reducir(plan)}, {"nivel": "alto", "mision": "M-057"},
    )
    lineas = [
        f"  {p['peso']} · {p['tool']} — {p['justificacion']}"
        for p in plan["pasos"]
    ]
    solicitud["tipo"] = "plan"
    solicitud["resumen"] = (
        f"PLAN ({plan['peso_total']} pesos):\n" + "\n".join(lineas)
    )
    solicitud["plan"] = {"pasos": plan["pasos"], "peso_total": plan["peso_total"]}
    return solicitud


def verificar_aprobacion_plan(token: str, plan: dict) -> bool:
    """¿El doctor aprobó ESTE plan? Fail-closed ante cualquier duda."""
    from core.harness.layer2_risk import hitl

    try:
        return hitl.verificar_aprobacion(token, "_plan", {"pasos": _reducir(plan)})
    except hitl.AprobacionInvalida:
        return False
