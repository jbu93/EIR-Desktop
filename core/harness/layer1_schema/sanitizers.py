# -*- coding: utf-8 -*-
"""
core/harness/layer1_schema/sanitizers.py · Sanitizadores de la Capa 1 (M-054)
═══════════════════════════════════════════════════════════════════════════════
Detienen tres familias de abuso antes de que un argumento toque un handler:

  · path_escapado       — path traversal (../../etc/passwd) y cruce de volumen
                          (C:\\ vs D:\\). Mismo patrón commonpath que
                          DR_EIR_EXOCAD_LITE/core_engine/security_sanitizer.py.
  · comando_no_permitido— el primer token del comando no está en el allowlist.
  · metacaracteres_shell— ; | & > < ` $ ( ) y saltos de línea: si están, NO se
                          ejecuta nada (aunque el binario fuera permitido).

Estas funciones son puras y deterministas: sin red, sin estado. El motivo de
error es SIEMPRE un código estable (apto para tests y telemetría), nunca una
frase que pueda arrastrar contenido del usuario (L6/PHI).
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path


class ToolCallInvalida(Exception):
    """Una tool call fue rechazada por la Capa 1. ``motivo`` es un código estable."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


# ─── confinamiento de rutas (G1) ────────────────────────────────────────
def confinar_ruta(ruta: str, raiz: Path) -> Path:
    """Devuelve la ruta absoluta de ``ruta`` confinada bajo ``raiz``.

    Lanza ToolCallInvalida("path_escapado") si la ruta escapa (directorio o
    volumen). Fail-closed: ruta vacía o de tipo inválido también rechaza.
    """
    if not ruta or not isinstance(ruta, str):
        raise ToolCallInvalida("path_escapado")
    base = Path(os.path.abspath(str(raiz)))
    cruda = Path(ruta)
    # Las rutas RELATIVAS se resuelven contra la raíz del sandbox, jamás contra
    # el CWD del proceso (que podría estar fuera). Las absolutas se normalizan.
    objetivo = Path(os.path.abspath(cruda)) if cruda.is_absolute() else (base / cruda).resolve()
    try:
        comun = os.path.commonpath([str(base), str(objetivo)])
    except ValueError:  # volúmenes distintos (C:\\ vs D:\\)
        raise ToolCallInvalida("path_escapado") from None
    if Path(comun) != base:
        raise ToolCallInvalida("path_escapado")
    return objetivo


# ─── ejecución de comandos (G2/G3) ──────────────────────────────────────
# Binarios que el agente del desktop puede lanzar DENTRO del sandbox. Nada
# fuera de aquí se ejecuta jamás. Añadir un binario exige revisión del Soberano.
COMANDOS_PERMITIDOS = frozenset({
    "git", "python", "node", "echo", "pwd", "ls", "dir", "date", "stl_tool",
})

# Metacaracteres de shell que prohiben ejecutar: el comando va a subprocess
# con shell=False y, aunque lo fuera, estos caracteres permiten encadenar o
# redirigir. El '$' cubre $() y ${}; el '\n' evita inyección por salto de línea.
_METACARACTERES = re.compile(r"[;&|<>`$\r\n]")


def verificar_comando(comando: str) -> list[str]:
    """Valida un comando de la tool ejecutar_comando.

    1. Rechaza metacharacteres de shell → ToolCallInvalida("metacaracteres_shell").
    2. Tokeniza (shlex, sin POSIX para conservar backslashes de Windows).
    3. Rechaza binarios fuera del allowlist → ToolCallInvalida("comando_no_permitido").

    Devuelve el argv ya tokenizado, listo para subprocess.run(argv, shell=False).
    """
    if _METACARACTERES.search(comando):
        raise ToolCallInvalida("metacaracteres_shell")
    try:
        argv = shlex.split(comando, posix=False)
    except ValueError:
        raise ToolCallInvalida("metacaracteres_shell") from None
    if not argv:
        raise ToolCallInvalida("comando_no_permitido")
    if argv[0] not in COMANDOS_PERMITIDOS:
        raise ToolCallInvalida("comando_no_permitido")
    return argv
