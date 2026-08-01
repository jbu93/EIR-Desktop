# -*- coding: utf-8 -*-
"""
core/harness/lsp/cliente_lsp.py · Cliente LSP mínimo por stdio (M-055 Fase 2B)
═══════════════════════════════════════════════════════════════════════════════
Habla JSON-RPC 2.0 sobre stdio con ``python -m pylsp``. Se implementa a mano
—en vez de traer una librería cliente— porque solo se necesitan tres peticiones
(``definition``, ``references``, ``publishDiagnostics``) y el ciclo de vida del
proceso ya tiene precedente en el repo (``opencode_server.py``).

Invariantes:
  · **Nunca cuelga.** Toda espera tiene timeout; vencido, se devuelve
    ``LspNoDisponible("lsp_timeout")`` y el desktop sigue vivo.
  · **Nunca inventa.** Si el servidor no arranca o muere, se lanza
    ``LspNoDisponible("lsp_no_disponible")``; jamás una respuesta vacía que
    parezca "no hay referencias" cuando en realidad no se pudo preguntar (L4).
  · **Perezoso y reutilizable.** El servidor arranca en la primera petición y
    se reutiliza; ``reiniciar()`` lo apaga (los arneses y los tests lo usan).
  · **Solo lectura.** No se envían ``didChange`` ni ``willSave``: los archivos
    se abren tal como están en disco.
"""
from __future__ import annotations

import atexit
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Slot que los arneses sobrescriben DIRECTAMENTE (asignación de módulo) para
# inyectar un servidor falso y simular caído/mudo/en-dos-lotes (G6, G7, G8,
# G9). Fuera de los arneses queda en None y ``_comando_servidor()`` decide.
_COMANDO_SERVIDOR: list[str] | None = None


def _comando_servidor() -> list[str]:
    """Comando para arrancar el servidor LSP, resuelto en tiempo de llamada (L10).

    Orden de resolución:
      1. ``_COMANDO_SERVIDOR`` sobrescrito por un arnés — se respeta tal cual
         (no toca env ni ``sys.frozen``; así G6/G7/G8/G9 siguen inyectando un
         proceso falso sin más cambios).
      2. ``EIR_LSP_COMANDO`` (env, parseable con ``shlex``) — el doctor con
         Python instalado en su máquina enciende el LSP a mano.
      3. Bundle PyInstaller (``sys.frozen``) sin comando explícito: en onefile
         ``sys.executable`` ES el propio .exe, así que ``-m pylsp`` relanzaría
         la app entera en vez de un servidor. Se prefiere una degradación
         honesta (L4) — ``LspNoDisponible`` — a un fork silencioso del
         desktop.
      4. Desarrollo: ``python -m pylsp`` de siempre.
    """
    if _COMANDO_SERVIDOR is not None:
        return list(_COMANDO_SERVIDOR)
    env = os.environ.get("EIR_LSP_COMANDO", "").strip()
    if env:
        return shlex.split(env)
    if getattr(sys, "frozen", False):
        raise LspNoDisponible("lsp_no_disponible_en_bundle")
    return [sys.executable, "-m", "pylsp"]

TIMEOUT_POR_DEFECTO = 20.0
# El arranque va acotado y CORTO a propósito: si el servidor no saluda pronto,
# es que no va a saludar, y el desktop no puede quedarse esperando (G7).
_TIMEOUT_ARRANQUE = 8.0
# Tras el primer lote de diagnósticos, pylsp suele publicar más (un plugin por
# vez). Se espera esta ventana para quedarse con la foto completa, no con la
# primera parcial — que sería reportar "0 errores" sobre un archivo roto.
_VENTANA_DIAGNOSTICOS = 1.5

_lock = threading.RLock()
_servidor: "_Servidor | None" = None


