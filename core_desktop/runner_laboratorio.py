"""runner_laboratorio.py · Rol laboratorio del sandbox desktop.

Cubre pedidos protesis CAD, info de materiales, comunicacion con el
laboratorio externo. Usa ``info_cad_protesis`` (ya declarada en
data/autonomia_zonas.json zona=clinica, efecto=lectura). El sandbox
validara el stub de ``leer_stl_local`` (Fase 2) tambien bajo este rol,
pero como esqueleto, no como tool activa.
"""
from __future__ import annotations

from .cable_local import construir_ejecutores, construir_tools_schema
from .cliente_opencode import resolver_cliente
from .plugin_registry import poblar_registro_completo

SISTEMA_LABORATORIO = (
    "Eres EIR, asistente de laboratorio dental. Redacta pedidos a "
    "proveedores, resume fichas CAD de protesis y describe materiales "
    "por especialidad. No prometes tiempos sin confirmacion del lab. "
    "Si un archivo STL local esta disponible lo describes solo por "
    "geometria (bounding box, vertices); no disenas protesis."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True,
                   token_aprobacion: str | None = None,
                   paradigma: str = "build",
                   max_pesos: int | None = None,
                   plan: dict | None = None,
                   token_plan: str | None = None) -> dict:
    historial = list(historial or [])
    lc = resolver_cliente("laboratorio")

    # Poblar registro de plugins ANTES de ejecutar el loop
    poblar_registro_completo()

    try:
        from core import agente_loop
    except Exception as exc:
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}
    r = agente_loop.ejecutar(
        lc=lc, modelo="mock-laboratorio", mensaje=mensaje, historial=historial,
        sistema=SISTEMA_LABORATORIO, habilitado=habilitar_loop,
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
