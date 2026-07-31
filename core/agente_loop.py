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


def _auditar(paso: Paso) -> None:
    """Rastro por paso, SIN PHI: solo herramienta, resultado y motivo. Nunca el texto
    que escribió el doctor ni datos del paciente."""
    try:
        from core import audit_logger as audit
        audit.info("AGENTE_LOOP", herramienta=paso.herramienta[:40],
                   ok=paso.ok, motivo=paso.motivo[:60], intentos=paso.intentos)
    except Exception:                 # noqa: BLE001 — un log jamás tumba el loop
        pass


def _pedir_siguiente_herramienta(lc, modelo: str, mensajes: list) -> tuple[str | None, dict]:
    """Una vuelta al LLM: ¿qué herramienta sigue? None = el modelo dice que terminó."""
    try:
        from core.shell_tools import TOOLS_SCHEMA
    except Exception:                 # noqa: BLE001
        TOOLS_SCHEMA = []
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
             modo: str = "autonomo") -> Resultado:
    """Corre la cadena de herramientas dentro del presupuesto y de la frontera.

    `lc` y `ejecutores` son inyectables: el arnés y los tests pasan dobles y así el
    bucle se verifica sin red y de forma determinista.
    """
    inicio = time.time()
    r = Resultado()

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

    n = 0
    while n < tope_pasos:
        if time.time() - inicio > tope_seg:
            r.tope_alcanzado, r.motivo_fin = True, "timeout"
            break

        if pendiente is not None:
            herramienta, argumentos = pendiente
            pendiente = None
        else:
            herramienta, argumentos = _pedir_siguiente_herramienta(lc, modelo, mensajes)
        if not herramienta:
            r.motivo_fin = "completo"          # el modelo dio la cadena por terminada
            break

        n += 1
        paso = Paso(n=n, herramienta=herramienta, argumentos=argumentos)

        # ── candado 2 · la frontera se consulta en CADA paso (D048) ──
        permitido, motivo = _verificar_frontera(herramienta, modo)
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
