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

import threading

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


# ─── D078 · auto-update del .exe (kill-switch L10 + descarga + reinicio) ───

_ESTADO_UPDATE: dict = {"fase": "inactivo", "progreso": 0, "detalle": ""}
_LOCK_UPDATE = threading.Lock()


def _mod_actualizador_auto():
    mod = _mod_actualizador()
    if not mod._auto_update_activo():
        return None
    return mod


@bp_desktop.post("/api/shell/actualizar")
def actualizar():
    """Inicia la descarga+verificación del .exe nuevo (D078).

    Fail-closed (L2/L10): si el kill-switch EIR_DESKTOP_AUTOUPDATE está
    apagado, responde 403 sin tocar nada. Exige hay_actualizacion (L4).
    """
    mod = _mod_actualizador_auto()
    if mod is None:
        return jsonify(ok=False, error="kill_switch_apagado", code=403), 403

    est = mod.estado(force=True)
    if not est.get("ok") or not est.get("hay_actualizacion"):
        return jsonify(ok=False, error="sin_actualizacion",
                       detalle=est.get("motivo") or "sin_release", code=409), 409

    with _LOCK_UPDATE:
        if _ESTADO_UPDATE.get("fase") in ("descargando", "aplicando"):
            return jsonify(ok=True, fase=_ESTADO_UPDATE["fase"],
                           progreso=_ESTADO_UPDATE["progreso"]), 200

        url = est.get("url_windows") or ""
        sha = est.get("sha256") or ""
        version = est.get("disponible") or ""
        if not url or not sha:
            return jsonify(ok=False, error="sin_metadata", code=409), 409

        _ESTADO_UPDATE.update({"fase": "descargando", "progreso": 0, "detalle": ""})

        def _tarea():
            def _cb_progreso(pct):
                with _LOCK_UPDATE:
                    _ESTADO_UPDATE.update({"progreso": int(pct)})
            try:
                r = mod.descargar_y_verificar(url, sha, version,
                                              on_progreso=_cb_progreso)
                if not r.get("ok"):
                    with _LOCK_UPDATE:
                        _ESTADO_UPDATE.update({"fase": "error",
                                               "detalle": r.get("motivo") or "error"})
                    return
                bat = mod.generar_script_reemplazo(r["ruta"], _exe_actual())
                if not bat.get("ok"):
                    with _LOCK_UPDATE:
                        _ESTADO_UPDATE.update({"fase": "error",
                                               "detalle": bat.get("motivo") or "error"})
                    return
                with _LOCK_UPDATE:
                    _ESTADO_UPDATE.update({"fase": "aplicando", "progreso": 100,
                                           "bat_ruta": bat["bat_ruta"]})
            except Exception as exc:              # noqa: BLE001
                with _LOCK_UPDATE:
                    _ESTADO_UPDATE.update({"fase": "error", "detalle": str(exc)})

        threading.Thread(target=_tarea, daemon=True).start()
        return jsonify(ok=True, fase="descargando", progreso=0), 202


def _exe_actual() -> str:
    """Ruta del .exe en ejecución (sys.executable dentro del bundle PyInstaller;
    en dev es el python.exe)."""
    import sys
    return sys.executable


@bp_desktop.get("/api/shell/actualizar/progreso")
def progreso_actualizar():
    with _LOCK_UPDATE:
        est = dict(_ESTADO_UPDATE)
    return jsonify(ok=True, **est)


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
