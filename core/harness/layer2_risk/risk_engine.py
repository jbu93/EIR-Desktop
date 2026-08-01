# -*- coding: utf-8 -*-
"""
core/harness/layer2_risk/risk_engine.py · Clasificación de riesgo (M-055)
═══════════════════════════════════════════════════════════════════════════════
``evaluar_riesgo(tool, args_normalizados, decl)`` clasifica una tool call en
``bajo`` | ``medio`` | ``alto`` y dice si necesita aprobación humana.

Qué NO hace este módulo (L14): **no autoriza**. El PDP de zona/efecto sigue
siendo ``core/autonomia.py``. Dos motores de decisión que puedan divergir son
peor que uno: aquí solo se mide el daño potencial, no el permiso.

De dónde salen las señales: de la DECLARACIÓN en ``data/autonomia_zonas.json``
y de los argumentos YA NORMALIZADOS por la capa 1 (rutas absolutas confinadas,
comando tokenizado como argv). Este módulo jamás re-parsea entrada cruda (L3).

Fail-closed (L2): tool sin declaración, declaración con forma inesperada o
evaluación que lanza → ``alto`` con aprobación obligatoria.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

NIVELES = ("bajo", "medio", "alto")

# Binarios que, aun estando en el allowlist de la capa 1, ejecutan código
# arbitrario ya presente en el sandbox. No se prohíben (la capa 1 ya decidió
# que son admisibles); se marcan para que el humano lo vea al aprobar.
_BINARIOS_QUE_EJECUTAN_CODIGO = frozenset({"python", "python3", "py", "node", "npm", "npx"})

# Umbral por encima del cual una escritura deja de ser "una nota" (L8).
_BYTES_ESCRITURA_GRANDE = 100_000


def _peor(a: str, b: str) -> str:
    """El nivel resultante es siempre el más grave de los dos."""
    return a if NIVELES.index(a) >= NIVELES.index(b) else b


def evaluar_riesgo(tool: str, args_normalizados: dict[str, Any],
                   decl: dict[str, Any] | None) -> dict[str, Any]:
    """Clasifica una tool call ya validada por la capa 1.

    Parameters
    ----------
    tool : str
        Nombre de la herramienta.
    args_normalizados : dict
        Lo que devolvió ``validar_tool_call`` — nunca argumentos crudos.
    decl : dict | None
        Declaración de la herramienta en la matriz de autonomía
        (``core.autonomia.REGISTRO.get(tool)``). ``None`` = no declarada.

    Returns
    -------
    dict
        ``{"nivel": str, "razones": list[str], "requiere_aprobacion": bool}``
    """
    try:
        return _evaluar(tool, dict(args_normalizados or {}), decl)
    except Exception as exc:  # noqa: BLE001 — fail-closed: cualquier fallo es ALTO
        return {
            "nivel": "alto",
            "razones": [f"la evaluacion de riesgo fallo ({type(exc).__name__}): se asume el peor caso"],
            "requiere_aprobacion": True,
        }


def _evaluar(tool: str, args: dict[str, Any], decl: dict[str, Any] | None) -> dict[str, Any]:
    razones: list[str] = []

    if not isinstance(decl, dict) or not decl:
        return {
            "nivel": "alto",
            "razones": [f"'{tool}' no esta declarada en la matriz de autonomia (fail-closed)"],
            "requiere_aprobacion": True,
        }

    efecto = decl.get("efecto")
    zona = decl.get("zona")

    if efecto == "lectura":
        nivel = "bajo"
    elif efecto == "escritura_reversible":
        nivel = "medio"
        razones.append(f"efecto declarado '{efecto}' en zona '{zona}'")
    elif efecto == "escritura_irreversible":
        nivel = "alto"
        razones.append(f"efecto declarado '{efecto}' en zona '{zona}': no hay deshacer")
    else:
        # Efecto ausente o desconocido: no se adivina hacia abajo.
        return {
            "nivel": "alto",
            "razones": [f"efecto '{efecto}' no reconocido en la declaracion de '{tool}' (fail-closed)"],
            "requiere_aprobacion": True,
        }

    if decl.get("requiere_humano") is True:
        nivel = _peor(nivel, "alto")
        razones.append("la matriz marca esta herramienta como requiere_humano")

    nivel = _peor(nivel, _riesgo_por_argumentos(tool, args, razones))

    return {
        "nivel": nivel,
        "razones": razones,
        "requiere_aprobacion": nivel == "alto",
    }


def _riesgo_por_argumentos(tool: str, args: dict[str, Any], razones: list[str]) -> str:
    """Señales que sube el riesgo por lo que la call hace, no por lo que es."""
    nivel = "bajo"

    if tool == "escribir_archivo":
        ruta = args.get("ruta")
        if ruta and Path(ruta).exists():
            nivel = _peor(nivel, "alto")
            razones.append(f"sobrescribe un archivo que YA existe: {Path(ruta).name}")
        contenido = args.get("contenido") or ""
        if len(contenido) > _BYTES_ESCRITURA_GRANDE:
            nivel = _peor(nivel, "alto")
            razones.append(f"escritura grande: {len(contenido)} caracteres")

    if tool == "ejecutar_comando":
        argv = args.get("argv") or []
        binario = Path(str(argv[0])).name.lower() if argv else ""
        binario = binario[:-4] if binario.endswith(".exe") else binario
        if binario in _BINARIOS_QUE_EJECUTAN_CODIGO:
            nivel = _peor(nivel, "alto")
            razones.append(f"'{binario}' puede ejecutar codigo arbitrario ya presente en el sandbox")

    return nivel
