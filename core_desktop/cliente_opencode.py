from __future__ import annotations

"""cliente_opencode.py · Adapter opencode serve como proveedor de inferencia.

Misión M-051 — prototipo A1 (BYOK local). El desktop EIR habla a `opencode serve`
levantado localmente (127.0.0.1) para obtener inferencia REAL de los providers
que el Soberano ya tiene configurados (Anthropic, Groq, NVIDIA, Cloudflare),
manteniendo el harness clínico de EIR como envoltura.

PRINCIPIOS (leyes que NO se violan):
  · L3 Validator-First: el LLM se llama DESPUES del ClinicalValidator en la ruta clínica.
  · L6 PHI nunca sale en claro: el historial se anonimiza ANTES de este adapter.
  · L2 Fail-closed: si opencode serve está caído, respuesta honesta, nunca LLM sin validar.
  · L5 Offline: si EIR_OPENCODE_ENABLED apagado, se cae a ClienteMockSandbox.
  · L10 Kill-switch leído en tiempo de llamada, default apagado.

El adapter mima la superficie `chat.completions.create(**kw)` que ya usa
``core/agente_loop._pedir_siguiente_herramienta`` — drop-in para el loop sin tocarlo.

NO es un loop de tools propio: EIR orquesta las tools con autonomía por paso;
opencode se llama SIN su loop agéntico (pura inferencia chat).
"""
import base64
import json
import os
import urllib.request
import urllib.error

from core_desktop.cliente_llm import _MockResponse, _MockMessage  # reusa la forma
from core_desktop.model_catalog import (
    Tier, Modo, Preferencia,
    resolver_modelo, resolver_tts,
    obtener_info_modelo, listar_modelos_disponibles,
)


# ─── Kill-switch (L10): leído en tiempo de llamada ────────────────────
#
# D097 · el selector BYOK del sidebar necesita encender esto SIN reiniciar el
# .exe (el env var solo se lee una vez al arranque del proceso). Este estado
# en memoria vive por proceso — el desktop es de un solo usuario (L1); si
# algún día soportara multi-sesión, esto tendría que volverse por-sesión, no
# global de módulo.
_MODO_INFERENCIA_SESION: str | None = None  # None | "cloud" | "byok"


def establecer_modo_inferencia(modo: str) -> None:
    """Fija la elección del doctor (selector del sidebar). ``modo`` es
    'cloud' o 'byok'; cualquier otro valor se ignora (fail-closed: no
    cambia nada ante un valor que no se reconoce)."""
    global _MODO_INFERENCIA_SESION
    if modo in ("cloud", "byok"):
        _MODO_INFERENCIA_SESION = modo


def modo_inferencia_actual() -> str:
    """Para que el endpoint de estado sea honesto sobre qué eligió el doctor."""
    return _MODO_INFERENCIA_SESION or "cloud"


# ─── 2026-08-03 · selector de modelo del sidebar (mismo patrón de switch
# de sesión que _MODO_INFERENCIA_SESION) ───────────────────────────────
_MODELO_ELEGIDO_SESION: str | None = None  # None = "auto" (deja decidir a resolver_modelo)


def establecer_modelo(modelo_id: str | None) -> None:
    """Fija el modelo elegido explícitamente por el doctor en el selector
    del sidebar. Un valor vacío/None vuelve a "auto" (deja decidir al
    catálogo por tier+paradigma). No valida contra el catálogo aquí —
    `resolver_cliente()` es quien decide en tiempo de llamada si el modelo
    guardado sigue siendo válido para el tier real de la sesión (fail-closed:
    si ya no aplica, cae a auto en vez de forzar un modelo fuera de tier)."""
    global _MODELO_ELEGIDO_SESION
    _MODELO_ELEGIDO_SESION = (modelo_id or "").strip() or None


def modelo_elegido_actual() -> str:
    """Para que el frontend sepa qué mostrar como seleccionado."""
    return _MODELO_ELEGIDO_SESION or "auto"


def _switch_activo() -> bool:
    # El doctor eligió BYOK explícitamente en esta sesión: enciende sin
    # esperar a un reinicio del proceso. Si eligió "cloud" o no ha elegido
    # nada, se respeta el comportamiento histórico (leer el env var, que
    # nace apagado — L10).
    if _MODO_INFERENCIA_SESION == "byok":
        return True
    return os.getenv("EIR_OPENCODE_ENABLED", "0").strip() in ("1", "true", "True", "yes")


