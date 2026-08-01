"""runner_marketing.py · Rol marketing del sandbox desktop.

Redacta contenido social media, organiza cronogramas, centra la voz de
marca EIR. NO toca PHI ni criterio clinico: todas sus tools son
administrativas, zona=lectura.
"""
from __future__ import annotations

from .cable_local import construir_ejecutores, construir_tools_schema
from .cliente_opencode import resolver_cliente
from .plugin_registry import poblar_registro_completo

SISTEMA_MARKETING = (
    "Eres EIR, asistente de marketing de un consultorio odontologo. "
    "Redactas contenido educativo (no testimonios clinicos ni promesas "
    "de resultados), programas redes y propones cronogramas. Nunca "
    "prometes curas ni eficacia con cifras; respetas la Ley 1164 / "
    "etica medica. No usas datos de pacientes reales como caso de "
    "marketing sin consentimiento explicito."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True,
                   token_aprobacion: str | None = None,
                   paradigma: str = "build",
                   max_pesos: int | None = None,
                   plan: dict | None = None,
                   token_plan: str | None = None) -> dict:
    historial = list(historial or [])
    lc = resolver_cliente("marketing")

    # Poblar registro de plugins ANTES de ejecutar el loop
    poblar_registro_completo()

    try:
        from core import agente_loop
    except Exception as exc:
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}
    r = agente_loop.ejecutar(
        lc=lc, modelo="mock-marketing", mensaje=mensaje, historial=historial,
        sistema=SISTEMA_MARKETING, habilitado=habilitar_loop,
        token_aprobacion=token_aprobacion,
        paradigma=paradigma, max_pesos=max_pesos, plan=plan, token_plan=token_plan,
        ejecutores=construir_ejecutores(),
        tools_schema=construir_tools_schema(),
        max_pasos=2, max_segundos=12.0,
    )
    # M-052 · cliente cloud: el backend ya narró la respuesta → se expone tal cual
    traza_cloud = getattr(lc, "ultima_traza", None)
    texto_final = getattr(lc, "ultimo_texto_final", None)
    credito = getattr(lc, "ultimo_credito", None)
    out = {
        "pasos": traza_cloud if traza_cloud else [
            {"n": p.n, "herramienta": p.herramienta, "ok": p.ok,
             "resumen": p.resumen, "motivo": p.motivo} for p in r.pasos],
        "tope_alcanzado": r.tope_alcanzado, "motivo_fin": r.motivo_fin,
        "segundos": r.segundos, "resumen": texto_final or r.resumen_para_narrar(),
        "aprobacion_pendiente": r.aprobacion_pendiente,
        "plan": r.plan,
        "peso_total": r.peso_total,
    }
    if credito is not None:
        out["credito_restante_hoy"] = credito
    return out
