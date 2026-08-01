"""workflow_orchestrator.py · Motor de workflows multi-turno del desktop EIR.

Orquesta secuencias de herramientas compuestas (``WorkflowDef``) ejecutando
cada paso con los hooks de ciclo de vida de ``agent_hooks``:

  1. ``run_pre_hooks``   → fail-closed: herramienta no autorizada = interrumpe.
  2. ``obtener``         → resuelve el ejecutor en el registro de plugins.
  3. ejecución           → con captura de excepciones.
  4. ``run_post_hooks``  → detecta ``needs_clarification`` (la tool devolvió
                           error y no se puede continuar sin aclaración).
  5. ``run_error_hook``  → degradación elegante sin inventar datos (L2/L4).

El resultado respeta el contract que ya consume ``desktop_chat.js``
(``pasos``, ``resumen``, estado), más los campos nuevos ``estado`` y
``needs_clarification`` para que la UI pueda pintar la traza en vivo.

Los imports a ``plugin_registry`` y ``agent_hooks`` usan el patrón de
fallback multi-prefijo del proyecto (misma convención que ``routes_desktop``
y los ``runner_*``) para correr como paquete o como script.
"""
from __future__ import annotations

from typing import Any

_PREFIJOS = ("eir_desktop_v1.core_desktop.", "core_desktop.", "")


def _importar_hermano(nombre_modulo: str) -> Any:
    """Importa un módulo hermano priorizando el MISMO paquete (misma instancia).

    Prioridad 1: import relativo (``.plugin_registry``) para garantizar que el
    registro que ve este orquestador es el MISMO objeto ``dict`` que pueblan
    los ``runner_*`` / el arnés. Si este módulo se importó como
    ``core_desktop.X`` y alguien registra vía ``core_desktop.plugin_registry``,
    ambos apuntan a la misma instancia.

    Prioridad 2: fallback multi-prefijo (patrón de ``routes_desktop``) para
    correr como script/paquete cuando el relativo no es posible.
    """
    import importlib
    if __package__:
        try:
            return importlib.import_module(f".{nombre_modulo}", __package__)
        except Exception:  # noqa: BLE001 — cae al fallback absoluto
            pass
    for prefijo in _PREFIJOS:
        try:
            return importlib.import_module(prefijo + nombre_modulo)
        except ImportError:
            continue
    raise ImportError(
        f"No se pudo importar 'eir_desktop_v1.core_desktop.{nombre_modulo}' "
        f"(probado con prefijos: {_PREFIJOS!r})."
    )


class WorkflowDef:
    """Definición declarativa de una secuencia de pasos.

    Cada paso es un dict::

        {"tool": "ver_protocolo", "args": {"especialidad": "endodoncia"}}
    """

    def __init__(self, pasos: list[dict[str, Any]], nombre: str = "workflow"):
        if not isinstance(pasos, list) or not pasos:
            raise ValueError("Un WorkflowDef necesita al menos un paso.")
        self.nombre = str(nombre)
        self.pasos = pasos

    def __repr__(self) -> str:  # pragma: no cover - solo depuración
        return f"WorkflowDef({self.nombre!r}, pasos={len(self.pasos)})"


