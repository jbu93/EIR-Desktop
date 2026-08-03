"""Runtime hook de PyInstaller para EIR Desktop.

Este módulo se ejecuta ANTES que el código de la aplicación cuando se arranca
el .exe empaquetado. Su única misión es sembrar las variables de entorno que
activan las funciones de producción (kill-switches) ANTES de que cualquier
módulo las lea.

Sin este hook, EIR_DESKTOP_AUTOUPDATE nacería
en su default "0" (apagado) y la función real estaría inaccesible.
"""

import os

# Kill-switch de auto-update (D078). Debe ser "1" en releases reales.
os.environ.setdefault("EIR_DESKTOP_AUTOUPDATE", "1")

# Kill-switch de inferencia cloud (M-052). D085 lo excluía de aquí porque el
# gate de tier todavía no distinguía FREEMIUM de PAGO/ULTRA (D098 lo revirtió
# hoy: FREEMIUM también usa Cloud, con límite de volumen server-side, no de
# acceso) — ese riesgo ya no existe, así que el switch nace encendido en todo
# build real. Sin esto, ni el login ni el selector del sidebar activan nada
# (bug real cazado el 2026-08-03: "hola eir" seguía devolviendo el mock con
# una sesión freemium válida).
os.environ.setdefault("EIR_CLOUD_INFERENCE", "1")

# NOTA: EIR_TIER_LIMITS_ENABLED NO se siembra aquí — la decide el backend
# (Railway), no el cliente. Sembrarla aquí no activaría nada real.
# EIR_OPENCODE_ENABLED se deja en default "0" por seguridad.