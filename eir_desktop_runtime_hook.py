"""eir_desktop_runtime_hook.py · Runtime hook de PyInstaller (auto-update).

ESTE ARCHIVO SOLO CORRE DENTRO DEL BUNDLE PyInstaller del release, NUNCA
en desarrollo ni en la CI (PyInstaller inyecta los runtime hooks al ejecutable
empaquetado).

Propósito (L10 · AGENTS.md): los kill-switches nacen APAGADOS en el código
fuente, pero el `.exe` que se publica como release ES un acto deliberado del
Soberano. Aquí se siembra el switch del auto-update para que el bundle traiga
la capacidad ENCENDIDA, sin congelar nada a nivel de módulo: `actualizador.py`
sigue leyendo `os.getenv("EIR_DESKTOP_AUTOUPDATE")` en tiempo de llamada.

Si algún día se quiere publicar un build SIN auto-update, se quita este hook
del spec (o se siembra "0") y se reconstruye: el release no reactiva la
capacidad por casualidad.
"""
from __future__ import annotations

import os

if os.getenv("EIR_DESKTOP_AUTOUPDATE") is None:
    os.environ["EIR_DESKTOP_AUTOUPDATE"] = "1"