def _cloud_switch_activo() -> bool:
    # Simétrico a _switch_activo() (BYOK, arriba): la elección explícita del
    # doctor en esta sesión manda sobre el env var, en ambas direcciones.
    # Antes esta función solo leía el env var, así que con
    # EIR_CLOUD_INFERENCE=1 (default desde el runtime hook, D098) elegir BYOK
    # en el sidebar no tenía ningún efecto — resolver_cliente() evaluaba cloud
    # primero y ganaba siempre. Ahora: BYOK explícito apaga cloud; cloud
    # explícito lo prende sin esperar reinicio; sin elección, cae al env var.
    if _MODO_INFERENCIA_SESION == "byok":
        return False
    if _MODO_INFERENCIA_SESION == "cloud":
        return True
    return os.getenv("EIR_CLOUD_INFERENCE", "0").strip() in ("1", "true", "True", "yes")


def _tier_permite_cloud() -> bool:
    """D098 · revierte D085: TODOS los tiers reales usan Cloud, incluido FREEMIUM.

    El tier ya NO decide si hay inferencia real — decide solo el volumen. El
    freno de FREEMIUM es el límite diario de su tier (exigido en
    routes_inference.py vía core.metered_billing, NO aquí — la cifra exacta
    es un parámetro de negocio del backend cloud, no de este cliente).
    Antes (D085) esta función excluía FREEMIUM por completo: un doctor sin
    plan de pago nunca tocaba inferencia real, sin importar qué instalara.
    Fail-closed se mantiene (L2): si no se puede leer NINGÚN tier de la
    sesión, no se asume acceso."""
    try:
        from core_desktop import sesion
        tier_str = (sesion.estado() or {}).get("tier", "freemium")
        return Tier(tier_str.lower()) in (Tier.FREEMIUM, Tier.PAGO, Tier.ULTRA)
    except Exception:
        return False


def resolver_cliente(rol: str = "odontologo", paradigma: str = "build"):
    """Devuelve el cliente LLM adecuado según los kill-switches (L10) y el
    tier real de la sesión (M-058, revertido por D098).

    1. EIR_CLOUD_INFERENCE=1 Y hay tier real de sesión (FREEMIUM/PAGO/ULTRA)
       → ClienteEirCloud (M-052). Sin sesión guardada → el cliente responde
       "inicia sesión" (L2/L5). El límite de FREEMIUM es de VOLUMEN (15
       consultas/día, core.metered_billing en el servidor), no de acceso.
    2. EIR_OPENCODE_ENABLED=1  → ClienteOpencode (BYOK local, M-051).
    3. Default → ClienteMockSandbox (offline).
    Llamada en tiempo de ejecución (no a nivel de módulo) — cumple L10.

    `paradigma` (2026-08-03): el mismo valor "plan"/"build" que ya viaja desde
    el toggle del sidebar hasta `agente_loop.ejecutar(paradigma=...)` — antes
    llegaba hasta cada runner y se perdía ahí, porque `resolver_cliente()` no
    lo recibía y siempre resolvía `Modo.BUILD` (línea de abajo). Son dos
    conceptos con el mismo nombre en español (el `Modo` de model_catalog.py
    para elegir MODELO, y el `paradigma` que gatea qué tools ve el LLM) que
    nunca se habían tocado entre sí.
    """
    if _cloud_switch_activo() and _tier_permite_cloud():
        from core_desktop.cliente_cloud import ClienteEirCloud
        return ClienteEirCloud(rol=rol)
    if not _switch_activo():
        from core_desktop.cliente_llm import ClienteMockSandbox
        return ClienteMockSandbox(rol=rol)
    
    # Obtener tier y modo de la sesión local
    try:
        from core_desktop import sesion
        estado_sesion = sesion.estado()
        tier_str = estado_sesion.get("tier", "freemium")
        # M-058 · Tier guarda sus VALORES en minúscula ("pago"/"ultra"); usar
        # .upper() aquí siempre lanzaba y caía en el except, así que el BYOK
        # local jamás detectaba el tier real de la sesión (bug preexistente,
        # cazado de paso al escribir _tier_permite_cloud con el mismo patrón).
        tier = Tier(tier_str.lower())
    except Exception:
        tier = Tier.FREEMIUM
    
    # El paradigma real (plan/build) decide el Modo de selección de modelo —
    # ya no queda fijo en BUILD sin importar qué eligió el doctor. Cualquier
    # valor que no sea "plan" cae a BUILD (fail-safe: build es el default
    # histórico, L17 — aditivo, no cambia comportamiento previo si no se pasa).
    modo = Modo.PLAN if paradigma == "plan" else Modo.BUILD

    # Selector de modelo del sidebar (2026-08-03): si el doctor eligió un
    # modelo explícito Y ese modelo sigue disponible para SU tier real, se
    # usa tal cual — nunca se confía a ciegas en lo que quedó guardado en
    # memoria de una sesión anterior con otro tier (fail-closed: si ya no
    # aplica, cae a "auto" y deja decidir a resolver_modelo() como siempre).
    modelo_elegido = _MODELO_ELEGIDO_SESION
    if modelo_elegido and modelo_elegido not in listar_modelos_disponibles(tier, modo):
        modelo_elegido = None

    url = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096").rstrip("/")
    return ClienteOpencode(base_url=url, rol=rol, tier=tier, modo=modo, modelo=modelo_elegido)


