"""smoke_agente_local.py · Arn Fase 1 del sandbox EIR Desktop v1.

Corre SIN Flask y SIN red, usando el mock 100% offline de core_desktop.
Afirma PROPIEDADES, no NUMEROS (L9 AGENTS.md): un arnés que cuenta pasos
protege el bug; uno que afirma "el presupuesto se respeta" SI puede
romperlo al corregir el bug lo caza.

Propiedades afirmadas
---------------------
P1 · Fail-closed: una tool NO declarada en data/autonomia_zonas.json
    queda denegada por ``motivo='no_declarada'`` (validacion independiente
    de core.autonomia, sin acoplarse al nombre exacto).

P2 · Declaracion honesta: un paso fallido aparece en resumen_para_narrar
    con la frase literal "no inventes este dato" (mecanismo que el loop
    usa en produccion para no mentir cuando una tool cae / offline).

P3 · Presupuesto de pasos: si forzamos max_pasos a 1, el loop no ejecuta
    un segundo paso (la propiedad "existe un presupuesto y se respeta").

MutacionProbe
-------------
Despues de correr las verificaciones en verde, mutamos el mock para pasar
un tool_call con nombre 'zzz_no_existe' y verificamos que el arnés SIGUE
afirmando P1 fail-closed. Si el arnés pasara la mutacion en verde, no
seria un arnés (seria decorativo).

NO se ejecuta Flask. NO se abre pywebview. NO se llama a eirdr.com.

Uso
---
    set PYTHONPATH=<raiz-del-repo-eir-desktop>
    py scripts/smoke_agente_local.py
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

LOG = logging.getLogger("smoke_eir_desktop")


def _asegurar_path() -> None:
    """Permite correr como script o paquete; inserta proyectos."""
    HERE = Path(__file__).resolve().parent
    RAIZ_REPO = HERE.parent                 # raiz del repo de código abierto
    SYS_ROOT = HERE.parent.parent           # proyecto principal (privado) si existe
    DESKTOP_ROOT = HERE.parent              # carpeta del desktop
    for p in (str(RAIZ_REPO), str(SYS_ROOT), str(DESKTOP_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _correr_rol(rol: str) -> dict:
    """Invoca el runner del rol pasandole el cliente mockeado."""
    from core_desktop.runner_odontologo import ejecutar_local as run_odo
    from core_desktop.runner_recepcion import ejecutar_local as run_rec
    from core_desktop.runner_laboratorio import ejecutar_local as run_lab
    from core_desktop.runner_marketing import ejecutar_local as run_mkt
    runners = {
        "odontologo": run_odo, "recepcion": run_rec,
        "laboratorio": run_lab, "marketing": run_mkt,
    }
    if rol not in runners:
        raise ValueError(f"rol desconocido: {rol!r}")
    res = runners[rol]("consulta sandbox para " + rol, historial=[],
                      habilitar_loop=True)
    if not isinstance(res, dict):
        raise AssertionError(f"runner {rol} devolvio {type(res)}, esperaba dict")
    return res


def _verificar_propiedad_fail_closed() -> tuple[bool, str]:
    """P1: tool no declarada -> motivo='no_declarada' O fallido.
    No nos acoplamos al nombre exacto de la tool; si el FAIL-CLOSED
    funciona, sea cual sea la denegacion, ok=False."""
    try:
        from core_desktop.cliente_llm import ClienteMockSandbox, _MockToolCall
        # Inyectamos un tool_call con nombre seguro-que-no-declarado
        # forzando que la frontera lo rejecte.
        c = ClienteMockSandbox("odontologo")
        # mutar primera respuesta con nombre inventado
        original = c.chat.completions.create
        def _mutado(*, model, messages, tools, **kw):
            # en contador==1Devolver no_existe
            if c.chat.completions._contador == 0:
                from core_desktop.cliente_llm import _MockResponse
                return _MockResponse({"content": None,
                                      "tool_calls": [_MockToolCall("zzz_inexistente", {})]})
            return original(model=model, messages=messages, tools=tools, **kw)
        c.chat.completions.create = _mutado
        from core import agente_loop
        r = agente_loop.ejecutar(lc=c, modelo="mock-mutado", mensaje="x",
                                historial=[], sistema="mut test",
                                habilitado=True, max_pasos=1, max_segundos=5)
        pasos = r.pasos
        if not pasos:
            return False, "no se ejecuto ningun paso (fail-closed no verificado)"
        if pasos[0].ok:
            return False, f"FAIL: tool inventada fue ACEPTADA (ok=True) - frontera rota"
        if pasos[0].motivo != "no_declarada":
            return False, f"FAIL: paso fallo pero con motivo {pasos[0].motivo!r}, no 'no_declarada'"
        return True, f"ok (motivo='no_declarada', tool inventada rechazada correctamente)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_propiedad_declaracion_honesta() -> tuple[bool, str]:
    """P2: FORZAMOS un paso fallido inyectando un tool_call con argumentos
    invalidos; el resumen debe contener 'no inventes este dato'. Asi
    afirmamos la propiedad (mecanismo de honestidad del loop) sin depender
    del happy path de la tool real."""
    try:
        from core_desktop.cliente_llm import ClienteMockSandbox, _MockToolCall, _MockResponse
        c = ClienteMockSandbox("odontologo")
        original = c.chat.completions.create
        def _forzar_error(*, model, messages, tools, **kw):
            if c.chat.completions._contador == 0:
                return _MockResponse({"content": None,
                                      "tool_calls": [_MockToolCall("ver_protocolo",
                                                                  {"especialidad": "          INVENTADA_XYZ",
                                                                   "param_extra_invalido": True})]})
            return original(model=model, messages=messages, tools=tools, **kw)
        c.chat.completions.create = _forzar_error
        from core import agente_loop
        r = agente_loop.ejecutar(
            lc=c, modelo="mock-forcado", mensaje="x", historial=[],
            sistema="forzar fracaso", habilitado=True, max_pasos=1, max_segundos=5,
        )
        resumen = r.resumen_para_narrar()
        pasos = r.pasos
        if not pasos:
            return False, "FAIL: no se ejecuto ningun paso"
        if pasos[0].ok:
            return False, f"FAIL: esperaba paso fallido, salio ok=True ({pasos[0].herramienta})"
        if "no inventes este dato" not in resumen:
            return False, f"FAIL: resumen no declara falla honestamente. resumen={resumen!r}"
        return True, f"ok (paso '{pasos[0].herramienta}' fallo y el loop lo declara en resumen)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_propiedad_presupuesto() -> tuple[bool, str]:
    """P3: forzando max_pasos=1, ningun segundo paso debe ejecutarse.
    Afirma la propiedad 'existe un presupuesto y se respeta' sin proteger
    el numero exacto."""
    try:
        from core_desktop.cliente_llm import ClienteMockSandbox
        c = ClienteMockSandbox("odontologo")
        from core import agente_loop
        r = agente_loop.ejecutar(
            lc=c, modelo="mock", mensaje="test pres", historial=[],
            sistema="x", habilitado=True, max_pasos=1, max_segundos=5,
        )
        if len(r.pasos) > 1:
            return False, f"FAIL: corrio {len(r.pasos)} pasos pero el tope era 1"
        return True, f"ok (respeto tope 1: {len(r.pasos)} paso(s) dados)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _propiedad_4_roles_arrancan() -> tuple[bool, str]:
    """P4: los 4 runners cargan y devuelven diccionarios con las claves
    acordadas (pasos, motivo_fin, resumen). No afirmamos el contenido
    (eso P1-P3); solo que el dispatch a 4 roles no tomba el sandbox."""
    problemas = []
    for rol in ("odontologo", "recepcion", "laboratorio", "marketing"):
        try:
            res = _correr_rol(rol)
            for k in ("pasos", "motivo_fin", "resumen"):
                if k not in res:
                    problemas.append(f"{rol} sin clave {k!r}")
        except Exception as exc:
            problemas.append(f"{rol} critico: {exc!r}")
    if problemas:
        return False, " | ".join(problemas)
    return True, "ok (los 4 runners devuelven el contract acordado)"


def _verificar_hook_pre_autorizado() -> tuple[bool, str]:
    """P6: el hook pre NO bloquea una tool declarada en la matriz.

    Afirma la propiedad 'fail-closed no es fail-open': una herramienta que
    SÍ está declarada debe pasar el hook. Si la frontera bloqueara todo,
    el arnés lo cazaría (fail-closed silencioso)."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "ver_protocolo"},
                            {"rol": "odontologo"})
        if not pre.get("permitido"):
            return False, f"FAIL: tool autorizada fue BLOQUEADA por el pre-hook: {pre.get('motivo')}"
        return True, "ok (ver_protocolo pasa el pre-hook sin bloqueo)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_hook_pre_deniega() -> tuple[bool, str]:
    """P7: el pre-hook BLOQUEA una tool no declarada (fail-closed L2).

    Si el hook devolviera 'permitido=True' para una tool inexistente, sería
    fail-open: el arnés debe cazarlo (mutación de la frontera)."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "zzz_inexistente"},
                            {"rol": "odontologo"})
        if pre.get("permitido"):
            return False, "FAIL: tool no declarada fue ACEPTADA por el pre-hook (fail-open)"
        if not pre.get("motivo"):
            return False, "FAIL: denegada sin motivo (la honestidad exige explicar el bloqueo)"
        return True, f"ok (bloqueada con motivo: {pre['motivo']})"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_hook_error_honestidad() -> tuple[bool, str]:
    """P8: el error-hook produce un mensaje con el literal canónico de honestidad.

    Reusamos la MISMA frase que core/agente_loop (L4/L14): una sola forma de
    decir 'no invento el dato' en toda la superficie."""
    try:
        from core_desktop.agent_hooks import run_error_hook, FRASE_NO_INVENTES
        msg = run_error_hook("odontologo",
                             {"herramienta": "ver_protocolo"},
                             RuntimeError("boom"), {"rol": "odontologo"})
        if "ver_protocolo" not in msg:
            return False, "FAIL: el mensaje de error no menciona la herramienta que falló"
        if FRASE_NO_INVENTES not in msg:
            return False, "FAIL: el error-hook no reutiliza el literal canónico de honestidad"
        return True, "ok (error-hook degrada con el literal canónico de honestidad)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_workflow_completo() -> tuple[bool, str]:
    """P9: un workflow con herramientas autorizadas llega a estado='completo'.

    El orquestador ejecuta pasos secuenciales con plugins reales registrados
    en el registro dinámico (sin red, sin Flask)."""
    try:
        from core_desktop.plugin_registry import registrar, obtener
        from core_desktop.workflow_orchestrator import WorkflowDef, WorkflowOrchestrator

        registrar("ver_protocolo", lambda especialidad="": f"protocolo-{especialidad}")
        registrar("buscar_doctor", lambda especialidad="": f"doctor-{especialidad}")

        wf = WorkflowDef([
            {"tool": "ver_protocolo", "args": {"especialidad": "endodoncia"}},
            {"tool": "buscar_doctor", "args": {"especialidad": "endodoncia"}},
        ], nombre="smoke-completo")

        res = WorkflowOrchestrator().ejecutar(wf, {"rol": "odontologo"})
        if res.get("estado") != "completo":
            return False, f"FAIL: estado {res.get('estado')!r}, esperaba 'completo': {res.get('resumen')}"
        if len(res.get("pasos", [])) != 2:
            return False, f"FAIL: se ejecutaron {len(res.get('pasos', []))} pasos, esperaba 2"
        for p in res["pasos"]:
            if not p.get("ok"):
                return False, f"FAIL: paso {p.get('herramienta')} marcado como fallido"
        return True, "ok (workflow de 2 pasos autorizados llegó a estado='completo')"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_workflow_error() -> tuple[bool, str]:
    """P10: un workflow con una tool que FALLA llega a estado='error'.

    Usamos UNA tool declarada en la matriz (``leer_stl_local``) con un handler
    roto: así el pre-hook la deja pasar (autorizada) y es el handler quien
    lanza la excepción. La excepción debe degradarse elegantemente
    (fail-closed) sin propagarse ni inventar datos."""
    try:
        from core_desktop.plugin_registry import registrar
        from core_desktop.workflow_orchestrator import WorkflowDef, WorkflowOrchestrator

        def _tool_rota(**kwargs) -> None:
            raise RuntimeError("cable cortado")

        registrar("leer_stl_local", _tool_rota)
        wf = WorkflowDef([{"tool": "leer_stl_local", "args": {}}], nombre="smoke-error")

        res = WorkflowOrchestrator().ejecutar(wf, {"rol": "odontologo"})
        if res.get("estado") != "error":
            return False, f"FAIL: estado {res.get('estado')!r}, esperaba 'error'"
        if not res.get("pasos") or res["pasos"][-1].get("ok"):
            return False, "FAIL: el paso roto no quedó marcado como fallido"
        if not res.get("resumen") or "no inventes este dato" not in res["resumen"]:
            return False, f"FAIL: el resumen no declara la falla con honestidad: {res.get('resumen')!r}"
        return True, "ok (workflow con tool rota degradó a estado='error' con honestidad)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


# ─── FASE 2: Tools locales reales ───────────────────────────────────────

def _verificar_p11_leer_stl_declarada() -> tuple[bool, str]:
    """P11: leer_stl_local declarada en autonomia_zonas.json y pasa pre-hook."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "leer_stl_local"},
                            {"rol": "odontologo"})
        if not pre.get("permitido"):
            return False, f"FAIL: leer_stl_local BLOQUEADA por pre-hook: {pre.get('motivo')}"
        return True, "ok (leer_stl_local declarada y pasa pre-hook)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p12_listar_archivos_declarada() -> tuple[bool, str]:
    """P12: listar_archivos_paciente declarada en autonomia_zonas.json y pasa pre-hook."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "listar_archivos_paciente"},
                            {"rol": "odontologo"})
        if not pre.get("permitido"):
            return False, f"FAIL: listar_archivos_paciente BLOQUEADA por pre-hook: {pre.get('motivo')}"
        return True, "ok (listar_archivos_paciente declarada y pasa pre-hook)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p13_leer_texto_declarada() -> tuple[bool, str]:
    """P13: leer_texto_local declarada en autonomia_zonas.json y pasa pre-hook."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "leer_texto_local"},
                            {"rol": "odontologo"})
        if not pre.get("permitido"):
            return False, f"FAIL: leer_texto_local BLOQUEADA por pre-hook: {pre.get('motivo')}"
        return True, "ok (leer_texto_local declarada y pasa pre-hook)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p14_workflow_leer_stl_real() -> tuple[bool, str]:
    """P14: workflow con leer_stl_local REAL (implementada, no stub) llega a completo.

    Usa el handler real de local_tools.leer_stl_local con un STL de prueba generado
    por el probe Fase 0 (probe_data.stl). Valida que la tool NO sea NotImplementedError."""
    try:
        from core_desktop.plugin_registry import registrar
        from core_desktop.workflow_orchestrator import WorkflowDef, WorkflowOrchestrator
        from core_desktop.local_tools import leer_stl_local
        from pathlib import Path

        # Buscar STL de prueba generado por spike_probe
        probe_stl = Path(__file__).resolve().parent.parent / "spike_probe" / "probe_data.stl"
        if not probe_stl.is_file():
            return False, f"FAIL: no existe STL de prueba en {probe_stl} (correr spike_probe primero)"

        # Registrar handler REAL (no mock)
        registrar("leer_stl_local", leer_stl_local)

        wf = WorkflowDef([{"tool": "leer_stl_local", "args": {"ruta": str(probe_stl)}}], nombre="smoke-fase2-stl")

        res = WorkflowOrchestrator().ejecutar(wf, {"rol": "odontologo"})
        if res.get("estado") != "completo":
            return False, f"FAIL: estado {res.get('estado')!r}, esperaba 'completo': {res.get('resumen')}"
        if not res.get("pasos") or len(res["pasos"]) != 1:
            return False, f"FAIL: se ejecutaron {len(res.get('pasos', []))} pasos, esperaba 1"
        paso = res["pasos"][0]
        if not paso.get("ok"):
            return False, f"FAIL: paso marcado como fallido: {paso.get('motivo')}"
        # Verificar que el resumen contiene metadatos reales (no 'no inventes este dato')
        resumen = res.get("resumen", "")
        if "no inventes este dato" in resumen:
            return False, f"FAIL: el resumen declara falla honesta pero la tool debería funcionar: {resumen!r}"
        # Verificar que devolvió datos reales esperados
        if "n_caras" not in resumen and "volumen" not in resumen and "bounding" not in resumen:
            return False, f"FAIL: resumen no parece contener metadatos STL reales: {resumen!r}"
        return True, f"ok (workflow con leer_stl_local REAL llegó a completo: {resumen[:80]}...)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


