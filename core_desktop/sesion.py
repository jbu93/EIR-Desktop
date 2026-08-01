"""sesion.py · Sesión cloud EIR para el desktop (M-052).

Guarda el token de sesión EIR DR. en ``~/.eir_dr/sesion.json`` (SOLO token,
email, nombre y tier — nunca PHI clínica). El token NUNCA llega al webview:
vive en este proceso Python y se inyecta en ``ClienteEirCloud``.

L10 · la URL de la nube se lee en tiempo de llamada (EIR_CLOUD_URL).
L2  · fail-closed: sin archivo o sin token, ``token()`` devuelve None y el
      cliente cloud responde "inicia sesión" (honesto, nunca inventa).
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

_ARCHIVO = Path.home() / ".eir_dr" / "sesion.json"


def _base_url() -> str:
    return os.getenv("EIR_CLOUD_URL", "https://eirdr.com").strip().rstrip("/") or "https://eirdr.com"


def _leer() -> dict:
    try:
        if _ARCHIVO.exists():
            return json.loads(_ARCHIVO.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[sesion] lectura falló: {type(e).__name__}: {e}")
    return {}


def _escribir(data: dict):
    try:
        _ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
        _ARCHIVO.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[sesion] escritura falló: {type(e).__name__}: {e}")


def token() -> str | None:
    """Token de sesión guardado, o None."""
    return (_leer().get("token") or "").strip() or None


def estado() -> dict:
    """Proyección segura del estado de sesión (sin token para el frontend)."""
    d = _leer()
    tk = (d.get("token") or "").strip()
    return {
        "autenticado": bool(tk),
        "email": d.get("email", ""),
        "nombre": d.get("nombre", ""),
        "tier": d.get("tier", ""),
    }


def login(email: str, password: str) -> dict:
    """Llama al login cloud (header X-EIR-Desktop) y guarda el token.

    Devuelve {ok, ...} o {ok: False, error, mensaje}.
    """
    body = json.dumps({"email": email or "", "password": password or ""}).encode("utf-8")
    req = urllib.request.Request(
        _base_url() + "/api/auth/login", data=body,
        headers={"Content-Type": "application/json", "X-EIR-Desktop": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detalle = json.loads(e.read().decode("utf-8"))
            msg = detalle.get("error") or str(e)
        except Exception:
            msg = str(e)
        return {"ok": False, "error": "http_" + str(e.code), "mensaje": msg}
    except Exception as e:
        return {"ok": False, "error": "red", "mensaje": f"Sin conexión con la nube: {e}"}

    if not resp.get("ok") or not resp.get("token"):
        return {"ok": False, "error": "login_rechazado",
                "mensaje": resp.get("error") or "Credenciales incorrectas."}
    usuario = resp.get("usuario") or {}
    _escribir({
        "token": resp["token"],
        "email": usuario.get("email", email),
        "nombre": usuario.get("nombre", ""),
        "tier": usuario.get("tier", ""),
    })
    return {"ok": True, "email": usuario.get("email"), "nombre": usuario.get("nombre"),
            "tier": usuario.get("tier")}


def logout():
    try:
        _ARCHIVO.unlink(missing_ok=True)
    except Exception:
        try:
            _ARCHIVO.write_text("{}", encoding="utf-8")
        except Exception:
            pass
