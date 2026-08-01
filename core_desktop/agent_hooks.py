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

# Fase F · Capa 4 (egress): destino de red VERIFICADO en el código de cada
# tool, no el que el paso declare. Un ``host`` puesto por el paso vendría del
# propio modelo (es LLM-controlled): confiar en él dejaría que una tool call
# nombrara cualquier destino "de mentiras" solo para pasar el filtro. El
# destino real es una propiedad de la IMPLEMENTACIÓN de la tool (la constante
# BASE_URL de cada módulo), así que aquí se declara a mano y en corto, contra
# el código verificado — igual que D080 declaró la deuda de HITL en vez de
# esconderla, esto declara el alcance: solo las tools de las que se comprobó
# el host en su propio módulo entran a este mapa. Hoy: las 2 de Fish Audio.
#
# ``enviar_whatsapp_business`` está marcada ``requiere_red=True`` en
# plugin_registry pero NO aparece aquí a propósito: automa la app local de
# WhatsApp Business via UI automation (no hay socket propio del proceso ni un
# BASE_URL que verificar en su módulo). Meterle un host inventado violaría
# L4 ("no inventes este dato"); dejarla fuera de este mapa hace que
# ``_verificar_egress`` no tenga nada que comprobar y la deje pasar, tal como
# pasaba ANTES de esta fase (aditivo, ninguna tool existente empieza a
# fallar).
_HOSTS_DE_RED: dict[str, str] = {
    "hablar_fish_audio": "api.fish.audio",
    "listar_voces_fish_audio": "api.fish.audio",
}


def _buscar_autonomia_zonas() -> str | None:
    """Busca de forma defensiva el primer ``data/autonomia_zonas.json`` real.

    En lugar de suponer una profundidad fija de directorios (frágil si el
    módulo se mueve o se empaqueta con PyInstaller), escala hacia arriba
    desde este archivo buscando la carpeta ``data/`` del proyecto EIR.
    Devuelve ``None`` si no lo encuentra en ningún nivel conocido.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
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


def _normalizar_args(tool_name: str, paso: dict[str, Any]) -> dict[str, Any] | None:
    """Argumentos del paso pasados por la capa 1, si la herramienta la tiene.

    La capa 2 SIEMPRE evalúa sobre argumentos normalizados (L3). Si la tool no
    está en el alcance de la capa 1 (``tool_no_soportada`` — p. ej. las de
    app_control o las clínicas), se devuelven tal cual: la capa 1 no opina
    sobre ellas, y eso NO es un rechazo. Si la capa 1 sí la conoce y RECHAZA
    sus argumentos, se devuelve ``None``: no hay nada que aprobar porque la
    call ni siquiera es benigna.
    """
    args = dict(paso.get("args") or {})
    try:
        from core.harness.layer1_schema.validator import validar_tool_call
        from core.harness.layer1_schema.sanitizers import ToolCallInvalida
    except Exception:  # noqa: BLE001 — sin capa 1 disponible, se evalúa lo que hay
        return args

    try:
        from . import terminal_tools
    except ImportError:  # pragma: no cover - import como script suelto
        try:
            import terminal_tools  # type: ignore
        except ImportError:
            return args

    try:
        return validar_tool_call(tool_name, args, terminal_tools._raiz_sandbox())
    except ToolCallInvalida as exc:
        if exc.motivo == "tool_no_soportada":
            return args        # fuera del alcance de la capa 1, no rechazada por ella
        return None
    except Exception:  # noqa: BLE001 — tool ajena a la capa 1
        return args


def _evaluar_riesgo(tool_name: str, args: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    """Clasificación de la capa 2. Fail-closed si el motor no está disponible."""
    try:
        from core.harness.layer2_risk.risk_engine import evaluar_riesgo
    except Exception as exc:  # noqa: BLE001
        return {
            "nivel": "alto",
            "razones": [f"motor de riesgo no disponible ({exc}): se asume el peor caso"],
            "requiere_aprobacion": True,
        }
    return evaluar_riesgo(tool_name, args, decl)


def run_pre_hooks(rol: str, paso: dict[str, Any], sesion: dict[str, Any]) -> dict[str, Any]:
    """Valida la herramienta ANTES de ejecutarla. Fail-closed (L2).

    - Si el nombre está vacío: se permite (no hay herramienta que bloquear).
    - Si la matriz no se lee o la herramienta no está declarada: BLOQUEA.
    - Si la herramienta está declarada con ``requiere_humano=True``: no se
      ejecuta sin aprobación. Con un ``token_aprobacion`` válido para ESTOS
      argumentos → permitido; sin él → bloqueado, pero devolviendo una
      ``solicitud`` para que la UI pueda pedirla (M-055).

    Devuelve ``{"permitido": bool, "motivo": str|None}`` y, cuando hace falta
    una aprobación, además ``requiere_aprobacion``, ``motivo_aprobacion`` y
    ``solicitud`` (aditivo: quien no los lea ve el bloqueo de siempre, L17).
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

    bloqueo_egress = _verificar_egress(tool_name)
    if bloqueo_egress is not None:
        return bloqueo_egress

    if declaradas[tool_name].get("requiere_humano"):
        return _resolver_hitl(tool_name, paso, declaradas[tool_name])

    return {"permitido": True, "motivo": None}