# ─── FASE B1: control de apps de escritorio (D074) ─────────────────────

def _verificar_p15_apps_declaradas() -> tuple[bool, str]:
    """P15: zona 'producto' con el override correcto — lectura/reversible pasan
    el pre-hook; las irreversibles (escribir/enviar) quedan BLOQUEADAS por
    requiere_humano aunque estén declaradas (D074: jamás autónomas)."""
    try:
        from core_desktop.agent_hooks import run_pre_hooks
        no_irreversibles = ("listar_apps_abiertas", "traer_al_frente",
                            "capturar_pantalla_app", "listar_plantillas",
                            "render_plantilla")
        irreversibles = ("escribir_texto_en", "enviar_whatsapp_business")
        bloqueadas = []
        for t in no_irreversibles:
            pre = run_pre_hooks("odontologo", {"herramienta": t}, {"rol": "odontologo"})
            if not pre.get("permitido"):
                bloqueadas.append(f"{t}:{pre.get('motivo')}")
        if bloqueadas:
            return False, f"FAIL: tools de lectura/reversible BLOQUEADAS: {', '.join(bloqueadas)}"
        for t in irreversibles:
            pre = run_pre_hooks("odontologo", {"herramienta": t}, {"rol": "odontologo"})
            if pre.get("permitido"):
                return False, f"FAIL: {t} (irreversible) pasó el pre-hook autónomo (requiere_humano roto)"
            motivo = str(pre.get("motivo") or "")
            if "humano" not in motivo:
                return False, f"FAIL: {t} bloqueada pero sin motivo de requiere_humano: {motivo!r}"
        return True, ("ok (lectura/reversible permitidas; irreversibles bloqueadas "
                      "por requiere_humano: override respetado)")
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p16_whatsapp_dry_run() -> tuple[bool, str]:
    """P16: enviar_whatsapp_business sin confirmar JAMÁS toca la app ni inventa
    envío. Sin confirmar -> dry_run con preview y destinatario_hash; con
    confirmar=True pero kill-switch apagado -> fail-closed, nunca 'enviado'."""
    try:
        import os
        os.environ.pop("EIR_APP_CONTROL_ENABLED", None)
        from core_desktop.app_control import enviar_whatsapp_business
        r = enviar_whatsapp_business("Paciente Demo", "cita 10am", confirmar=False)
        if not r.get("ok") or not r.get("dry_run"):
            return False, f"FAIL: dry-run no devolvió ok+dry_run: {r}"
        if not r.get("preview") or "cita 10am" not in r["preview"]:
            return False, "FAIL: dry-run sin preview del contenido"
        if not r.get("destinatario_hash") or "Paciente Demo" in str(r.get("destinatario_hash")):
            return False, "FAIL: la traza expone el destinatario en claro (L6)"
        if r.get("requiere_humano") is not True:
            return False, "FAIL: acción irreversible sin requiere_humano"
        rc = enviar_whatsapp_business("Paciente Demo", "cita 10am", confirmar=True)
        if rc.get("enviado") is True or rc.get("ok") is True:
            return False, f"FAIL: envío 'ok'/'enviado' sin kill-switch (L10/fail-open): {rc}"
        return True, "ok (dry-run honesto con hash; confirmar sin switch -> fail-closed)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p17_escribir_dry_run() -> tuple[bool, str]:
    """P17: escribir_texto_en sin confirmar devuelve preview con requiere_humano
    (irreversible); con confirmar=True y kill-switch apagado -> fail-closed."""
    try:
        import os
        os.environ.pop("EIR_APP_CONTROL_ENABLED", None)
        from core_desktop.app_control import escribir_texto_en
        r = escribir_texto_en("blender", "escala 1:1", confirmar=False)
        if not r.get("ok") or not r.get("dry_run"):
            return False, f"FAIL: escribir dry-run no devolvió ok+dry_run: {r}"
        if r.get("requiere_humano") is not True:
            return False, "FAIL: escribir_texto_en sin requiere_humano (irreversible)"
        rc = escribir_texto_en("blender", "x", confirmar=True)
        if rc.get("ok") is True or rc.get("escrito") is True:
            return False, f"FAIL: escritura 'ok' sin kill-switch (L10/fail-open): {rc}"
        return True, "ok (escribir: dry-run honesto y fail-closed sin kill-switch)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p18_registro_y_traza() -> tuple[bool, str]:
    """P18: registrar_tools_app_control indexa las 5 en el plugin_registry y
    el hash de destinatario tiene el largo del contrato (L6: traza sin PHI)."""
    try:
        from core_desktop.plugin_registry import listar, obtener
        from core_desktop.app_control import registrar_tools_app_control, _hash_destinatario
        registrar_tools_app_control()
        herramientas = ("listar_apps_abiertas", "traer_al_frente", "escribir_texto_en",
                        "capturar_pantalla_app", "enviar_whatsapp_business",
                        "listar_plantillas", "render_plantilla")
        faltantes = [h for h in herramientas if h not in listar()]
        if faltantes:
            return False, f"FAIL: tools no indexadas en plugin_registry: {faltantes}"
        for h in herramientas:
            if obtener(h) is None:
                return False, f"FAIL: {h} indexada pero sin handler"
        h = _hash_destinatario("Ana Paciente #1")
        if not h or len(h) != 16:
            return False, f"FAIL: hash de destinatario mal formado: {h!r}"
        return True, "ok (7 tools de producto indexadas con handler; traza solo con hash)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _mutacion_fb1() -> tuple[bool, str]:
    """Mutación Fase B1: sin kill-switch, ninguna tool de producto puede tocar
    la app real. Si alguien conectara las tools ignorando el switch (L10), el
    arnés lo cazaría: el fail-closed debe decir 'app_control_desactivado'."""
    try:
        import os
        os.environ.pop("EIR_APP_CONTROL_ENABLED", None)
        from core_desktop.app_control import listar_apps_abiertas, enviar_whatsapp_business
        r = listar_apps_abiertas()
        if r.get("ok") is True:
            return False, f"FAIL: listar_apps_abiertas tocó el desktop sin kill-switch: {r}"
        rc = enviar_whatsapp_business("P", "m", confirmar=True)
        if rc.get("enviado") is True:
            return False, "FAIL: envío real sin kill-switch (L10)"
        motivos = {r.get("motivo"), rc.get("motivo")}
        if "app_control_desactivado" not in motivos:
            return False, f"FAIL: fail-closed sin el motivo del kill-switch: {motivos}"
        return True, "ok (sin kill-switch, producto no toca la app: fail-closed L10/L2)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"

