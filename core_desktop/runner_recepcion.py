"""runner_recepcion.py · Rol recepcion del sandbox desktop.

A diferencia del rol odontologo, este NO toca criterio clinico: gestiona
agenda, busca doctores, toma mensajeria. No requiere guardrails clinicos
estrictos (L1/L3) pero si respeta el fail-closed de autonomia: toda tool
no declarada en data/autonomia_zonas.json queda denegada.
"""
from __future__ import annotations

from .cliente_opencode import resolver_cliente

SISTEMA_RECEPCION = (
    "Eres EIR, recepcionista agil de un consultorio odontologo. Hablas "
    "en español neutro, amable y concreto. Gestionas agenda, buscas "
    "doctores disponibles y archivas mensajes. No diagnosticas ni "
    "recomiendas tratamientos. Si algo requiere criterio clinico, lo "
    "escalonas al rol odontologo, no improvisas."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True) -> dict:
    historial = list(historial or [])
    lc = resolver_cliente("recepcion")
    try:
        from core import agente_loop
    except Exception as exc:
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}
    r = agente_loop.ejecutar(
        lc=lc, modelo="mock-recepcion", mensaje=mensaje, historial=historial,
        sistema=SISTEMA_RECEPCION, habilitado=habilitar_loop,
        max_pasos=2, max_segundos=12.0,
    )
    return {
        "pasos": [{"n": p.n, "herramienta": p.herramienta, "ok": p.ok,
                   "resumen": p.resumen, "motivo": p.motivo} for p in r.pasos],
        "tope_alcanzado": r.tope_alcanzado, "motivo_fin": r.motivo_fin,
        "segundos": r.segundos, "resumen": r.resumen_para_narrar(),
    }
