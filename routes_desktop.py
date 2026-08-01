"""routes_desktop.py · Blueprint aislado del sandbox desktop.

NO toca ``routes_narrativa.py`` (produccion). Define ``bp_desktop`` que:

  - POST /api/shell/conversar  · recibe {rol, mensaje, historial?} y
                                  despacha al runner_*.ejecutar_local()
                                  del rol correspondiente.
  - GET  /api/shell/roles      · lista los 4 roles disponibles (para el
                                  selector del frontend).
  - GET  /api/shell/contrato   · devueve el JSON contract documentado
                                  de eirdr.com/api/v1/inference futuro
                                  (util para inspeccionar el mock).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bp_desktop = Blueprint("bp_desktop", __name__)

_ROLES = ("odontologo", "recepcion", "laboratorio", "marketing")


def _resolver_runner(rol: str):
    """Import perezoso para no arrastrar dependencias en arranque."""
    from importlib import import_module
    try:
        base = "eir_desktop_v1.core_desktop"
        mod = import_module
    except ImportError:
        base = "core_desktop"
    runners = {
        "odontologo":  "runner_odontologo",
        "recepcion":   "runner_recepcion",
        "laboratorio": "runner_laboratorio",
        "marketing":   "runner_marketing",
    }
    if rol not in runners:
        raise ValueError(f"rol desconocido: {rol!r}")
    # primer intento como paquete completo; fallback a modulo relativo
    for prefix in ("eir_desktop_v1.core_desktop.", "core_desktop.", ""):
        try:
            return import_module(prefix + runners[rol])
        except ImportError:
            continue
    raise ImportError(f"no pude importar el runner para {rol!r}")


@bp_desktop.get("/api/shell/roles")
def roles():
    return jsonify(roles=list(_ROLES))


# ─── M-052 · sesión cloud + versión (el token nunca toca el webview) ─────
def _mod_sesion():
    try:
        from .core_desktop import sesion
        return sesion
    except ImportError:
        from core_desktop import sesion
        return sesion


def _mod_actualizador():
    try:
        from .core_desktop import actualizador
        return actualizador
    except ImportError:
        from core_desktop import actualizador
        return actualizador


@bp_desktop.post("/api/login")
def login_local():
    payload = request.get_json(silent=True) or {}
    res = _mod_sesion().login(payload.get("email", ""), payload.get("password", ""))
    status = 200 if res.get("ok") else 401
    return jsonify(res), status


@bp_desktop.post("/api/logout")
def logout_local():
    _mod_sesion().logout()
    return jsonify({"ok": True})


@bp_desktop.get("/api/sesion")
def sesion_local():
    return jsonify(_mod_sesion().estado())


@bp_desktop.get("/api/version")
def version_local():
    return jsonify(_mod_actualizador().estado())


@bp_desktop.get("/api/shell/contrato")
def contrato():
    try:
        from .core_desktop.cliente_llm import contrato_inference
    except ImportError:
        from core_desktop.cliente_llm import contrato_inference
    return jsonify(contrato_inference())


@bp_desktop.post("/api/shell/conversar")
def conversar():
    payload = request.get_json(silent=True) or {}
    rol = (payload.get("rol") or "").strip()
    mensaje = (payload.get("mensaje") or "").strip()
    historial = payload.get("historial") or []

    if rol not in _ROLES:
        return jsonify(error="rol_invalido", roles_aceptados=list(_ROLES),
                       code=400), 400
    if not mensaje:
        return jsonify(error="mensaje_vacio", code=400), 400

    try:
        runner = _resolver_runner(rol)
        resultado = runner.ejecutar_local(mensaje, historial, habilitar_loop=True)
        return jsonify(rol=rol, resultado=resultado)
    except Exception as exc:               # noqa: BLE001
        return jsonify(error="runner_fallo", detalle=str(exc),
                       rol=rol, code=500), 500