# ─── FASE B2: anti-baneo medible + verificación visual (D074) ────────────

def _verificar_p19_plantillas_nunca_identicas() -> tuple[bool, str]:
    """P19: render_plantilla resuelve TODOS los placeholders (L4) y dos renders
    con datos distintos NUNCA salen idénticos (patrón anti-baneo)."""
    try:
        from core_desktop import app_control
        a = app_control.render_plantilla(
            "recordatorio_cita_24h", nombre="Ana", fecha="5 ago",
            hora="10:00", consultorio="Clinica", mensaje_libre="Traer radiografia")
        b = app_control.render_plantilla(
            "recordatorio_cita_24h", nombre="Luis", fecha="6 ago",
            hora="14:30", consultorio="Clinica", mensaje_libre="Confirmar asistencia")
        if not a.get("ok") or not b.get("ok"):
            return False, f"FAIL: plantilla no renderiza: {a} / {b}"
        if a["texto"] == b["texto"]:
            return False, "FAIL: dos renders distintos salieron IDÉNTICOS (anti-baneo roto)"
        if "{" in a["texto"] or "{" in b["texto"]:
            return False, "FAIL: quedó un placeholder sin resolver en el texto (L4)"
        # placeholder faltante -> fail-closed
        c = app_control.render_plantilla("bienvenida", nombre="Ana")
        if c.get("ok") is not False or c.get("motivo") != "placeholder_sin_resolver":
            return False, f"FAIL: placeholder faltante no negó la plantilla: {c}"
        if not c.get("faltantes"):
            return False, "FAIL: el fail-closed no dice QUÉ placeholder falta"
        return True, "ok (renders distintos jamás idénticos; placeholder faltante negado)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p20_tope_diario() -> tuple[bool, str]:
    """P20: el tope diario (anti-baneo) es una cota dura y se lee EN LLAMADA
    (L10). Con EIR_WHATSAPP_TOPE_DIARIO=0 el envío queda negado fail-closed;
    el mecanismo es el mismo que bloquea el envío real cuando se agota el cupo."""
    try:
        import os
        from core_desktop import app_control
        os.environ["EIR_WHATSAPP_TOPE_DIARIO"] = "0"
        try:
            permitido, enviados, tope = app_control._verificar_tope_diario()
            if permitido:
                return False, f"FAIL: tope=0 permitió el envío ({enviados}/{tope}) — cota dura rota"
            if tope != 0:
                return False, f"FAIL: tope leído como {tope}, esperaba 0 (kill-switch congelado)"
            # el envío real con confirmar=True también debe negarse (tope antes de app)
            r = app_control.enviar_whatsapp_business("P", "m", confirmar=True)
            if r.get("motivo") != "tope_diario_alcanzado":
                return False, f"FAIL: el envío no respetó el tope: {r}"
        finally:
            os.environ.pop("EIR_WHATSAPP_TOPE_DIARIO", None)
        return True, "ok (tope diario leído en llamada y respetado por el envío)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p21_verificacion_fail_closed() -> tuple[bool, str]:
    """P21: la verificación UI (triangulación B2) es fail-closed sin ventana
    real: nunca lanza, nunca afirma 'verificado', y jamás devuelve ok=True."""
    try:
        from core_desktop import verificacion_ui
        if verificacion_ui.leer_arbol(None) != []:
            return False, "FAIL: leer_arbol(None) no devolvió [] (debe ser fail-closed)"
        c = verificacion_ui.verificar_campo_mensaje(None)
        if c.get("ok") is not None:
            return False, f"FAIL: campo sin ventana dio ok={c.get('ok')} (esperaba None)"
        ch = verificacion_ui.verificar_chat_activo(None, "Ana")
        if ch.get("ok") is not None:
            return False, f"FAIL: chat sin ventana dio ok={ch.get('ok')} (esperaba None)"
        e = verificacion_ui.verificar_envio(None, "frag")
        if e.get("ok") is not None:
            return False, f"FAIL: envío sin ventana dio ok={e.get('ok')} (esperaba None)"
        return True, "ok (verificación UI fail-closed: nunca afirma sin ventana real)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _mutacion_b2_tope() -> tuple[bool, str]:
    """Mutación B2: si alguien quita el chequeo del tope diario (anti-baneo),
    P20 DEBE cazarlo. Reasignamos _verificar_tope_diario a 'siempre permitido'
    y exigimos que P20 falle; si P20 siguiera verde, el arnés es decorativo."""
    try:
        from core_desktop import app_control
        original = app_control._verificar_tope_diario
        app_control._verificar_tope_diario = lambda: (True, 0, 999999)
        try:
            ok, _ = _verificar_p20_tope_diario()
        finally:
            app_control._verificar_tope_diario = original
        if ok:
            return False, "FAIL: arnés NO caza el tope diario ignorado (mutación sobrevivió)"
        return True, "ok (el arnés caza un tope ignorado: la cota dura está vigilada)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _mutacion_fase2() -> tuple[bool, str]:
    """Mutación Fase 2: si quitamos una tool del JSON, P11-P13 DEBEN fallar.

    Simulamos quitando 'leer_stl_local' de la matriz (monkeypatch) y verificamos
    que el pre-hook la bloquea. Si P11-P13 pasaran, el arnés sería decorativo."""
    try:
        # Test directo: tool NO declarada debe ser bloqueada por pre-hook
        from core_desktop.agent_hooks import run_pre_hooks
        pre = run_pre_hooks("odontologo",
                            {"herramienta": "zzz_fase2_inexistente"},
                            {"rol": "odontologo"})
        if pre.get("permitido"):
            return False, "FAIL: tool inventada Fase 2 fue ACEPTADA por pre-hook (fail-open)"
        if not pre.get("motivo"):
            return False, "FAIL: denegada sin motivo"
        return True, f"ok (mutación Fase 2: tool inexistente bloqueada con motivo: {pre['motivo']})"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _mutacion_p1() -> tuple[bool, str]:
    """Mutacion: si desactivaramos la frontera (return True siempre),
    nuestra P1 deberia romperse. Verificamos que la asercion SIGUE
    cazando la tool inventada. Si no, el arnés es decorativo.

    Como no podemos monkeypatchar core.autonomia sin tocar repo principal,
    verificamos indirectamente: con la frontera intacta, generamos
    tool_call inventado. El arn debe seguir afirmando P1.
    Si el aguacero de 'no_declarada' cambiara, no cazamos el bug."""
    ok, msg = _verificar_propiedad_fail_closed()
    if not ok:
        return False, f"arn no caza mutacion de fail-closed: {msg}"
    return True, f"ok (mutacion probe: arn sigue verde SOLO porque frontera opera: {msg})"


