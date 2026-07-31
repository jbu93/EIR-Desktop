"""agent_hooks.py · Middleware de ciclo de vida (Agentic Hooks) del desktop EIR.

Este módulo envuelve cada paso del agente con tres hooks intercambiables:

  - ``run_pre_hooks``  : verifica seguridad y conectividad ANTES de invocar la
                         herramienta. Fail-closed (L2): si la herramienta no
                         está declarada en ``data/autonomia_zonas.json``, o la
                         matriz no se puede leer, el paso se BLOQUEA.
  - ``run_post_hooks`` : evalúa la respuesta de la herramienta DESPUÉS de
                         ejecutarla. Si la herramienta devolvió un error,
                         marca el paso como ``needs_clarification`` sin inventar
                         datos (L4).
  - ``run_error_hook`` : captura excepciones y produce un mensaje de
                         degradación elegante (fail-closed, L2) reutilizando el
                         literal canónico "no inventes este dato, dilo con
                         honestidad" que ya vive en ``core/agente_loop.py``.

Diseño: módulo puro (sin Flask, sin red, sin estado global). Cada función
recibe el paso y la sesión para que sea trivially testable en el arnés sin red
(``scripts/smoke_agente_local.py``).
"""
from __future__ import annotations

import json
import os
from typing import Any

# Literal canónico de honestidad. Reusamos EXACTAMENTE la frase que ya usa
# core/agente_loop.resumen_para_narrar (línea 66) para no divergir en dos
# formas de decir lo mismo (L4/L14).
FRASE_NO_INVENTES = "no inventes este dato, dilo con honestidad"


def _buscar_autonomia_zonas() -> str | None:
    """Busca de forma defensiva el primer ``data/autonomia_zonas.json`` real.

    En lugar de suponer una profundidad fija de directorios (frágil si el
    módulo se mueve o se empaqueta con PyInstaller), escala hacia arriba
    desde este archivo buscando la carpeta ``data/`` del proyecto EIR.
    Devuelve ``None`` si no lo encuentra en ningún nivel conocido.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # Repo de código abierto (estructura plana): core_desktop/ está 1 nivel
        # por debajo de la raíz, la matriz vive en <raiz>/data/.
        os.path.join(here, "..", "data", "autonomia_zonas.json"),
        os.path.join(here, "..", "..", "data", "autonomia_zonas.json"),
        os.path.join(here, "..", "..", "..", "data", "autonomia_zonas.json"),
        os.path.join(here, "..", "..", "..", "..", "data", "autonomia_zonas.json"),
    ]
    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if os.path.isfile(normalized):
            return normalized
    return None


def _leer_autonomia() -> dict[str, Any]:
    """Carga la matriz de autonomía. Fail-closed: si falta, lanza FileNotFoundError."""
    ruta = _buscar_autonomia_zonas()
    if not ruta:
        raise FileNotFoundError(
            "No se encontró data/autonomia_zonas.json (fail-closed: sin matriz, sin autonomía)."
        )
    with open(ruta, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    herramientas = data.get("herramientas", {})
    if not isinstance(herramientas, dict):
        raise ValueError("La matriz de autonomía no tiene el formato esperado (herramientas como dict).")
    return herramientas


def _nombre_herramienta(paso: dict[str, Any]) -> str:
    """Extrae el nombre de la herramienta del paso (acepta variantes de clave)."""
    return paso.get("herramienta") or paso.get("name") or ""


def run_pre_hooks(rol: str, paso: dict[str, Any], sesion: dict[str, Any]) -> dict[str, Any]:
    """Valida la herramienta ANTES de ejecutarla. Fail-closed (L2).

    - Si el nombre está vacío: se permite (no hay herramienta que bloquear).
    - Si la matriz no se lee o la herramienta no está declarada: BLOQUEA.
    - Si la herramienta está declarada con ``requiere_humano=True``: BLOQUEA
      en modo autónomo (override explícito del Soberano).

    Devuelve ``{"permitido": bool, "motivo": str|None}``.
    """
    tool_name = _nombre_herramienta(paso)
    if not tool_name:
        return {"permitido": True, "motivo": None}

    try:
        declaradas = _leer_autonomia()
    except Exception as exc:  # noqa: BLE001 — fail-closed: bloquea ante cualquier fallo de lectura
        return {
            "permitido": False,
            "motivo": f"Bloqueo de seguridad: no se pudo leer la matriz de autonomía ({exc})",
        }

    if tool_name not in declaradas:
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}' no declarada en la matriz de autonomía.",
        }

    if declaradas[tool_name].get("requiere_humano"):
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}' requiere humano (override de autonomía).",
        }

    return {"permitido": True, "motivo": None}


def run_post_hooks(rol: str, paso: dict[str, Any], resultado: Any, sesion: dict[str, Any]) -> dict[str, Any]:
    """Evalúa la respuesta de la herramienta. Si devolvió error, pide aclaración.

    NUNCA inventa datos (L4): si el resultado indica un fallo, se marca
    ``needs_clarification=True`` con el motivo textual devuelto por la tool.

    Devuelve ``{"needs_clarification": bool, "motivo": str|None, "resultado": Any}``.
    """
    if isinstance(resultado, dict) and resultado.get("error"):
        return {
            "needs_clarification": True,
            "motivo": f"La herramienta devolvió un error: {resultado['error']}",
            "resultado": resultado,
        }
    return {"needs_clarification": False, "motivo": None, "resultado": resultado}


def run_error_hook(rol: str, paso: dict[str, Any], error: Exception, sesion: dict[str, Any]) -> str:
    """Captura excepciones y produce un mensaje de degradación honesta (L2/L4).

    Reutiliza ``FRASE_NO_INVENTES`` (el literal canónico de ``core.agente_loop``)
    para que el usuario reciba la misma señal de honestidad en toda la superficie.
    """
    tool_name = _nombre_herramienta(paso) or "desconocida"
    return (
        f"La herramienta '{tool_name}' falló en la ejecución: {error}. "
        f"{FRASE_NO_INVENTES}"
    )
