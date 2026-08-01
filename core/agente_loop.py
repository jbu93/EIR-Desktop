"""
core/agente_loop.py · §M-028 · Loop agéntico con presupuesto
═══════════════════════════════════════════════════════════════════════════════
Cierra la **Etapa 2**: EIR deja de razonar en una sola pasada y pasa a **encadenar**
herramientas, **reintentar** las que fallan y **replantearse** cuando el resultado no
sirve o la frontera le niega el paso.

Decisión de arquitectura (D049): el bucle NO vive dentro del orquestador. Vive aquí,
aislado, con el cliente LLM **inyectable** — así se prueba en frío, sin red, y el bloque
de narración con streaming del chat (lo más frágil) queda intacto.

Tres candados, en este orden:
  1. **Kill-switch** — `EIR_AGENTE_LOOP_ENABLED` default 0. Apagado ⇒ una sola pasada,
     comportamiento idéntico al de siempre. El deploy no cambia producción por sí solo.
  2. **Frontera M-027** — se consulta en CADA paso, no una vez al inicio. Ninguna
     iteración puede escribir en la historia clínica ni firmar (D048).
  3. **Presupuesto** — tope de pasos y tope de reloj. Al agotarse responde con lo que
     tenga y lo **declara**; jamás inventa el faltante.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

MAX_PASOS_DEFAULT = 3
MAX_SEGUNDOS_DEFAULT = 25.0
REINTENTOS_POR_HERRAMIENTA = 1        # un reintento: cubre el fallo transitorio, no insiste


@dataclass
class Paso:
    """Un intento de herramienta dentro de la cadena. Auditable, sin PHI."""
    n: int
    herramienta: str
    argumentos: dict = field(default_factory=dict)
    ok: bool = False
    resumen: str = ""
    motivo: str = ""                  # por qué falló o fue negado (código estable)
    intentos: int = 0


@dataclass
class Resultado:
    pasos: list[Paso] = field(default_factory=list)
    tope_alcanzado: bool = False
    motivo_fin: str = "completo"      # completo | max_pasos | timeout | error_herramienta | sin_cliente
    segundos: float = 0.0
    # M-055 · si un paso se negó por `requiere_humano`, aquí queda la solicitud
    # de aprobación para que la UI pueda pedirla. None = nada que aprobar.
    aprobacion_pendiente: dict | None = None
    # M-057 · el plan estructurado (paradigma plan/auto) y el peso de riesgo
    # acumulado. None = sin plan (build o fase que no llegó a presentar plan).
    plan: dict | None = None
    peso_total: int = 0
    # M-058 · una tool pedida que este loop no ejecuta él mismo (delegada al
    # caller — típicamente el gateway cloud devolviéndosela al desktop).
    tool_call_delegado: dict | None = None

    @property
    def exitosos(self) -> list[Paso]:
        return [p for p in self.pasos if p.ok]

    def resumen_para_narrar(self) -> str:
        """Lo que se le pasa al LLM para que redacte. Incluye los fracasos: si una
        herramienta cayó, el modelo debe poder decirlo en vez de rellenar el hueco."""
        if not self.pasos:
            return "No se ejecutó ninguna herramienta."
        lineas = []
        for p in self.pasos:
            if p.ok:
                lineas.append(f"[{p.herramienta}] {p.resumen}")
            else:
                lineas.append(f"[{p.herramienta}] NO DISPONIBLE ({p.motivo}) — "
                              f"no inventes este dato, dilo con honestidad.")
        if self.tope_alcanzado:
            lineas.append("AVISO: se alcanzó el tope de pasos; la respuesta puede estar incompleta "
                          "y así debe declararse.")
        return "\n".join(lineas)


def habilitado_por_entorno() -> bool:
    """Nace apagado (invariante #4 del plan): encender es una decisión del Soberano."""
    return os.getenv("EIR_AGENTE_LOOP_ENABLED", "0").strip() in ("1", "true", "True", "si", "yes")


def _verificar_frontera(herramienta: str, modo: str) -> tuple[bool, str]:
    """Consulta la frontera de autonomía (M-027/D048).

    Vive como función de módulo A PROPÓSITO: es el punto exacto que la prueba de
    mutación del arnés reemplaza para comprobar que, sin ella, el loop quedaría
    autorizado a escribir en la historia clínica.

    Si el módulo de frontera no cargara, se niega (fail-closed)."""
    try:
        from core import autonomia
        return autonomia.puede_ejecutar(herramienta, modo=modo)
    except Exception:                 # noqa: BLE001 — sin frontera legible, no hay permiso
        return False, "frontera_no_disponible"


def _solicitar_aprobacion(herramienta: str, argumentos: dict) -> dict | None:
    """Emite una solicitud HITL para un paso negado por `requiere_humano` (M-055).

    NO autoriza nada: la frontera ya dijo que no. Solo construye la petición que
    la UI le muestra al doctor. Si la capa 2 no está disponible, devuelve None y
    el comportamiento es exactamente el de antes: negado y punto (fail-closed).
    """
    try:
        from core.harness.layer2_risk import hitl, risk_engine
        from core.harness.layer1_schema.validator import validar_tool_call
        from core import autonomia
    except Exception:                 # noqa: BLE001 — sin capa 2, no hay tercera vía
        return None

    try:
        args = validar_tool_call(herramienta, dict(argumentos or {}), _raiz_sandbox_l1())
    except Exception:                 # noqa: BLE001 — argumentos no benignos: nada que aprobar
        return None

    decl = autonomia.REGISTRO.get(herramienta)
    riesgo = risk_engine.evaluar_riesgo(herramienta, args, decl)
    solicitud = hitl.crear_solicitud(herramienta, args, riesgo)
    solicitud["args"] = args
    return solicitud


def _raiz_sandbox_l1():
    """Raíz del sandbox de las terminal tools, resuelta por su dueño (L10).

    Import tolerante: el desktop puede correr como paquete (repo en el path) o
    con ``eir_desktop_v1`` directamente en sys.path (bundle, arneses).
    """
    try:
        from eir_desktop_v1.core_desktop import terminal_tools
    except ImportError:
        from core_desktop import terminal_tools  # type: ignore
    return terminal_tools._raiz_sandbox()


def _verificar_aprobacion(herramienta: str, argumentos: dict, token: str) -> bool:
    """¿El humano aprobó ESTA llamada con ESTOS argumentos? (M-055)"""
    if not token:
        return False
    try:
        from core.harness.layer2_risk import hitl
        from core.harness.layer1_schema.validator import validar_tool_call
        args = validar_tool_call(herramienta, dict(argumentos or {}), _raiz_sandbox_l1())
        return bool(hitl.verificar_aprobacion(token, herramienta, args))
    except Exception:                 # noqa: BLE001 — fail-closed: ante la duda, no
        return False


def _auditar(paso: Paso) -> None:
    """Rastro por paso, SIN PHI: solo herramienta, resultado y motivo. Nunca el texto
    que escribió el doctor ni datos del paciente."""
    try:
        from core import audit_logger as audit
        audit.info("AGENTE_LOOP", herramienta=paso.herramienta[:40],
                   ok=paso.ok, motivo=paso.motivo[:60], intentos=paso.intentos)
    except Exception:                 # noqa: BLE001 — un log jamás tumba el loop
        pass


def _modo_plan():
    """El módulo del paradigma plan/build/auto (M-057), importado en tiempo de
    llamada: evita acoplar el import del loop al harness si no se usa."""
    from core import modo_plan
    return modo_plan


def _pedir_siguiente_herramienta(lc, modelo: str, mensajes: list,
                                 tools_schema: list | None = None) -> tuple[str | None, dict]:
    """Una vuelta al LLM: ¿qué herramienta sigue? None = el modelo dice que terminó.

    ``tools_schema`` (M-056): el catálogo de herramientas que se le ofrece al
    modelo. Si llega, se usa tal cual; si no, se cae al default del web
    (comportamiento histórico intacto).
    """
    if tools_schema is None:
        try:
            from core.shell_tools import TOOLS_SCHEMA
        except Exception:                 # noqa: BLE001
            TOOLS_SCHEMA = []
    else:
        TOOLS_SCHEMA = tools_schema
    try:
        resp = lc.chat.completions.create(
            model=modelo, messages=mensajes, tools=TOOLS_SCHEMA,
            tool_choice="auto", temperature=0.3, max_tokens=512,
        )
        choice = resp.choices[0].message
        tcs = getattr(choice, "tool_calls", None)
        if not tcs:
            return None, {}
        tc = tcs[0]
        try:
            args = json.loads(tc.function.arguments or "{}")
        except Exception:             # noqa: BLE001 — argumentos basura no tumban la cadena
            args = {}
        return tc.function.name, (args if isinstance(args, dict) else {})
    except Exception as e:            # noqa: BLE001
        print(f"[agente_loop] router: {type(e).__name__}: {e}")
        return None, {}


def _ejecutar_con_reintento(fn, argumentos: dict, paso: Paso) -> bool:
    """Ejecuta con 1 reintento. Devuelve True si alguna vez salió bien."""
    for intento in range(1 + REINTENTOS_POR_HERRAMIENTA):
        paso.intentos = intento + 1
        try:
            res = fn(**argumentos)
            paso.ok = bool(res.get("ok", True)) if isinstance(res, dict) else True
            paso.resumen = (res.get("resumen", "") if isinstance(res, dict) else str(res))[:2000]
            if paso.ok:
                return True
            paso.motivo = "herramienta_sin_resultado"
        except Exception as e:        # noqa: BLE001
            paso.ok = False
            paso.motivo = f"error_{type(e).__name__}"[:60]
            if intento < REINTENTOS_POR_HERRAMIENTA:
                time.sleep(0.05)      # backoff mínimo: cubre el parpadeo de red
    return False


def ejecutar(lc, modelo: str, mensaje: str, historial: list, *,
             ejecutores: dict | None = None,
             sistema: str = "",
             max_pasos: int | None = None,
             max_segundos: float | None = None,
             habilitado: bool | None = None,
             primera_herramienta: str | None = None,
             primeros_argumentos: dict | None = None,
             token_aprobacion: str | None = None,
             modo: str = "autonomo",
             tools_schema: list | None = None,
             paradigma: str = "build",
             max_pesos: int | None = None,
             plan: dict | None = None,
             token_plan: str | None = None,
             tools_delegables: set[str] | None = None) -> Resultado:
    """Corre la cadena de herramientas dentro del presupuesto y de la frontera.

    `lc` y `ejecutores` son inyectables: el arnés y los tests pasan dobles y así el
    bucle se verifica sin red y de forma determinista.

    `token_aprobacion` (M-055): aprobación humana de un solo uso emitida en un
    turno anterior. Levanta `requiere_humano` SOLO para la herramienta y los
    argumentos exactos que el doctor aprobó; para cualquier otra cosa no vale.

    `tools_schema` (M-056): el catálogo de herramientas que se le ofrece al LLM.
    Por defecto None = el del web (core.shell_tools). El desktop le pasa el del
    cable local (cable_local.construir_tools_schema), que añade las tools locales
    sin quitar las del web.

    `paradigma` (M-057): `build` (default, comportamiento histórico) · `plan`
    (solo-lectura + plan estructurado que el doctor aprueba) · `auto` (fase plan
    y, con `plan` + `token_plan` válido, fase build que ejecuta exactamente los
    pasos aprobados). `max_pesos` es la cota de riesgo acumulado de la fase plan
    (default interno 10). `plan`/`token_plan` alimentan la fase build de `auto`.

    `tools_delegables` (M-058): nombres de herramientas que ESTE loop no puede
    ejecutar — son del caller (p. ej. el gateway cloud recibiendo un tool_call
    para una tool que solo existe en el filesystem local del doctor). Si el
    modelo pide una de estas, el loop PARA de inmediato (no la marca
    herramienta_inexistente, no reintenta) y deja `Resultado.tool_call_delegado`
    con `{tool, args}` para que el caller decida qué hacer. Default None =
    comportamiento histórico intacto (L17).
    """
    inicio = time.time()
    r = Resultado()

    # ── M-057 · resolver el paradigma de ejecución (aditivo, L17) ──
    plan_fase = False
    build_desde_plan = False
    if paradigma == "build":
        pass                                        # comportamiento histórico
    elif paradigma == "plan":
        plan_fase = True
    elif paradigma == "auto":
        if plan:
            if token_plan and _modo_plan().verificar_aprobacion_plan(token_plan, plan):
                build_desde_plan = True
            else:
                r.motivo_fin = "plan_no_aprobado"   # fail-closed: sin aprobación, no se construye
                return r
        else:
            plan_fase = True
    else:
        r.motivo_fin = "paradigma_invalido"
        return r

    if ejecutores is None:
        try:
            from core.shell_tools import TOOLS_EXEC as ejecutores  # type: ignore
        except Exception:             # noqa: BLE001
            ejecutores = {}

    if lc is None:
        r.motivo_fin = "sin_cliente"
        return r

    encendido = habilitado_por_entorno() if habilitado is None else bool(habilitado)
    tope_pasos = 1 if not encendido else int(max_pasos or MAX_PASOS_DEFAULT)
    tope_seg = float(max_segundos if max_segundos is not None else MAX_SEGUNDOS_DEFAULT)

    # §M-043 · el mismo motor de presupuesto que usa el orquestador. Antes esta era
    # la TERCERA copia del recorte a mano (front + 2 ramas del orquestador): un
    # planificador que solo ve 3 turnos no puede encadenar herramientas con criterio.
    from core import contexto_chat
    mensajes = contexto_chat.construir_mensajes(
        sistema or ("Eres el planificador de EIR DR. Elige la siguiente herramienta "
                    "necesaria. Cuando ya tengas lo suficiente, responde SIN herramientas."),
        historial, mensaje)

    # §M-028 · los atajos DETERMINISTAS del orquestador (_quiere_normativa / _quiere_evidencia)
    # siguen mandando: si traen una herramienta, es el paso 1 y no se le consulta al LLM.
    # Encadenar no puede costarnos la doctrina de "normativa NUNCA va a PubMed".
    pendiente: tuple[str, dict] | None = (
        (primera_herramienta, dict(primeros_argumentos or {})) if primera_herramienta else None
    )

    # M-057 · en la fase plan el LLM SOLO ve tools de lectura + presentar_plan:
    # no se le tienta con lo que no puede ejecutar (defensa en profundidad).
    if plan_fase:
        tools_schema = _modo_plan().esquema_plan(tools_schema)

    # M-057 · fase build de `auto`: ejecuta EXACTAMENTE los pasos del plan que el
    # doctor aprobó (token_plan ya verificado arriba). Sin consultar al LLM: el
    # plan aprobado ES el plan de trabajo; re-preguntar lo desvirtuaría.
    if build_desde_plan:
        peso_usado = 0
        n = 0
        for paso_plan in (plan or {}).get("pasos", []):
            n += 1
            herramienta = paso_plan.get("tool", "")
            argumentos = dict(paso_plan.get("args") or {})
            paso = Paso(n=n, herramienta=herramienta, argumentos=argumentos)
            # Frontera por paso (D048) + M-055 tercera vía: las escrituras
            # irreversibles siguen exigiendo su propio HITL (default L2).
            permitido, motivo = _verificar_frontera(herramienta, modo)
            if not permitido and motivo == "requiere_humano":
                if _verificar_aprobacion(herramienta, argumentos, token_aprobacion or ""):
                    permitido, motivo = True, "aprobado_por_humano"
                elif r.aprobacion_pendiente is None:
                    r.aprobacion_pendiente = _solicitar_aprobacion(herramienta, argumentos)
            if not permitido:
                paso.ok, paso.motivo = False, motivo
                r.pasos.append(paso)
                _auditar(paso)
                continue
            fn = (ejecutores or {}).get(herramienta)
            if fn is None:
                paso.ok, paso.motivo = False, "herramienta_inexistente"
                r.pasos.append(paso)
                _auditar(paso)
                continue
            _ejecutar_con_reintento(fn, argumentos, paso)
            r.pasos.append(paso)
            _auditar(paso)
            peso_usado += int(paso_plan.get("peso", 0))
        r.peso_total = peso_usado
        r.motivo_fin = "plan_aprobado_ejecutado" if r.pasos else "plan_vacio"
        r.segundos = round(time.time() - inicio, 3)
        return r

    n = 0
    while n < tope_pasos:
        if time.time() - inicio > tope_seg:
            r.tope_alcanzado, r.motivo_fin = True, "timeout"
            break

        if pendiente is not None:
            herramienta, argumentos = pendiente
            pendiente = None
        else:
            herramienta, argumentos = _pedir_siguiente_herramienta(
                lc, modelo, mensajes, tools_schema)
        if not herramienta:
            r.motivo_fin = "completo"          # el modelo dio la cadena por terminada
            break

        n += 1
        paso = Paso(n=n, herramienta=herramienta, argumentos=argumentos)

        # M-058 · la tool pedida no es de este loop (p. ej. el servidor cloud
        # recibiendo un tool_call que solo existe en el filesystem local del
        # doctor). Para de inmediato: NO se ejecuta, NO se marca inexistente,
        # NO se reintenta — se devuelve tal cual al caller.
        if tools_delegables and herramienta in tools_delegables:
            r.tool_call_delegado = {"tool": herramienta, "args": dict(argumentos or {})}
            r.motivo_fin = "tool_delegada"
            break

        # M-057 · fase plan: si el modelo presenta el plan, se valida
        # determinísticamente y se corta la fase. `presentar_plan` es interna del
        # loop: no pasa por la frontera (no está declarada en la matriz a propósito).
        if plan_fase and herramienta == _modo_plan().PLAN_TOOL:
            ok_plan, motivo_plan, plan_obj = _modo_plan().validar_plan(
                argumentos.get("plan"),
                max_pesos if max_pesos is not None else _modo_plan().PESOS_DEFAULT,
                _raiz_sandbox_l1())
            if ok_plan and plan_obj:
                r.plan = plan_obj
                r.peso_total = plan_obj["peso_total"]
                r.aprobacion_pendiente = _modo_plan().crear_solicitud_plan(plan_obj)
                r.motivo_fin = "plan_listo"
                break
            paso.ok, paso.motivo = False, motivo_plan or "plan_invalido"
            r.pasos.append(paso)
            _auditar(paso)
            # El plan inválido VUELVE al contexto: el modelo lo corrige y repite.
            mensajes.append({"role": "assistant",
                             "content": f"[plan_invalido] {paso.motivo}"})
            mensajes.append({"role": "user",
                             "content": "El plan no pasó la validación. Corrígelo y "
                                        "preséntalo de nuevo."})
            continue

        # ── candado 2 · la frontera se consulta en CADA paso (D048) ──
        if plan_fase:
            # M-057 · fase plan: frontera compuesta (solo-lectura ∩ frontera normal).
            permitido, motivo = _modo_plan().puede_ejecutar_plan(herramienta, modo)
        else:
            permitido, motivo = _verificar_frontera(herramienta, modo)

        # M-055 · tercera vía para `requiere_humano`: si el doctor ya aprobó
        # ESTA llamada con ESTOS argumentos, el paso procede. La frontera NO se
        # toca (sigue diciendo que no por sí sola): lo que cambia es que existe
        # una autorización humana explícita, de un solo uso, que la levanta.
        if not permitido and motivo == "requiere_humano":
            if _verificar_aprobacion(herramienta, argumentos, token_aprobacion or ""):
                permitido, motivo = True, "aprobado_por_humano"
            elif r.aprobacion_pendiente is None:
                r.aprobacion_pendiente = _solicitar_aprobacion(herramienta, argumentos)

        if not permitido:
            paso.ok, paso.motivo = False, motivo
            r.pasos.append(paso)
            _auditar(paso)
            # El rechazo VUELVE al contexto: el modelo se replantea en vez de morir.
            mensajes.append({"role": "assistant",
                             "content": f"[herramienta_denegada] {herramienta}: {motivo}"})
            mensajes.append({"role": "user",
                             "content": "Esa herramienta no está permitida sin autorización "
                                        "humana. Continúa con otra o responde con lo que tengas."})
            continue

        fn = (ejecutores or {}).get(herramienta)
        if fn is None:
            paso.ok, paso.motivo = False, "herramienta_inexistente"
            r.pasos.append(paso)
            _auditar(paso)
            mensajes.append({"role": "user",
                             "content": f"La herramienta {herramienta} no existe. Usa otra."})
            continue

        _ejecutar_con_reintento(fn, argumentos, paso)
        r.pasos.append(paso)
        _auditar(paso)

        # El resultado (bueno o malo) alimenta la siguiente vuelta.
        mensajes.append({"role": "assistant",
                         "content": f"[resultado_{herramienta}] "
                                    f"{paso.resumen if paso.ok else 'FALLÓ: ' + paso.motivo}"})
        mensajes.append({"role": "user",
                         "content": "¿Necesitas otra herramienta? Si ya tienes lo necesario, "
                                    "responde sin herramientas."})
    else:
        # el while terminó por agotar los pasos, no por break
        if tope_pasos > 1:
            r.tope_alcanzado, r.motivo_fin = True, "max_pasos"

    if r.pasos and not r.exitosos and r.motivo_fin == "completo":
        r.motivo_fin = "error_herramienta"

    r.segundos = round(time.time() - inicio, 3)
    return r