class LspNoDisponible(Exception):
    """El servidor LSP no pudo responder. ``motivo`` es un código estable."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def _uri(ruta: str | Path) -> str:
    return Path(ruta).resolve().as_uri()


class _Servidor:
    """Proceso pylsp + lector en hilo daemon que despacha por id de petición."""

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self._id = 0
        self._respuestas: dict[int, Any] = {}
        self._diagnosticos: dict[str, list] = {}
        self._evento = threading.Condition()
        self._abiertos: set[str] = set()

        try:
            self.proc = subprocess.Popen(
                _comando_servidor(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=str(self.raiz),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except OSError as exc:
            raise LspNoDisponible("lsp_no_disponible") from exc

        self._lector = threading.Thread(target=self._leer_siempre, daemon=True)
        self._lector.start()
        self._inicializar()

    # ─── transporte ────────────────────────────────────────────────────
    def _enviar(self, mensaje: dict) -> None:
        if self.proc.poll() is not None:
            raise LspNoDisponible("lsp_no_disponible")
        cuerpo = json.dumps(mensaje).encode("utf-8")
        cabecera = f"Content-Length: {len(cuerpo)}\r\n\r\n".encode("ascii")
        try:
            self.proc.stdin.write(cabecera + cuerpo)
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise LspNoDisponible("lsp_no_disponible") from exc

    def _leer_siempre(self) -> None:
        """Hilo lector: separa respuestas (con id) de notificaciones."""
        salida = self.proc.stdout
        while True:
            try:
                largo = 0
                while True:
                    linea = salida.readline()
                    if not linea:
                        return                      # el proceso murió
                    linea = linea.decode("utf-8", "replace").strip()
                    if not linea:
                        break                       # fin de cabeceras
                    if linea.lower().startswith("content-length:"):
                        largo = int(linea.split(":", 1)[1].strip())
                if largo <= 0:
                    continue
                crudo = salida.read(largo)
                if not crudo:
                    return
                msg = json.loads(crudo.decode("utf-8", "replace"))
            except Exception:                       # noqa: BLE001 — un mensaje roto no mata el hilo
                continue

            with self._evento:
                if "id" in msg and ("result" in msg or "error" in msg):
                    self._respuestas[msg["id"]] = msg
                elif msg.get("method") == "textDocument/publishDiagnostics":
                    params = msg.get("params") or {}
                    self._diagnosticos[params.get("uri", "")] = params.get("diagnostics") or []
                self._evento.notify_all()

    def _peticion(self, metodo: str, params: dict, timeout: float) -> Any:
        with self._evento:
            self._id += 1
            id_ = self._id
        self._enviar({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params})

        limite = time.time() + timeout
        with self._evento:
            while id_ not in self._respuestas:
                restante = limite - time.time()
                if restante <= 0:
                    raise LspNoDisponible("lsp_timeout")
                if self.proc.poll() is not None:
                    raise LspNoDisponible("lsp_no_disponible")
                self._evento.wait(min(restante, 0.25))
            msg = self._respuestas.pop(id_)
        if "error" in msg:
            raise LspNoDisponible("lsp_error")
        return msg.get("result")

    def _notificar(self, metodo: str, params: dict) -> None:
        self._enviar({"jsonrpc": "2.0", "method": metodo, "params": params})

    # ─── ciclo de vida ─────────────────────────────────────────────────
    def _inicializar(self) -> None:
        self._peticion("initialize", {
            "processId": os.getpid(),
            "rootUri": _uri(self.raiz),
            "capabilities": {"textDocument": {
                "publishDiagnostics": {},
                "definition": {}, "references": {},
            }},
        }, _TIMEOUT_ARRANQUE)
        self._notificar("initialized", {})

    def apagar(self) -> None:
        try:
            self._notificar("exit", {})
        except Exception:                           # noqa: BLE001
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:                           # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:                       # noqa: BLE001
                pass

    # ─── documentos ────────────────────────────────────────────────────
    def abrir(self, ruta: Path) -> str:
        uri = _uri(ruta)
        try:
            texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LspNoDisponible("archivo_ilegible") from exc
        with self._evento:
            self._diagnosticos.pop(uri, None)
        self._notificar("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "python", "version": 1, "text": texto,
        }})
        self._abiertos.add(uri)
        return uri

    def esperar_diagnosticos(self, uri: str, timeout: float) -> list:
        """Los diagnósticos llegan como notificación asíncrona tras el didOpen.

        pylsp publica una vez por plugin: quedarse con el primer lote reportaría
        "0 errores" sobre un archivo roto. Tras el primer lote se espera una
        ventana corta y se devuelve la última foto.
        """
        limite = time.time() + timeout
        with self._evento:
            while uri not in self._diagnosticos:
                restante = limite - time.time()
                if restante <= 0:
                    raise LspNoDisponible("lsp_timeout")
                if self.proc.poll() is not None:
                    raise LspNoDisponible("lsp_no_disponible")
                self._evento.wait(min(restante, 0.25))

        fin_ventana = min(time.time() + _VENTANA_DIAGNOSTICOS, limite)
        while time.time() < fin_ventana:
            with self._evento:
                self._evento.wait(min(fin_ventana - time.time(), 0.25))
        with self._evento:
            return list(self._diagnosticos.get(uri) or [])


# ─── API pública ────────────────────────────────────────────────────────
def _obtener(raiz: Path) -> _Servidor:
    global _servidor
    with _lock:
        if _servidor is not None and _servidor.proc.poll() is None \
                and _servidor.raiz == Path(raiz):
            return _servidor
        if _servidor is not None:
            _servidor.apagar()
            _servidor = None
        _servidor = _Servidor(Path(raiz))
        return _servidor


def reiniciar() -> None:
    """Apaga el servidor si está vivo. Lo usan los arneses y el apagado del app."""
    global _servidor
    with _lock:
        if _servidor is not None:
            _servidor.apagar()
            _servidor = None


atexit.register(reiniciar)


def _posiciones(resultado: Any) -> list[dict]:
    """Normaliza Location | Location[] | LocationLink[] a una lista estable.

    Las líneas y columnas de LSP son 0-based; se devuelven 1-based porque es
    lo que un humano lee en su editor y lo que el agente va a narrar.
    """
    if resultado is None:
        return []
    bruto = resultado if isinstance(resultado, list) else [resultado]
    salida: list[dict] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri") or ""
        rango = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange") or {}
        inicio = (rango or {}).get("start") or {}
        try:
            archivo = str(Path(uri.replace("file:///", "").replace("file://", "")))
        except Exception:                           # noqa: BLE001
            archivo = uri
        salida.append({
            "archivo": archivo,
            "linea": int(inicio.get("line", 0)) + 1,
            "columna": int(inicio.get("character", 0)) + 1,
        })
    return salida


def definicion(ruta: Path, linea: int, columna: int, raiz: Path,
               timeout: float = TIMEOUT_POR_DEFECTO) -> list[dict]:
    """Dónde se define el símbolo en (linea, columna) — ambas 1-based."""
    srv = _obtener(raiz)
    uri = srv.abrir(ruta)
    res = srv._peticion("textDocument/definition", {
        "textDocument": {"uri": uri},
        "position": {"line": max(0, linea - 1), "character": max(0, columna - 1)},
    }, timeout)
    return _posiciones(res)


def referencias(ruta: Path, linea: int, columna: int, raiz: Path,
                timeout: float = TIMEOUT_POR_DEFECTO) -> list[dict]:
    """Dónde se usa el símbolo en (linea, columna) — incluye la declaración."""
    srv = _obtener(raiz)
    uri = srv.abrir(ruta)
    res = srv._peticion("textDocument/references", {
        "textDocument": {"uri": uri},
        "position": {"line": max(0, linea - 1), "character": max(0, columna - 1)},
        "context": {"includeDeclaration": True},
    }, timeout)
    return _posiciones(res)


def diagnosticos(ruta: Path, raiz: Path,
                 timeout: float = TIMEOUT_POR_DEFECTO) -> list[dict]:
    """Qué ve roto el analizador en el archivo. Lista vacía = nada que reportar."""
    srv = _obtener(raiz)
    uri = srv.abrir(ruta)
    crudos = srv.esperar_diagnosticos(uri, timeout)
    salida = []
    for d in crudos:
        inicio = ((d or {}).get("range") or {}).get("start") or {}
        salida.append({
            "linea": int(inicio.get("line", 0)) + 1,
            "columna": int(inicio.get("character", 0)) + 1,
            "severidad": int(d.get("severity") or 0),   # 1=error 2=aviso 3=info 4=pista
            "mensaje": str(d.get("message", ""))[:300],
            "fuente": str(d.get("source", ""))[:40],
        })
    return salida
