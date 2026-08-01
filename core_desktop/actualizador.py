"""actualizador.py · Señal de versión del desktop (M-052).

Consulta ``GET /api/desktop/version`` (backend web) y compara con la versión
local. La señal la publica el Soberano en Railway con env vars
(EIR_DESKTOP_VERSION / DOWNLOAD_URL / SHA256) tras cada deploy.

L5 · Offline-friendly: si no hay red, jamás bloquea el arranque; responde
     ``ok:false`` con motivo y el desktop sigue igual.
L4 · Nunca inventa versiones: si el backend dice sin_release, no hay update.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

from core_desktop import __version__

_CACHE_SEGUNDOS = 3600
_ULTIMO_ESTADO: dict | None = None
_ULTIMO_TS: float = 0.0


def _base_url() -> str:
    return os.getenv("EIR_CLOUD_URL", "https://eirdr.com").strip().rstrip("/") or "https://eirdr.com"


def version_actual() -> str:
    return __version__


def _claves(v: str):
    partes = []
    for p in (v or "0").split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        partes.append(int(num) if num else 0)
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes[:3])


def _comparar(a: str, b: str) -> int:
    """>0 si a es más nueva que b. Solo compara numérico (1.0.0-beta ~ 1.0.0)."""
    ka, kb = _claves(a), _claves(b)
    return (ka > kb) - (ka < kb)


def estado(force: bool = False) -> dict:
    """Estado de versión. Cachea 1h; force para reconsultar."""
    global _ULTIMO_ESTADO, _ULTIMO_TS
    ahora = time.time()
    if _ULTIMO_ESTADO and not force and (ahora - _ULTIMO_TS) < _CACHE_SEGUNDOS:
        return _ULTIMO_ESTADO

    try:
        req = urllib.request.Request(_base_url() + "/api/desktop/version",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        res = {"ok": False, "motivo": "sin_red", "actual": version_actual()}
        _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
        return res
    except Exception as e:
        res = {"ok": False, "motivo": f"error:{type(e).__name__}",
               "actual": version_actual()}
        _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
        return res

    if not data.get("ok"):
        res = {"ok": False, "motivo": data.get("mensaje") or "sin_release",
               "actual": version_actual()}
    else:
        disp = data.get("version_desktop") or ""
        res = {
            "ok": True,
            "actual": version_actual(),
            "disponible": disp,
            "hay_actualizacion": bool(disp) and _comparar(disp, version_actual()) > 0,
            "url_windows": data.get("url_windows") or "",
            "sha256": data.get("sha256") or "",
            "notas": data.get("notas") or "",
        }
    _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
    return res
