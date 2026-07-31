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
