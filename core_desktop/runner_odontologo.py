"""runner_odontologo.py · Rol odontologo del sandbox desktop.

Envoltorio delgado sobre ``core.agente_loop.ejecutar`` (SIN modificarlo).
Inyecta:
  - ``lc``         : un ``ClienteMockSandbox(rol='odontologo')`` 100% offline.
  - ``sistema``    : system_prompt del rol odontologo (▼ ENDPOINT EIR CLOUD).
  - ``ejecutores`` : mapeo de tools locales disponibles para este rol.

En Fase 1 real (cuando ``eirdr.com/api/v1/inference`` exista):
  - ``ClienteMockSandbox`` se sustituye por el cliente HTTP al gateway EIR.
  - El system_prompt vive en el BACKEND cloud (no en el cliente). El cliente
    solo envia historial + tools_locales_disponibles y recibe ``tool_call``.
  Aqui el cliente lo trae por mock; el backend lo traera por ajuste fino del
  Soberano, ya que tocar criterio clinico es autoridad del Soberano
  (AGENTS.md §0: "cuando algo toca criterio clinico, el es la autoridad").

Lo que el sandbox valida:
  - el modulo importa y compila
  - ``ejecutar()`` se llama con el signature correcto (inyectando todo)
  - el dispatch por rol (routes_desktop) enruta correctamente a este runner
"""
from __future__ import annotations

from .cliente_opencode import resolver_cliente

SISTEMA_ODONTOLOGO = (
    "Eres EIR, copiloto clinico odontologo. Respondes en español neutro. "
    "Aplicas el validador-clinico primero (L3 AGENTS.md). Nada de cifras "
    "sin respaldo (L4). El paciente es parte de la identidad de toda "
    "respuesta clinica (L1). Hoy operas en modo sandbox: cuando el LLM "
    "cloud no este disponible, declaras limitacion honesta, NO inventas."
)


def ejecutar_local(mensaje: str, historial: list | None = None,
                   *, habilitar_loop: bool = True) -> dict:
    """Ejecuta el rol odontologo en el sandbox.

    Con EIR_OPENCODE_ENABLED apagado (default) → ClienteMockSandbox (offline).
    Con EIR_OPENCODE_ENABLED=1 → ClienteOpencode (BYOK local, M-051).
    """
    historial = list(historial or [])
    lc = resolver_cliente("odontologo")

    try:
        from core import agente_loop
    except Exception as exc:               # sandbox puede correr sin core/
        return {"pasos": [], "tope_alcanzado": False, "motivo_fin": f"sin_core:{exc!r}",
                "segundos": 0.0, "resumen": ""}

    # Ejecutores: en Fase 2 real, merge de tools cloud (zona=lectura) +
    # tools locales nuevas (local_tools.py). En el sandbox, si las 6 tools
    # existentes de shell_tools.TOOLS_EXEC son zona=lectura (verificado en
    # docs/09), basta con dejar que agente_loop las tome del import default.
    r = agente_loop.ejecutar(
        lc=lc,
        modelo="mock-odontologo",
        mensaje=mensaje,
        historial=historial,
        sistema=SISTEMA_ODONTOLOGO,
        habilitado=habilitar_loop,
        max_pasos=2,       # sandbox corto: basta para ver 1 tool-call + 1 salida
        max_segundos=15.0,
    )
    # M-052 · cliente cloud: el backend ya narró la respuesta → se expone tal cual
    traza_cloud = getattr(lc, "ultima_traza", None)
    texto_final = getattr(lc, "ultimo_texto_final", None)
    credito = getattr(lc, "ultimo_credito", None)
    out = {
        "pasos": traza_cloud if traza_cloud else [
            {"n": p.n, "herramienta": p.herramienta, "ok": p.ok,
             "resumen": p.resumen, "motivo": p.motivo}
            for p in r.pasos
        ],
        "tope_alcanzado": r.tope_alcanzado,
        "motivo_fin": r.motivo_fin,
        "segundos": r.segundos,
        "resumen": texto_final or r.resumen_para_narrar(),
    }
    if credito is not None:
        out["credito_restante_hoy"] = credito
    return out
