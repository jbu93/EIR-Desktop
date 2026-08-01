# -*- coding: utf-8 -*-
"""
eir_desktop_v1/core_desktop/lsp_tools.py · LSP tools nativas (M-055 Fase 2B)
═══════════════════════════════════════════════════════════════════════════════
Lectura de código con un analizador real, no con expresiones regulares:

  · lsp_definicion    zona investigacion · lectura · dónde se DEFINE un símbolo
  · lsp_referencias   zona investigacion · lectura · dónde se USA de verdad
  · lsp_diagnosticos  zona investigacion · lectura · qué ve roto el analizador

Las tres son de solo lectura y autónomas: leer código no le hace daño a nadie,
y obligar a aprobar cada consulta convertiría el HITL en ruido que el doctor
aprendería a ignorar.

Toda ruta pasa por la Capa 1 y queda confinada a ``EIR_SANDBOX_ROOT`` — se
reutiliza ``terminal_tools._raiz_sandbox()`` para que no existan dos raíces que
puedan divergir.

Honestidad (L4): si el servidor LSP no arranca, muere o no responde, se devuelve
``{"ok": False, "motivo": ...}``. Una lista vacía de referencias significa "no
hay", nunca "no pude preguntar".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.harness.layer1_schema.validator import validar_tool_call, ToolCallInvalida
from core.harness.lsp import cliente_lsp
from core.harness.lsp.cliente_lsp import LspNoDisponible

from .terminal_tools import _raiz_sandbox

# Tope de ubicaciones devueltas: un símbolo muy usado no debe inundar el
# contexto del modelo (L8). El total real se informa aparte, sin mentir.
_MAX_UBICACIONES = 100


def _relativa(archivo: str, raiz: Path) -> str:
    """Ruta relativa al sandbox: el absoluto del disco del doctor no aporta."""
    try:
        return str(Path(archivo).resolve().relative_to(raiz.resolve()))
    except (ValueError, OSError):
        return Path(archivo).name


def _acotar(ubicaciones: list[dict], raiz: Path) -> dict[str, Any]:
    for u in ubicaciones:
        u["archivo"] = _relativa(u.get("archivo", ""), raiz)
    return {
        "ubicaciones": ubicaciones[:_MAX_UBICACIONES],
        "n_total": len(ubicaciones),
        "truncado": len(ubicaciones) > _MAX_UBICACIONES,
    }


def _lsp_definicion(ruta: str, linea: int, columna: int, timeout: float) -> dict[str, Any]:
    raiz = _raiz_sandbox()
    ubic = cliente_lsp.definicion(Path(ruta), linea, columna, raiz, timeout=timeout)
    return {"ok": True, **_acotar(ubic, raiz)}


def _lsp_referencias(ruta: str, linea: int, columna: int, timeout: float) -> dict[str, Any]:
    raiz = _raiz_sandbox()
    ubic = cliente_lsp.referencias(Path(ruta), linea, columna, raiz, timeout=timeout)
    return {"ok": True, **_acotar(ubic, raiz)}


def _lsp_diagnosticos(ruta: str, timeout: float) -> dict[str, Any]:
    raiz = _raiz_sandbox()
    diags = cliente_lsp.diagnosticos(Path(ruta), raiz, timeout=timeout)
    errores = [d for d in diags if d.get("severidad") == 1]
    return {
        "ok": True,
        "archivo": _relativa(ruta, raiz),
        "diagnosticos": diags[:_MAX_UBICACIONES],
        "n_errores": len(errores),
        "n_total": len(diags),
    }


_MANEJADORES = {
    "lsp_definicion": _lsp_definicion,
    "lsp_referencias": _lsp_referencias,
    "lsp_diagnosticos": _lsp_diagnosticos,
}


def ejecutar_tool(nombre: str, argumentos: dict[str, Any],
                  timeout: float = cliente_lsp.TIMEOUT_POR_DEFECTO) -> dict[str, Any]:
    """Ejecuta una LSP tool tras pasar la Capa 1 (Validator-First).

    Devuelve siempre un dict. Fail-closed (L2) y honesto (L4): ningún fallo del
    servidor se disfraza de resultado vacío.
    """
    try:
        normalizado = validar_tool_call(nombre, argumentos, _raiz_sandbox())
    except ToolCallInvalida as exc:
        return {"ok": False, "motivo": exc.motivo}

    handler = _MANEJADORES.get(nombre)
    if handler is None:
        return {"ok": False, "motivo": "tool_no_soportada"}

    ruta = Path(normalizado["ruta"])
    if not ruta.is_file():
        return {"ok": False, "motivo": "archivo_no_existe"}

    try:
        return handler(timeout=timeout, **normalizado)
    except LspNoDisponible as exc:
        return {"ok": False, "motivo": exc.motivo}
    except Exception as exc:  # noqa: BLE001 — degradación honesta, nunca un leak
        return {"ok": False, "motivo": "error_ejecucion", "detalle": str(exc)[:300]}


def listar() -> list[str]:
    """Nombres de las LSP tools disponibles."""
    return sorted(_MANEJADORES)