def _verificar_egress(tool_name: str) -> dict[str, Any] | None:
    """Fase F · Capa 4: si la tool está marcada ``requiere_red`` en el
    plugin_registry Y tiene un destino de red VERIFICADO en ``_HOSTS_DE_RED``,
    lo comprueba contra ``data/egress_allowlist.json``. Fail-closed en el
    mismo estilo que la matriz de autonomía: si el verificador de egress no
    está disponible, se BLOQUEA (no se asume "sin egress, todo pasa").

    Devuelve ``None`` cuando no hay nada que bloquear (tool sin red, sin
    registro poblado, o sin un host verificado para ella — ver
    ``_HOSTS_DE_RED``): es la señal de "sigue con el resto del hook", ADITIVO
    con el contrato previo de ``run_pre_hooks``.
    """
    try:
        try:
            from . import plugin_registry
        except ImportError:  # pragma: no cover - import como script suelto
            import plugin_registry  # type: ignore
    except Exception:
        return None  # sin registro de plugins, no hay red que verificar (comportamiento previo intacto)

    if not plugin_registry.requiere_red(tool_name):
        return None

    host = _HOSTS_DE_RED.get(tool_name)
    if not host:
        return None  # requiere_red=True pero sin host verificado en el código: no hay destino que negar (L4)

    try:
        from core.harness.layer4_egress.egress import verificar_destino
    except Exception as exc:  # noqa: BLE001 — fail-closed: sin verificador, se bloquea
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}' bloqueada: el verificador de egress no está disponible ({exc}).",
        }

    permitido, motivo = verificar_destino(host, tool_name)
    if not permitido:
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}' bloqueada: destino de red '{host}' no permitido ({motivo}).",
        }
    return None


def _resolver_hitl(tool_name: str, paso: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    """Tercera vía para ``requiere_humano``: ni "sí" ni "no", sino "pide permiso".

    - Con ``token_aprobacion`` válido para ESTOS argumentos → permitido.
    - Sin token (o con uno que no cuadra) → sigue BLOQUEADO (fail-closed, L2)
      pero se emite una solicitud para que la UI pueda pedir la aprobación.

    El contrato histórico se conserva: ``permitido``/``motivo`` siguen ahí y
    quien no conozca los campos nuevos ve exactamente el bloqueo de antes (L17).
    """
    args = _normalizar_args(tool_name, paso)
    if args is None:
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}': argumentos rechazados por la capa 1.",
        }

    try:
        from core.harness.layer2_risk import hitl
    except Exception as exc:  # noqa: BLE001 — sin HITL, el bloqueo duro de siempre
        return {
            "permitido": False,
            "motivo": f"Herramienta '{tool_name}' requiere humano y el canal de "
                      f"aprobación no está disponible ({exc}).",
            "requiere_aprobacion": False,
        }

    token = paso.get("token_aprobacion") or ""
    if token:
        try:
            hitl.verificar_aprobacion(token, tool_name, args)
            return {"permitido": True, "motivo": None, "aprobado_por_humano": True}
        except hitl.AprobacionInvalida as exc:
            motivo_aprobacion = exc.motivo
        except Exception as exc:  # noqa: BLE001
            motivo_aprobacion = f"verificacion_fallida:{type(exc).__name__}"
    else:
        motivo_aprobacion = "sin_aprobacion"

    riesgo = _evaluar_riesgo(tool_name, args, decl)
    solicitud = hitl.crear_solicitud(tool_name, args, riesgo)
    return {
        "permitido": False,
        # El motivo sigue nombrando a requiere_humano: P15 del arnés del desktop
        # (D074) exige que el bloqueo sea atribuible al override, no a otra cosa.
        "motivo": f"Herramienta '{tool_name}' requiere la aprobación de un humano "
                  f"({motivo_aprobacion}).",
        "requiere_aprobacion": True,
        "motivo_aprobacion": motivo_aprobacion,
        "solicitud": solicitud,
    }


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
