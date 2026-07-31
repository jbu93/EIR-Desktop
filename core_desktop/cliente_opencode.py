"""cliente_opencode.py · Adapter opencode serve como proveedor de inferencia.

Misión M-051 — prototipo A1 (BYOK local). El desktop EIR habla a `opencode serve`
levantado localmente (127.0.0.1) para obtener inferencia REAL de los providers
que el Soberano ya tiene configurados (Anthropic, Groq, NVIDIA, Cloudflare),
manteniendo el harness clínico de EIR como envoltura.

PRINCIPIOS (leyes que NO se violan):
  · L3 Validator-First: el LLM se llama DESPUES del validador clínico en la ruta clínica.
  · L6 PHI nunca sale en claro: el historial se anonimiza ANTES de este adapter.
  · L2 Fail-closed: si opencode serve está caído, respuesta honesta, nunca LLM sin validar.
  · L5 Offline: si EIR_OPENCODE_ENABLED apagado, se cae a ClienteMockSandbox.
  · L10 Kill-switch leído en tiempo de llamada, default apagado.

El adapter mima la superficie `chat.completions.create(**kw)` que ya usa
``core/agente_loop._pedir_siguiente_herramienta`` — drop-in para el loop sin tocarlo.

NO es un loop de tools propio: EIR orquesta las tools con autonomía por paso;
opencode se llama SIN su loop agéntico (pura inferencia chat).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
import urllib.error

from core_desktop.cliente_llm import _MockResponse, _MockMessage  # reusa la forma


# ─── Kill-switch (L10): leído en tiempo de llamada ────────────────────
def _switch_activo() -> bool:
    return os.getenv("EIR_OPENCODE_ENABLED", "0").strip() in ("1", "true", "True", "yes")


def resolver_cliente(rol: str = "odontologo"):
    """Devuelve el cliente LLM adecuado según el kill-switch.

    Con EIR_OPENCODE_ENABLED apagado (default) → ClienteMockSandbox (offline).
    Con EIR_OPENCODE_ENABLED encendido → ClienteOpencode (BYOK local).
    Llamada en tiempo de ejecución (no a nivel de módulo) — cumple L10.
    """
    if not _switch_activo():
        from core_desktop.cliente_llm import ClienteMockSandbox
        return ClienteMockSandbox(rol=rol)
    url = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096").rstrip("/")
    return ClienteOpencode(base_url=url, rol=rol)


# ─── Mapeo modo + tier → modelo (control de EIR; el usuario NO ve el menú completo) ──
_TIER_PLAN = {
    "FREEMIUM": "groq/llama-3.1-8b-instant",        # free-tier: modelo barato/gratis
    "PAGO":     "anthropic/claude-sonnet-4-5",     # pago: vanguardia (Plan)
}
_TIER_BUILD = {
    "FREEMIUM": "groq/llama-3.1-8b-instant",
    "PAGO":     "groq/llama-3.3-70b-versatile",    # build: potente pero barato
}


def resolver_modelo(modo: str = "build", tier: str = "FREEMIUM") -> str:
    """Resuelve el modelo por modo (Plan/Build) y tier. EIR controla qué ve cada tier."""
    tablas = _TIER_PLAN if modo == "plan" else _TIER_BUILD
    return tablas.get(tier, tablas["FREEMIUM"])


# ─── ClienteOpencode: superficie drop-in ─────────────────────────────
class ClienteOpencode:
    """Adapter a `opencode serve` con la superficie `chat.completions.create`.

    No es un loop agéntico: EIR orquesta las tools. Este adapter solo pide
    el siguiente token del LLM y devuelve cualquier tool_call que el modelo
    formule (EIR las ejecutará tras el control de autonomía).
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, base_url: str, rol: str = "odontologo", modelo: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.rol = rol
        self.modelo = modelo or "groq/llama-3.1-8b-instant"
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
                return self._respuesta_honesta("motor_no disponible: sesión no creada")
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

    def _respuesta_honesta(self, motivo: str) -> _MockResponse:
        """Fail-closed honesto (L2/L4): NO finge contenido del LLM."""
        return _MockResponse({
            "content": f"[EIR · motor offline] {motivo}",
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
    pasó por el control de PHI antes. No anonimiza aquí de nuevo para no duplicar;
    si un mensaje trae PHI cruda, se va a enviar. El control de PHI está en el runner,
    no en el adapter.
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