def resolver_modelo_tts(tier: Tier) -> str:
    """Resuelve el modelo Fish Audio TTS por tier."""
    return resolver_tts(tier)


# ─── ClienteOpencode: superficie drop-in ─────────────────────────────
class ClienteOpencode:
    """Adapter a `opencode serve` con la superficie `chat.completions.create`.

    No es un loop agéntico: EIR orquesta las tools. Este adapter solo pide
    el siguiente token del LLM y devuelve cualquier tool_call que el modelo
    formule (EIR las ejecutará tras el control de autonomía).
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, base_url: str, rol: str = "odontologo", modelo: str | None = None,
                 tier: Tier = Tier.FREEMIUM, modo: Modo = Modo.BUILD):
        self.base_url = base_url.rstrip("/")
        self.rol = rol
        self.tier = tier
        self.modo = modo
        self.modelo = modelo or resolver_modelo(tier, modo)
        # L10/L15: credenciales en tiempo de llamada, default vacío = sin auth
        self._auth_user = os.getenv("OPENCODE_SERVER_USER", "opencode").strip()
        self._auth_pass = os.getenv("OPENCODE_SERVER_PASSWORD", "").strip()
        self.chat = _ChatAdapter(self)

    def __repr__(self) -> str:
        return f"ClienteOpencode(base_url={self.base_url!r}, rol={self.rol!r}, modelo_habilitado=True)"

    def _post(self, url: str, payload: dict):
        """Overrideable para tests inyectables."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._auth_pass:
            token = base64.b64encode(f"{self._auth_user}:{self._auth_pass}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        req = urllib.request.Request(
            url, data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.DEFAULT_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))

    def _llamar_opencode(self, messages: list, model: str, *, temperature: float = 0.3,
                         max_tokens: int = 512, tools: list | None = None):
        """Llama a POST /session/:id/message de opencode serve.

        Si no hay sesión activa, crea una efímera (una por consulta — L1).
        Devuelve la parte de texto + eventuales tool_calls en formato EIR.
        """
        try:
            # 1) Crear sesión efímera
            sess = self._post(self.base_url + "/session", {})
            sess_id = sess.get("id") if isinstance(sess, dict) else None
            if not sess_id:
                return self._respuesta_honesta("motor no disponible: sesión no creada")
        except Exception as exc:
            return self._respuesta_honesta(f"motor no disponible: {exc} (L2)")

        try:
            body = {
                "model": None,
                "parts": [{"type": "text", "text": _messages_a_texto(messages)}],
            }
            resp = self._post(
                self.base_url + f"/session/{sess_id}/message", body
            )
        except urllib.error.HTTPError as exc:
            # opencode está vivo pero devolvió un error HTTP (4xx/5xx).
            # Leer el body del error para incluirlo en la respuesta honesta.
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                msg = detail.get("data", {}).get("message", str(exc))
            except Exception:
                msg = str(exc)
            return self._respuesta_honesta(f"motor error: {msg} (L2)")
        except urllib.error.URLError as exc:
            return self._respuesta_honesta(f"motor no disponible: {exc.reason} (L2)")
        except Exception as exc:
            return self._respuesta_honesta(f"motor no disponible: {exc} (L2)")
        finally:
            # intentar borrar la sesión efímera (L1: no reutilizar entre pacientes)
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        self.base_url + f"/session/{sess_id}",
                        method="DELETE",
                    ),
                    timeout=5,
                )
            except Exception:
                pass

        return self._parsear_respuesta_opencode(resp)

    def _motivo_amigable(self) -> str:
        """Traduce el estado del OpenCodeServerManager (M-059, ya calcula un
        motivo estable — opencode_no_instalado / proceso_crasheo /
        timeout_arranque / fallo_persistente / error_arranque) a un mensaje
        honesto y accionable en español. NUNCA expone el texto crudo de una
        excepción de socket (ej. WinError 10061) — mismo principio que
        ``opencode_server.py::estado()``: nunca None, nunca un traceback."""
        try:
            from core_desktop.opencode_server import get_server_manager
            estado = get_server_manager().estado() or {}
        except Exception:                     # noqa: BLE001
            estado = {}
        motivo = estado.get("motivo")
        if motivo == "opencode_no_instalado" or not estado.get("disponible", False):
            return ("OpenCode no está instalado o no está corriendo en este equipo. "
                    "Instálalo con: npm install -g opencode-ai — o cambia a "
                    "'Gratis en la nube' en el selector de motor.")
        if motivo == "proceso_crasheo":
            return ("El proceso de OpenCode se detuvo inesperadamente. Reinicia "
                    "EIR DR. Desktop o cambia a 'Gratis en la nube'.")
        if motivo in ("timeout_arranque", "fallo_persistente", "error_arranque"):
            return ("OpenCode no logró arrancar correctamente. Revisa la instalación "
                    "o cambia a 'Gratis en la nube'.")
        return "El motor local de OpenCode no respondió. Intenta de nuevo o cambia a 'Gratis en la nube'."

    def _respuesta_honesta(self, motivo: str) -> _MockResponse:
        """Fail-closed honesto (L2/L4): NO finge contenido del LLM. `motivo`
        (el diagnóstico técnico, puede llevar el texto crudo de una excepción)
        queda SOLO en el log — el mensaje visible nunca lo repite."""
        import logging
        logging.getLogger("eir_desktop.cliente_opencode").warning("BYOK falló: %s", motivo)
        return _MockResponse({
            "content": f"[EIR · motor offline] {self._motivo_amigable()}",
            "tool_calls": None,
        })

    def _parsear_respuesta_opencode(self, resp: dict) -> _MockResponse:
        """Convierte la respuesta de opencode (parts/lista) al formato EIR."""
        parts = resp.get("parts") or []
        texto = ""
        for p in parts:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content")
                if t:
                    texto += t
            elif isinstance(p, str):
                texto += p
        if not texto.strip():
            return self._respuesta_honesta("motor devolvió respuesta vacía")
        return _MockResponse({"content": texto, "tool_calls": None})


