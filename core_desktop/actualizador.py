"""actualizador.py · Señal de versión del desktop (M-052) + auto-update (D078).

Consulta ``GET /api/desktop/version`` (backend web) y compara con la versión
local. La señal la publica el Soberano en Railway con env vars
(EIR_DESKTOP_VERSION / DOWNLOAD_URL / SHA256) tras cada deploy.

Auto-update (D078):
  - ``descargar_y_verificar(url, sha256_esperado, version)`` baja el .exe a un
    directorio temporal, verifica su SHA256 contra el esperado (L4) y devuelve
    la ruta. NUNCA toca el .exe en uso.
  - Fail-closed (L2/L4): hash que no coincide → borra el temporal y falla.
  - L5 · Offline-friendly: si no hay red, jamás bloquea el arranque; responde
     ``ok:false`` con motivo y el desktop sigue igual.
  - L10 · kill-switch ``EIR_DESKTOP_AUTOUPDATE`` leído en tiempo de llamada;
     nace APAGADO (default "0"). El release lo siembra en "1" vía el runtime
     hook de PyInstaller (eir_desktop_runtime_hook.py).
  - L4 · Nunca inventa versiones: si el backend dice sin_release, no hay update.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

from core_desktop import __version__

_CACHE_SEGUNDOS = 3600
_ULTIMO_ESTADO: dict | None = None
_ULTIMO_TS: float = 0.0

# L8 · límite calibrado: el .exe son ~38 MB; 600s cubre una descarga lenta
# sin copiar el default defensivo de ejemplos web.
_DESCARGA_TIMEOUT = 600
_DIR_TEMPORAL = Path(tempfile.gettempdir()) / "eir_dr_update"


def _auto_update_activo() -> bool:
    """L10 · kill-switch leído en tiempo de llamada; default APAGADO."""
    return os.getenv("EIR_DESKTOP_AUTOUPDATE", "0").strip() in ("1", "true", "True", "yes")


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


def descargar_y_verificar(url: str, sha256_esperado: str, version: str,
                          on_progreso=None) -> dict:
    """Descarga el .exe nuevo a TEMP, valida SHA256 (L4) y devuelve la ruta.

    Fail-closed (L2): si el hash no coincide, borra el temporal y devuelve
    ok:false — el .exe en uso jamás se toca. Devuelve {ok, ruta?, sha256,
    tamano_bytes?, motivo?}. ``on_progreso(porcentaje)`` se llama con un int
    0-100 calculado del Content-Length cuando el servidor lo expone (L4: si no
    se conoce el total, se reporta "indeterminado", nunca un % inventado).
    """
    if not url or not sha256_esperado:
        return {"ok": False, "motivo": "sin_metadata",
                "detalle": "falta url_windows o sha256 en la señal"}

    _DIR_TEMPORAL.mkdir(parents=True, exist_ok=True)
    destino = _DIR_TEMPORAL / f"EIR_DR_Desktop_{version}.exe"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=_DESCARGA_TIMEOUT) as r:
            total = None
            try:
                total = int(r.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                total = None
            if on_progreso is not None:
                on_progreso(0)
            trozos = []
            leidos = 0
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                trozos.append(chunk)
                leidos += len(chunk)
                if total and on_progreso is not None:
                    on_progreso(min(99, int(leidos * 100 / total)))
            data = b"".join(trozos)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {"ok": False, "motivo": "sin_red",
                "detalle": f"{type(e).__name__}: {e}"}
    except Exception as e:                    # noqa: BLE001
        return {"ok": False, "motivo": "error_descarga",
                "detalle": f"{type(e).__name__}: {e}"}

    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != (sha256_esperado or "").lower():
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "motivo": "sha256_no_coincide",
                "detalle": f"esperado {sha256_esperado[:12]}…, descargado {digest[:12]}…",
                "sha256_descargado": digest, "ruta": str(destino)}

    try:
        destino.write_bytes(data)
    except OSError as e:
        return {"ok": False, "motivo": "error_escritura", "detalle": str(e)}

    return {"ok": True, "ruta": str(destino), "sha256": digest,
            "tamano_bytes": len(data)}


def generar_script_reemplazo(ruta_nueva: str, exe_actual: str) -> dict:
    """Escribe el .bat que reemplaza el .exe en ejecución (D078).

    Windows no permite reemplazar un .exe mientras corre; el patrón es un
    script desacoplado que espera a que la app cierre, copia el nuevo binario
    sobre ``sys.executable``, limpia el temporal y relanza.

    Devuelve {ok, bat_ruta, motivo?}. El .bat se guarda en el mismo directorio
    temporal del update (no en el del .exe, que puede no tener permisos).
    """
    ruta_nueva = str(ruta_nueva)
    exe_actual = str(exe_actual)
    if not ruta_nueva or not exe_actual:
        return {"ok": False, "motivo": "sin_rutas"}
    try:
        _DIR_TEMPORAL.mkdir(parents=True, exist_ok=True)
        bat = _DIR_TEMPORAL / "_eir_dr_aplicar_update.bat"
        lineas = [
            "@echo off",
            "timeout /t 2 /nobreak >nul",
            f'copy /y "{ruta_nueva}" "{exe_actual}"',
            "if errorlevel 1 exit /b 1",
            f'del /f /q "{ruta_nueva}"',
            f'start "" "{exe_actual}"',
            "exit /b 0",
        ]
        bat.write_text("\r\n".join(lineas) + "\r\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "motivo": "error_escritura", "detalle": str(e)}
    return {"ok": True, "bat_ruta": str(bat), "exe_actual": exe_actual}


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
        res = {"ok": False, "motivo": "sin_red", "actual": version_actual(),
               "auto_update": _auto_update_activo()}
        _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
        return res
    except Exception as e:
        res = {"ok": False, "motivo": f"error:{type(e).__name__}",
               "actual": version_actual(),
               "auto_update": _auto_update_activo()}
        _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
        return res

    if not data.get("ok"):
        res = {"ok": False, "motivo": data.get("mensaje") or "sin_release",
               "actual": version_actual(),
               "auto_update": _auto_update_activo()}
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
            "auto_update": _auto_update_activo(),
        }
    _ULTIMO_ESTADO, _ULTIMO_TS = res, ahora
    return res
