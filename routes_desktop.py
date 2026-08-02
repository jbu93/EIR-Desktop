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
                # Disparador real: lanza el .bat desacoplado y cierra la app.
                # Sin esto el ciclo se quedaba a medias: el .bat quedaba escrito
                # en TEMP pero nadie lo ejecutaba, y la UI decia "reiniciando..."
                # para siempre.
                import subprocess, os
                bat_path = bat["bat_ruta"]
                # El entorno de un proceso PyInstaller congelado lleva marcas
                # (_PYI_APPLICATION_HOME_DIR, _PYI_ARCHIVE_FILE,
                # _PYI_PARENT_PROCESS_LEVEL) que le dicen al bootloader "ya
                # estas extraido". Si el .bat las hereda, el .exe relanzado se
                # las cree, no extrae su bundle y muere buscando el _MEI de
                # ESTA instancia, que para entonces ya se borro. El .bat las
                # limpia tambien por su cuenta; aqui se cortan en el origen.
                entorno = {k: v for k, v in os.environ.items()
                           if not k.startswith("_PYI_") and k != "_MEIPASS2"}
                subprocess.Popen(
                    ["cmd", "/c", bat_path],
                    env=entorno,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
                # Liberar el .exe: cerrar la app (pywebview)
                import sys
                if hasattr(sys, 'frozen') or 'pywebview' in sys.modules:
                    try:
                        import webview
                        for w in webview.windows:
                            w.destroy()
                    except Exception:
                        pass
                os._exit(0)
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


@bp_desktop.get("/api/opencode/estado")
def opencode_estado():
    """Diagnóstico honesto del motor BYOK local (M-059).

    El doctor nunca ve una consola (pywebview no la tiene): este endpoint es
    el único lugar donde puede enterarse de por qué su chat con OpenCode no
    responde. No requiere que el proceso esté vivo — si nunca arrancó, o si
    el módulo no se pudo importar, igual responde 200 con un motivo estable
    en vez de un 500 críptico.
    """
    try:
        from core_desktop.opencode_server import get_server_manager
    except ImportError:
        from .core_desktop.opencode_server import get_server_manager
    try:
        return jsonify(get_server_manager().estado())
    except Exception as exc:               # noqa: BLE001 — diagnóstico, nunca un 500
        return jsonify(disponible=False, motivo="error_interno",
                       reintentos=0, fallo_persistente=False, detalle=str(exc)[:200])


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
    # M-055 · aprobación humana emitida en un turno anterior. Vale una sola vez
    # y solo para la herramienta y los argumentos exactos que el doctor aprobó.
    token_aprobacion = (payload.get("token_aprobacion") or "").strip() or None
    # M-057 · paradigma de ejecución (plan/build/auto) + plan aprobado y su token.
    paradigma = (payload.get("paradigma") or "build").strip() or "build"
    max_pesos = payload.get("max_pesos")
    plan = payload.get("plan") or None
    token_plan = (payload.get("token_plan") or "").strip() or None

    if rol not in _ROLES:
        return jsonify(error="rol_invalido", roles_aceptados=list(_ROLES),
                       code=400), 400
    if not mensaje:
        return jsonify(error="mensaje_vacio", code=400), 400

    try:
        runner = _resolver_runner(rol)
        resultado = runner.ejecutar_local(mensaje, historial, habilitar_loop=True,
                                          token_aprobacion=token_aprobacion,
                                          paradigma=paradigma,
                                          max_pesos=max_pesos,
                                          plan=plan,
                                          token_plan=token_plan)
        return jsonify(rol=rol, resultado=resultado)
    except Exception as exc:               # noqa: BLE001
        return jsonify(error="runner_fallo", detalle=str(exc),
                       rol=rol, code=500), 500


@bp_desktop.post("/api/shell/aprobar")
def aprobar():
    """Aprobación humana de una tool call de alto riesgo (M-055 · HITL).

    El doctor aprueba un token emitido por ``agent_hooks``. Esta ruta NO ejecuta
    nada: solo verifica que el token es válido para la tool y los argumentos
    exactos que se van a ejecutar, y devuelve el token para que el siguiente
    turno reanude el paso. Fail-closed: cualquier fallo → 403 con motivo estable.
    """
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()
    tool = (payload.get("tool") or "").strip()
    args = payload.get("args") or {}

    if not token or not tool:
        return jsonify(error="solicitud_incompleta", code=400), 400

    try:
        from core.harness.layer2_risk.hitl import (
            AprobacionInvalida, huella_args,
        )
    except Exception as exc:               # noqa: BLE001 — sin canal HITL, se niega
        return jsonify(error="hitl_no_disponible", detalle=str(exc), code=503), 503

    # No se consume el token aquí: se comprueba que corresponde a estos
    # argumentos y se devuelve para que el paso lo gaste al ejecutarse. Así el
    # único consumo ocurre en el momento real de la ejecución.
    try:
        from core.harness.layer2_risk import hitl as _hitl
        _hitl.comprobar_aprobacion(token, tool, dict(args))
    except AprobacionInvalida as exc:
        return jsonify(error="aprobacion_invalida", motivo=exc.motivo, code=403), 403
    except Exception as exc:               # noqa: BLE001
        return jsonify(error="aprobacion_invalida", motivo=str(exc), code=403), 403

    return jsonify(aprobado=True, tool=tool, huella=huella_args(tool, dict(args)))
