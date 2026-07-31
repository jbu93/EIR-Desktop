"""runner_laboratorio.py · Rol laboratorio del sandbox desktop.

Cubre pedidos protesis CAD, info de materiales, comunicacion con el
laboratorio externo. Usa ``info_cad_protesis`` (ya declarada en
data/autonomia_zonas.json zona=clinica, efecto=lectura). El sandbox
validara el stub de ``leer_stl_local`` (Fase 2) tambien bajo este rol,
pero como esqueleto, no como tool activa.
"""
from __future__ import annotations

from .cliente_opencode import resolver_cliente

SISTEMA_LABORATORIO = (
    "Eres EIR, asistente de laboratorio dental. Redacta pedidos a "
    "proveedores, resume fichas CAD de protesis y describe materiales "
    "por especialidad. No prometes tiempos sin confirmacion del lab. "
    "Si un archivo STL local esta disponible lo describes solo por "
    "geometria (bounding box, vertices); no disenas protesis."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True) -> dict:
    historial = list(historial or [])
    lc = resolver_cliente("laboratorio")
    try:
        from core import agente_loop
    except Exception as exc:
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}
    r = agente_loop.ejecutar(
        lc=lc, modelo="mock-laboratorio", mensaje=mensaje, historial=historial,
        sistema=SISTEMA_LABORATORIO, habilitado=habilitar_loop,
        max_pasos=2, max_segundos=12.0,
    )
    return {
        "pasos": [{"n": p.n, "herramienta": p.herramienta, "ok": p.ok,
                   "resumen": p.resumen, "motivo": p.motivo} for p in r.pasos],
        "tope_alcanzado": r.tope_alcanzado, "motivo_fin": r.motivo_fin,
        "segundos": r.segundos, "resumen": r.resumen_para_narrar(),
    }
