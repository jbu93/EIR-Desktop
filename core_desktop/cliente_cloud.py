"""cliente_cloud.py · Adapter a eirdr.com/api/v1/inference (M-052).

Convierte en real la Fase futura del contrato documentado en ``cliente_llm.py``:
el desktop habla contra el gateway cloud de EIR DR. (el cerebro vive en la nube,
el cliente nunca ve claves de Groq/NVIDIA/Google).

Mima la superficie ``chat.completions.create(**kw)`` que usa
``core/agente_loop._pedir_siguiente_herramienta`` — drop-in para el loop.

PRINCIPIOS:
  · L6 · PHI nunca sale en claro: el historial que arma el loop ya pasó por
         core/anonymizer en el runner; este adapter confía en esa frontera.
  · L2 · Fail-closed: 401 → "inicia sesión", 402 → límite, 503 → motor caído.
         Nunca inventa contenido del LLM.
  · L5 · Si no hay sesión, responde honesto; el desktop sigue funcionando
         en modo local con otros clientes.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from core_desktop.cliente_llm import _MockResponse, _MockMessage  # reusa la forma
from core_desktop import sesion


def _base_url() -> str:
    return os.getenv("EIR_CLOUD_URL", "https://eirdr.com").strip().rstrip("/") or "https://eirdr.com"


class ClienteEirCloud:
    """Adapter al gateway cloud EIR. Superficie chat.completions.create."""

    DEFAULT_TIMEOUT = 60.0

    def __init__(self, rol: str = "odontologo", token: str | None = None):
        self.rol = rol
        self._token = (token or "").strip() or sesion.token()
        self.ultimo_texto_final: str | None = None
        self.ultima_traza: list | None = None
        self.ultimo_credito: int | None = None
        self.chat = _ChatAdapter(self)

    def __repr__(self) -> str:
        return (f"ClienteEirCloud(rol={self.rol!r}, "
                f"sesion={'ok' if self._token else 'sin_sesion'}, base={_base_url()!r})")

    # ─── HTTP ──────────────────────────────────────────────
    def _post(self, url: str, payload: dict, headers: dict | None = None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=self.DEFAULT_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))

    def _respuesta_honesta(self, motivo: str) -> _MockResponse:
        """Fail-closed honesto (L2/L4): NO finge contenido del LLM."""
        return _MockResponse({
            "content": f"[EIR · cloud] {motivo}",
            "tool_calls": None,
        })

    def _llamar_inference(self, mensaje: str, historial: list,
                          tools_disponibles: list) -> _MockResponse:
        if not self._token:
            return self._respuesta_honesta(
                "inicia sesión (menú de sesión) para usar la inferencia cloud.")

        payload = {
            "rol": self.rol,
            "mensaje": mensaje,
            "historial": historial,
            "tools_locales_disponibles": tools_disponibles,
        }
        try:
            resp = self._post(
                _base_url() + "/api/v1/inference", payload,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                detalle = json.loads(exc.read().decode("utf-8"))
                msg = detalle.get("mensaje") or detalle.get("error") or str(exc)
            except Exception:
                msg = str(exc)
            if code == 401:
                return self._respuesta_honesta("tu sesión expiró. Vuelve a iniciar sesión.")
            if code == 402:
                return self._respuesta_honesta(f"límite diario alcanzado: {msg}")
            if code == 429:
                return self._respuesta_honesta("demasiadas consultas. Intenta en un momento.")
            return self._respuesta_honesta(f"la nube respondió HTTP {code}: {msg}")
        except urllib.error.URLError as exc:
            return self._respuesta_honesta(f"nube no disponible: {exc.reason} (modo local activo)")
        except Exception as exc:
            return self._respuesta_honesta(f"nube no disponible: {exc}")

        self.ultima_traza = resp.get("traza_pasos") or []
        self.ultimo_credito = resp.get("credito_restante_hoy")
        tool_call = resp.get("tool_call")
        if tool_call and tool_call.get("name"):
            from core_desktop.cliente_llm import _MockToolCall
            try:
                args = json.loads(tool_call.get("arguments") or "{}")
            except Exception:
                args = {}
            return _MockResponse({
                "content": None,
                "tool_calls": [_MockToolCall(tool_call["name"], args)],
            })
        final_text = resp.get("final_text")
        if not final_text:
            return self._respuesta_honesta("la nube devolvió una respuesta vacía.")
        self.ultimo_texto_final = final_text
        return _MockResponse({"content": final_text, "tool_calls": None})


class _ChatAdapter:
    """Mima `cliente.chat`."""

    def __init__(self, padre: "ClienteEirCloud"):
        self.completions = _CompletionsAdapter(padre)


class _CompletionsAdapter:
    """Mima `cliente.chat.completions.create(**kw)` — drop-in para agente_loop."""

    def __init__(self, padre: "ClienteEirCloud"):
        self.padre = padre

    def create(self, *, model: str | None = None, messages: list | None = None,
               tools: list | None = None, **_):
        mensajes = list(messages or [])
        historial = mensajes[:-1] if mensajes else []
        ultimo = (mensajes[-1].get("content") or "") if mensajes else ""
        tools_disponibles = []
        for t in tools or []:
            nombre = (t.get("function") or {}).get("name") if isinstance(t, dict) else None
            if nombre:
                tools_disponibles.append(nombre)
        return self.padre._llamar_inference(ultimo, historial, tools_disponibles)