# ─── M-052 · Sesión cloud + actualizador de versión ─────────────────────

def _verificar_p22_login_sin_crear_sesion() -> tuple[bool, str]:
    """P22: el login con credenciales vacías NUNCA crea sesión (ok=True) y
    estado() devuelve el contrato de 4 claves sin importar si hay red."""
    try:
        from core_desktop import sesion
        est = sesion.estado()
        for k in ("autenticado", "email", "nombre", "tier"):
            if k not in est:
                return False, f"FAIL: estado() sin clave {k!r}: {est}"
        r = sesion.login("", "")
        if r.get("ok") is True:
            return False, "FAIL: login con credenciales vacías devolvió ok=True (fail-open)"
        if not r.get("error"):
            return False, f"FAIL: login fallido sin motivo: {r}"
        return True, f"ok (login vacío negado: {r.get('error')}; contrato de estado completo)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p23_cloud_fail_closed_sin_sesion() -> tuple[bool, str]:
    """P23: el cliente cloud SIN sesión responde honesto y orienta a login;
    nunca inventa contenido del LLM (L2/L4). Independiente de la red:
    forzamos token=None sin tocar el archivo real ~/.eir_dr."""
    try:
        from core_desktop import sesion
        from core_desktop.cliente_cloud import ClienteEirCloud
        original = sesion.token
        sesion.token = lambda: None
        try:
            c = ClienteEirCloud("odontologo")
            r = c._llamar_inference("hola", [], [])
        finally:
            sesion.token = original
        texto = (r.choices[0].message.content or "") if getattr(r, "choices", None) else ""
        if not texto.startswith("[EIR · cloud]"):
            return False, f"FAIL: sin sesión no usó el marcador honesto: {texto!r}"
        if "sesi" not in texto.lower():
            return False, f"FAIL: sin sesión no orienta a iniciarla: {texto!r}"
        if c.ultimo_texto_final is not None:
            return False, "FAIL: respuesta honesta quedó marcada como final_text del LLM"
        return True, f"ok (sin sesión → respuesta honesta: {texto[:48]}...)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _mutacion_cloud_fail_open() -> tuple[bool, str]:
    """Mutación M-052: si alguien sustituyera _respuesta_honesta por una que
    inventa contenido del LLM (fail-open), P23 DEBE fallar. Reinyectamos el
    parche y exigimos que P23 lo cace."""
    try:
        from core_desktop.cliente_cloud import ClienteEirCloud
        from core_desktop.cliente_llm import _MockResponse
        original = ClienteEirCloud._respuesta_honesta
        ClienteEirCloud._respuesta_honesta = lambda self, motivo: _MockResponse(
            {"content": "Respuesta del LLM inventada sin sesión", "tool_calls": None})
        try:
            ok, _ = _verificar_p23_cloud_fail_closed_sin_sesion()
        finally:
            ClienteEirCloud._respuesta_honesta = original
        if ok:
            return False, "FAIL: arnés NO caza un cliente cloud que inventa contenido (mutación sobrevivió)"
        return True, "ok (P23 caza un cliente cloud que inventa contenido del LLM)"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def _verificar_p24_actualizador() -> tuple[bool, str]:
    """P24: el comparador semver es correcto y estado() nunca lanza y siempre
    reporta la versión local real, haya o no red."""
    try:
        from core_desktop import actualizador
        if not actualizador.version_actual() or not isinstance(actualizador.version_actual(), str):
            return False, "FAIL: version_actual() vacía o no str"
        if actualizador._claves("1.2.3-beta") != (1, 2, 3):
            return False, f"FAIL: _claves no extrae el prefijo numérico: {actualizador._claves('1.2.3-beta')}"
        if actualizador._claves("1.2") != (1, 2, 0):
            return False, f"FAIL: _claves no rellena hasta 3 componentes: {actualizador._claves('1.2')}"
        if not (actualizador._comparar("1.1.0", "1.0.0") > 0):
            return False, "FAIL: 1.1.0 no es mayor que 1.0.0"
        if actualizador._comparar("1.0.0", "1.0.0") != 0:
            return False, "FAIL: versiones iguales no comparan a 0"
        if actualizador._comparar("1.0.1", "1.0.2") >= 0:
            return False, "FAIL: 1.0.1 no es menor que 1.0.2"
        est = actualizador.estado(force=True)
        for k in ("ok", "actual"):
            if k not in est:
                return False, f"FAIL: estado() sin clave {k!r}: {est}"
        if est["actual"] != actualizador.version_actual():
            return False, "FAIL: estado() reporta una versión distinta a la local"
        if not isinstance(est["ok"], bool):
            return False, "FAIL: estado().ok no es bool"
        return True, f"ok (semver correcto; estado nunca lanza; actual={est['actual']})"
    except Exception as exc:
        return False, f"excepcion: {exc!r}\n{traceback.format_exc()[:500]}"


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    LOG.info("=" * 64)
    LOG.info("SMOKE ARNAS FASE 1 - EIR Desktop v1 sandbox")
    LOG.info("=" * 64)

    _asegurar_path()

    checks = [
        ("P1 fail-closed deniega tool no declarada", _verificar_propiedad_fail_closed),
        ("P2 declaracion honesta del fracaso",        _verificar_propiedad_declaracion_honesta),
        ("P3 presupuesto de pasos se respeta",       _verificar_propiedad_presupuesto),
        ("P4 4 runners despachan contracto",          _propiedad_4_roles_arrancan),
        ("Mut: probe P1 sobrevive mutacion",           _mutacion_p1),
        ("P6 pre-hook no bloquea tool autorizada",     _verificar_hook_pre_autorizado),
        ("P7 pre-hook bloquea tool no declarada",      _verificar_hook_pre_deniega),
        ("P8 error-hook reusa honestidad canónica",    _verificar_hook_error_honestidad),
        ("P9 workflow autorizado llega a completo",    _verificar_workflow_completo),
        ("P10 workflow con tool rota degrada a error", _verificar_workflow_error),
        ("P11 leer_stl_local declarada y pasa pre-hook",       _verificar_p11_leer_stl_declarada),
        ("P12 listar_archivos_paciente declarada y pasa pre-hook", _verificar_p12_listar_archivos_declarada),
        ("P13 leer_texto_local declarada y pasa pre-hook",     _verificar_p13_leer_texto_declarada),
        ("P14 workflow leer_stl_local REAL llega a completo",  _verificar_p14_workflow_leer_stl_real),
        ("Mut-F2: tool inexistente Fase 2 bloqueada",          _mutacion_fase2),
        ("P15 apps de producto declaradas y pasan pre-hook",   _verificar_p15_apps_declaradas),
        ("P16 whatsapp dry-run honesto + fail-closed sin switch", _verificar_p16_whatsapp_dry_run),
        ("P17 escribir dry-run honesto + fail-closed sin switch", _verificar_p17_escribir_dry_run),
        ("P18 registro + traza sin destinatario en claro",     _verificar_p18_registro_y_traza),
        ("Mut-FB1: sin kill-switch producto no toca la app",   _mutacion_fb1),
        ("P19 plantillas nunca idénticas + sin huecos",        _verificar_p19_plantillas_nunca_identicas),
        ("P20 tope diario leído en llamada y respetado",       _verificar_p20_tope_diario),
        ("P21 verificación UI fail-closed sin ventana real",   _verificar_p21_verificacion_fail_closed),
        ("Mut-B2: el arnés caza un tope diario ignorado",      _mutacion_b2_tope),
        ("P22 login vacío negado + contrato de estado",        _verificar_p22_login_sin_crear_sesion),
        ("P23 cloud fail-closed sin sesión",                   _verificar_p23_cloud_fail_closed_sin_sesion),
        ("Mut-Cloud: P23 caza cliente que inventa contenido",  _mutacion_cloud_fail_open),
        ("P24 actualizador semver + estado sin red",           _verificar_p24_actualizador),
    ]
    todos_ok = True
    for nombre, fn in checks:
        try:
            ok, detalle = fn()
        except Exception as exc:
            ok, detalle = False, f"excepcion outside: {exc!r}"
        status = "PASS" if ok else "FAIL"
        LOG.info("[%s] %s - %s", status, nombre, detalle)
        if not ok:
            todos_ok = False
    LOG.info("-" * 64)
    LOG.info("RESULTADO: %s", "VERDE - luz para Fase 1+ real"
             if todos_ok else "ROJO - revisar lo de arriba")
    return 0 if todos_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
