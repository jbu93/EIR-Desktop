# -*- coding: utf-8 -*-
"""
eir_desktop_v1/core_desktop/terminal_tools.py · Terminal Tools nativas (M-054)
═══════════════════════════════════════════════════════════════════════════════
Canal de ejecución local del desktop EIR. Toda tool call pasa por la Capa 1
(``core.harness.layer1_schema.validator``) ANTES de tocar un handler: esquema
Pydantic v2 + confinamiento de ruta + allowlist de binarios (Validator-First, L3).

  · leer_archivo        zona investigacion · lectura
  · listar_archivos     zona investigacion · lectura
  · buscar_texto        zona investigacion · lectura
  · escribir_archivo    zona producto · escritura_irreversible · requiere_humano
  · ejecutar_comando    zona producto · escritura_irreversible · requiere_humano

La autorización de ZONA/EFECTO/requiere_humano (L2) NO se duplica aquí: la aplica
``agent_hooks.run_pre_hooks`` en el orquestador contra ``data/autonomia_zonas.json``.
Esta capa solo garantiza que lo que llega al handler es benigno y confinado.

Todas las rutas se confinan a ``EIR_SANDBOX_ROOT`` (env leído en tiempo de
llamada — L10). Sin esa variable el default es ``~/.eir_dr/sandbox`` en el
bundle PyInstaller y <repo>/sandbox en desarrollo. Nada puede escapar de ahí.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from core.harness.layer1_schema.validator import validar_tool_call, ToolCallInvalida

# Tope de salida de un comando para no inyectar megabytes en el contexto (L8).
_MAX_SALIDA = 4000
# Tope de archivos escaneados por buscar_texto.
_MAX_ARCHIVOS_BUSQUEDA = 500


def _raiz_sandbox() -> Path:
    """Raíz a la que se confinan todas las rutas. Leída en tiempo de llamada (L10).

    Orden de resolución:
      1. ``EIR_SANDBOX_ROOT`` si está definida — el Soberano manda siempre.
      2. Bundle PyInstaller (``sys.frozen``): ``~/.eir_dr/sandbox``. En onefile
         ``__file__`` cuelga de ``sys._MEIPASS``, un temporal que Windows BORRA
         al cerrar la app: el sandbox del doctor sería efímero. Se usa la misma
         convención de datos de usuario que ``sesion.py`` y ``app_control.py``.
      3. Desarrollo: ``<repo>/sandbox`` (comportamiento histórico).
    """
    env = os.environ.get("EIR_SANDBOX_ROOT", "").strip()
    if env:
        raiz = Path(env)
    elif getattr(sys, "frozen", False):
        raiz = Path.home() / ".eir_dr" / "sandbox"
    else:
        raiz = Path(__file__).resolve().parent.parent.parent / "sandbox"
    try:
        raiz.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return raiz


# ─── handlers (reciben SOLO argumentos ya validados y confinados) ───────
def _leer_archivo(ruta: str) -> dict[str, Any]:
    p = Path(ruta)
    if not p.is_file():
        return {"ok": False, "motivo": "archivo_no_existe", "ruta": ruta}
    try:
        contenido = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "motivo": "lectura_fallo", "detalle": str(exc)[:200]}
    return {"ok": True, "archivo": p.name, "ruta": ruta, "contenido": contenido}


def _listar_archivos(ruta: str) -> dict[str, Any]:
    p = Path(ruta)
    if not p.is_dir():
        return {"ok": False, "motivo": "carpeta_no_existe", "ruta": ruta}
    archivos = sorted(e.name for e in p.iterdir() if e.is_file())
    return {"ok": True, "ruta": ruta, "archivos": archivos, "n_archivos": len(archivos)}


def _buscar_texto(patron: str, ruta: str) -> dict[str, Any]:
    try:
        rx = re.compile(patron)
    except re.error:
        return {"ok": False, "motivo": "patron_invalido"}
    coincidencias = 0
    archivos_escaneados = 0
    for p in Path(ruta).rglob("*"):
        if archivos_escaneados >= _MAX_ARCHIVOS_BUSQUEDA:
            break
        if not p.is_file():
            continue
        archivos_escaneados += 1
        try:
            texto = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        coincidencias += len(rx.findall(texto))
    return {
        "ok": True,
        "patron": patron,
        "ruta": ruta,
        "coincidencias": coincidencias,
        "archivos_escaneados": archivos_escaneados,
    }


def _escribir_archivo(ruta: str, contenido: str) -> dict[str, Any]:
    p = Path(ruta)
    try:
        p.write_text(contenido, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "motivo": "escritura_fallo", "detalle": str(exc)[:200]}
    return {"ok": True, "archivo": p.name, "ruta": ruta,
            "bytes": len(contenido.encode("utf-8"))}


def _ejecutar_comando(argv: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(_raiz_sandbox()),
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"ok": False, "motivo": "binario_no_encontrado"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": "timeout"}
    salida = (proc.stdout or "")[:_MAX_SALIDA]
    error = (proc.stderr or "")[:2000]
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "salida": salida,
        "error": error,
    }


_MANEJADORES: dict[str, Callable[..., dict[str, Any]]] = {
    "leer_archivo": _leer_archivo,
    "listar_archivos": _listar_archivos,
    "buscar_texto": _buscar_texto,
    "escribir_archivo": _escribir_archivo,
    "ejecutar_comando": _ejecutar_comando,
}


def ejecutar_tool(nombre: str, argumentos: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una terminal tool tras pasar la Capa 1 (Validator-First).

    Devuelve siempre un dict. Ante argumentos inválidos devuelve
    ``{"ok": False, "motivo": <código estable>}`` (fail-closed, L2).
    """
    try:
        normalizado = validar_tool_call(nombre, argumentos, _raiz_sandbox())
    except ToolCallInvalida as exc:
        return {"ok": False, "motivo": exc.motivo}

    handler = _MANEJADORES.get(nombre)
    if handler is None:
        return {"ok": False, "motivo": "tool_no_soportada"}
    try:
        return handler(**normalizado)
    except Exception as exc:  # noqa: BLE001 — degradación honesta, nunca un leak
        return {"ok": False, "motivo": "error_ejecucion", "detalle": str(exc)[:300]}


def listar() -> list[str]:
    """Nombres de las terminal tools disponibles."""
    return sorted(_MANEJADORES)
