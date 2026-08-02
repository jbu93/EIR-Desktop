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
_MARCADOR_FALLO = _DIR_TEMPORAL / "_eir_dr_update_fallo.txt"


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

    Las tres cosas que este script tiene que hacer bien
    ---------------------------------------------------

    1. **Limpiar las variables ``_PYI_*`` antes de relanzar.** Es el bug que
       rompia el auto-update entero. PyInstaller 6 marca a su proceso hijo con
       ``_PYI_APPLICATION_HOME_DIR`` / ``_PYI_ARCHIVE_FILE`` /
       ``_PYI_PARENT_PROCESS_LEVEL`` (antes se llamaba ``_MEIPASS2``). El .bat
       nace de la app congelada y las hereda; el .exe relanzado tambien. Su
       bootloader concluye "ya soy el hijo extraido", NO extrae el bundle, y
       busca el DLL de Python en el ``_MEI`` de la instancia anterior — que ya
       fue borrado. Muere con un dialogo modal "Failed to load Python DLL
       '...\\_MEI21402\\python314.dll'". Modal: el proceso queda vivo esperando
       un clic que nadie va a dar. Para el odontologo: hace clic en Actualizar,
       la app se cierra y aparece un error cripitco que no dice nada.

    2. **Esperar a que el .exe se pueda escribir.** Windows no deja sobrescribir
       un ejecutable en uso. ``os._exit(0)`` mata al hijo Python al instante
       pero el bootloader padre sigue vivo un rato borrando su ``_MEI``, asi que
       un ``timeout`` fijo es una apuesta. Se sondea el bloqueo real
       (``>>file (call )`` falla mientras algo lo tenga tomado).

    3. **Dejar rastro — y NUNCA dejar al doctor sin app.** ``_eir_dr_update.log``
       es un rastro para diagnóstico, pero no lo lee nadie en el momento: la
       app que podría mostrarlo ya está muerta desde antes de que este script
       empiece a intentar nada (``os._exit(0)`` corre justo después de lanzar
       este .bat, no después de que termine). Descubierto de raíz (D097): si el
       .exe nunca se libera (90 intentos, ~3 min) o el ``copy`` falla, el script
       simplemente salía sin relanzar nada — el .exe original queda intacto en
       disco (nunca se llegó a tocar) pero nadie lo abre. Para el doctor: la app
       se cierra al hacer clic en Actualizar y **no vuelve nunca, sin ningún
       aviso**. Por eso ambos caminos de error ahora relanzan el .exe ORIGINAL
       (intacto, sin actualizar) y dejan un marcador de una sola lectura
       (``_eir_dr_update_fallo.txt``) que ``consumir_fallo_previo()`` lee y
       borra en el siguiente arranque — así la próxima sesión SÍ avisa qué
       pasó, en vez de fallar en silencio.

    Detalle menor: se usa ``ping`` como pausa y no ``timeout``, porque
    ``timeout`` lee de la consola y el .bat corre con DETACHED_PROCESS, sin
    consola alguna.
    """
    ruta_nueva = str(ruta_nueva)
    exe_actual = str(exe_actual)
    if not ruta_nueva or not exe_actual:
        return {"ok": False, "motivo": "sin_rutas"}
    try:
        _DIR_TEMPORAL.mkdir(parents=True, exist_ok=True)
        bat = _DIR_TEMPORAL / "_eir_dr_aplicar_update.bat"
        log = _DIR_TEMPORAL / "_eir_dr_update.log"
        marcador = _MARCADOR_FALLO
        lineas = [
            "@echo off",
            # (1) sin esto el .exe relanzado se cree el hijo ya extraido de la
            # instancia anterior y muere buscando un _MEI que ya no existe
            "set _PYI_APPLICATION_HOME_DIR=",
            "set _PYI_ARCHIVE_FILE=",
            "set _PYI_PARENT_PROCESS_LEVEL=",
            "set _MEIPASS2=",
            f'echo [%date% %time%] update: esperando a que se libere el exe > "{log}"',
            "set /a N=0",
            "",
            ":esperar",
            "ping -n 2 127.0.0.1 >nul 2>&1",
            "set /a N+=1",
            # (2) el append falla mientras algun proceso tenga el .exe tomado
            f'2>nul (>>"{exe_actual}" call ) && goto libre',
            "if %N% GEQ 90 (",
            f'  echo [%date% %time%] ERROR: el exe sigue bloqueado tras %N% intentos >> "{log}"',
            f'  echo exe_bloqueado> "{marcador}"',
            f'  echo El archivo seguia en uso tras ~3 minutos de intentos>> "{marcador}"',
            # (3) NUNCA dejar al doctor sin app: relanza el original intacto
            f'  start "" "{exe_actual}"',
            "  exit /b 2",
            ")",
            "goto esperar",
            "",
            ":libre",
            f'echo [%date% %time%] exe libre tras %N% intentos; copiando >> "{log}"',
            f'copy /y "{ruta_nueva}" "{exe_actual}" >nul 2>&1',
            "if errorlevel 1 (",
            f'  echo [%date% %time%] ERROR: fallo el copy >> "{log}"',
            f'  echo fallo_copy> "{marcador}"',
            f'  echo No se pudo copiar el archivo descargado>> "{marcador}"',
            f'  start "" "{exe_actual}"',
            "  exit /b 1",
            ")",
            f'del /f /q "{ruta_nueva}" >nul 2>&1',
            # margen para que el sistema cierre el handle de escritura del copy
            "ping -n 3 127.0.0.1 >nul 2>&1",
            f'echo [%date% %time%] relanzando >> "{log}"',
            f'start "" "{exe_actual}"',
            f'echo [%date% %time%] listo >> "{log}"',
            "exit /b 0",
        ]
        bat.write_text("\r\n".join(lineas) + "\r\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "motivo": "error_escritura", "detalle": str(e)}
    return {"ok": True, "bat_ruta": str(bat), "exe_actual": exe_actual,
            "log_ruta": str(log)}


def consumir_fallo_previo() -> dict | None:
    """Lee y borra el marcador de un intento de actualización fallido (D097).

    El .bat que aplica el reemplazo corre desacoplado, sin consola y con la
    app ya cerrada (``os._exit(0)`` corre antes de que el .bat empiece a
    intentar nada) — si algo sale mal ahí (el .exe nunca se libera, o el copy
    falla), no hay ninguna app viva para mostrar ese error en el momento. Este
    marcador es el único rastro, y se consume una sola vez (se borra al
    leerlo) para que el aviso no se repita en cada arranque siguiente.
    """
    if not _MARCADOR_FALLO.exists():
        return None
    try:
        lineas = _MARCADOR_FALLO.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    finally:
        try:
            _MARCADOR_FALLO.unlink(missing_ok=True)
        except OSError:
            pass
    motivo = lineas[0].strip() if lineas else "desconocido"
    detalle = lineas[1].strip() if len(lineas) > 1 else ""
    return {"motivo": motivo, "detalle": detalle}


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