def _messages_a_texto(messages: list) -> str:
    """Convierte el historial de mensajes a texto plano para el `parts` de opencode.

    El historial YA debe venir anonimizado (L6) — este adapter confia en que el caller
    (runner / agente_loop) pasó por core/anonymizer antes. No anonimiza aquí de nuevo
    para no duplicar; pero si收到了 un mensaje con 'Paciente NAME', lo va a enviar.
    El control de PHI está en el runner, no en el adapter.
    """
    chunks = []
    for m in messages or []:
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = m.get("content", "")
            chunks.append(f"[{role}] {content}")
        elif isinstance(m, str):
            chunks.append(m)
    return "\n".join(chunks) if chunks else "(vacío)"


class _ChatAdapter:
    """Mima `cliente.chat`."""

    def __init__(self, padre: "ClienteOpencode"):
        self.completions = _CompletionsAdapter(padre)


class _CompletionsAdapter:
    """Mima `cliente.chat.completions.create(**kw)` — drop-in para agente_loop."""

    def __init__(self, padre: "ClienteOpencode"):
        self.padre = padre

    def create(self, *, model: str | None = None, messages: list | None = None,
               tools: list | None = None, tool_choice: str = "auto",
               temperature: float = 0.3, max_tokens: int = 512, **_):
        mod = model or self.padre.modelo
        return self.padre._llamar_opencode(
            messages=messages or [],
            model=mod,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )
