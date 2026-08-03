"""runner_recepcion.py · Rol recepcion del sandbox desktop.

A diferencia del rol odontologo, este NO toca criterio clinico: gestiona
agenda, busca doctores, toma mensajeria. No requiere guardrails clinicos
estrictos (L1/L3) pero si respeta el fail-closed de autonomia: toda tool
no declarada en data/autonomia_zonas.json queda denegada.
"""
from __future__ import annotations

from .cable_local import construir_ejecutores, construir_tools_schema, resolver_texto_final
from .cliente_opencode import resolver_cliente
from .plugin_registry import poblar_registro_completo

SISTEMA_RECEPCION = (
    "Eres EIR, recepcionista agil de un consultorio odontologo. Hablas "
    "en español neutro, amable y concreto. Gestionas agenda, buscas "
    "doctores disponibles y archivas mensajes. No diagnosticas ni "
    "recomiendas tratamientos. Si algo requiere criterio clinico, lo "
    "escalonas al rol odontologo, no improvisas."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True,
                   token_aprobacion: str | None = None,
                   paradigma: str = "build",
                   max_pesos: int | None = None,
                   plan: dict | None = None,
                   token_plan: str | None = None,
                   preguntas_hechas: int = 0) -> dict:
    historial = list(historial or [])
    lc = resolver_cliente("recepcion", paradigma=paradigma)

    # Poblar registro de plugins ANTES de ejecutar el loop
    poblar_registro_completo()

    try:
        from core import agente_loop
    except Exception as exc:
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}
    r = agente_loop.ejecutar(
        lc=lc, modelo="mock-recepcion", mensaje=mensaje, historial=historial,
        sistema=SISTEMA_RECEPCION, habilitado=habilitar_loop,
        token_aprobacion=token_aprobacion,
        paradigma=paradigma, max_pesos=max_pesos, plan=plan, token_plan=token_plan,
        preguntas_hechas=preguntas_hechas,
        ejecutores=construir_ejecutores(),
        tools_schema=construir_tools_schema(),
        max_pasos=2, max_segundos=12.0,
    )
    # M-063 · el modelo pidió aclaración antes del plan: ESA es la respuesta
    # visible, tal cual — no se re-consulta al LLM (perdería la pregunta).
    if r.pregunta_aclaracion:
        resumen = r.pregunta_aclaracion
    else:
        # M-052 · cliente cloud: el backend ya narró la respuesta → se expone tal cual
        # D097 · para el resto, si el LLM no pidió ninguna tool, se pide el texto
        # conversacional real en vez de exponer el resumen técnico interno.
        resumen = resolver_texto_final(
            lc, r, modelo="mock-recepcion", sistema=SISTEMA_RECEPCION,
            historial=historial, mensaje=mensaje,
        )
    traza_cloud = getattr(lc, "ultima_traza", None)
    credito = getattr(lc, "ultimo_credito", None)
    out = {
        "pasos": traza_cloud if traza_cloud else [
            {"n": p.n, "herramienta": p.herramienta, "ok": p.ok,
             "resumen": p.resumen, "motivo": p.motivo} for p in r.pasos],
        "tope_alcanzado": r.tope_alcanzado, "motivo_fin": r.motivo_fin,
        "segundos": r.segundos, "resumen": resumen,
        "aprobacion_pendiente": r.aprobacion_pendiente,
        "plan": r.plan,
        "peso_total": r.peso_total,
    }
    if credito is not None:
        out["credito_restante_hoy"] = credito
    return out