class WorkflowOrchestrator:
    """Ejecutor de workflows que aplica hooks de ciclo de vida a cada paso."""

    def __init__(self) -> None:
        # Imports diferidos (patrón del repo) para no romper el arranque si
        # los módulos hermanos aún no son importables en el PYTHONPATH.
        self._hooks = _importar_hermano("agent_hooks")
        self._registro = _importar_hermano("plugin_registry")

    def _pre(self, rol: str, paso: dict[str, Any], sesion: dict[str, Any]) -> dict[str, Any]:
        return self._hooks.run_pre_hooks(rol, paso, sesion)

    def _post(self, rol: str, paso: dict[str, Any], resultado: Any, sesion: dict[str, Any]) -> dict[str, Any]:
        return self._hooks.run_post_hooks(rol, paso, resultado, sesion)

    def _error(self, rol: str, paso: dict[str, Any], error: Exception, sesion: dict[str, Any]) -> str:
        return self._hooks.run_error_hook(rol, paso, error, sesion)

    def ejecutar(self, wf: WorkflowDef, sesion: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ejecuta el workflow paso a paso aplicando hooks de ciclo de vida.

        Parameters
        ----------
        wf : WorkflowDef
            Secuencia de pasos a ejecutar.
        sesion : dict | None
            Contexto de sesión (rol, offline, etc.).

        Returns
        -------
        dict
            Claves compatibles con el contract de ``desktop_chat.js``:
            ``estado`` (``completo`` | ``needs_clarification`` | ``error``),
            ``pasos`` (list[dict] con ``herramienta``/``ok``/``resumen``),
            ``resumen`` (str) y ``needs_clarification`` (str|None).
        """
        sesion = dict(sesion or {})
        rol = sesion.get("rol", "odontologo")
        traza: list[dict[str, Any]] = []

        for paso in wf.pasos:
            tool_name = paso.get("tool")
            args = dict(paso.get("args") or {})

            # Los args viajan al hook: la capa 2 ata la aprobación humana a los
            # argumentos EXACTOS que se van a ejecutar (M-055). El token, si el
            # doctor ya aprobó, viaja en el propio paso.
            paso_hook = {"herramienta": tool_name, "args": args}
            if paso.get("token_aprobacion"):
                paso_hook["token_aprobacion"] = paso["token_aprobacion"]

            pre = self._pre(rol, paso_hook, sesion)
            if not pre["permitido"]:
                # Fail-closed intacto: sin aprobación no se ejecuta nada. La
                # diferencia es que ahora el paso puede REANUDARSE con el token
                # en vez de morir sin salida.
                if pre.get("requiere_aprobacion"):
                    return {
                        "estado": "requiere_aprobacion",
                        "pasos": traza,
                        "resumen": f"El paso '{tool_name}' necesita tu aprobación.",
                        "needs_clarification": None,
                        "aprobacion": pre["solicitud"],
                    }
                return self._terminar("error", traza, f"Workflow interrumpido: {pre['motivo']}")

            handler = self._registro.obtener(tool_name)
            if handler is None:
                return self._terminar(
                    "error",
                    traza,
                    f"Workflow interrumpido: la herramienta '{tool_name}' no está registrada.",
                )

            try:
                resultado = handler(**args)
                post = self._post(rol, {"herramienta": tool_name}, resultado, sesion)
                traza.append({
                    "herramienta": tool_name,
                    "ok": not post["needs_clarification"],
                    "resumen": str(resultado),
                })
                if post["needs_clarification"]:
                    return {
                        "estado": "needs_clarification",
                        "pasos": traza,
                        "resumen": f"El paso '{tool_name}' requiere aclaración.",
                        "needs_clarification": post["motivo"],
                    }
            except Exception as exc:  # noqa: BLE001 — degradación elegante fail-closed
                msg = self._error(rol, {"herramienta": tool_name}, exc, sesion)
                traza.append({"herramienta": tool_name, "ok": False, "resumen": msg})
                return self._terminar("error", traza, msg)

        return self._terminar("completo", traza, self._armar_resumen(traza))

    @staticmethod
    def _armar_resumen(traza: list[dict[str, Any]]) -> str:
        """Compone el resumen de éxito a partir de lo que las tools devolvieron.

        El ``resumen`` de nivel superior es lo que ``desktop_chat.js`` narra al
        usuario (``desktop_app.js:292``): un texto fijo como "Workflow ejecutado
        correctamente." no refleja nada de lo ejecutado (F5 — promesa muda).
        Se une el resumen de cada paso, acotado por paso (L8) para no inyectar
        megabytes en el contexto del chat.
        """
        LIMITE_POR_PASO = 200
        partes: list[str] = []
        for p in traza:
            detalle = str(p.get("resumen", "")).strip()
            if len(detalle) > LIMITE_POR_PASO:
                detalle = detalle[:LIMITE_POR_PASO] + "..."
            partes.append(f"{p.get('herramienta')}: {detalle}")
        if not partes:
            return "Workflow ejecutado correctamente."
        return " | ".join(partes)

    @staticmethod
    def _terminar(estado: str, traza: list[dict[str, Any]], resumen: str) -> dict[str, Any]:
        """Conforma el dict de retorno único del orquestador."""
        return {
            "estado": estado,
            "pasos": traza,
            "resumen": resumen,
            "needs_clarification": None,
        }
