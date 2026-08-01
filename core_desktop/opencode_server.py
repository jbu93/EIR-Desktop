"""opencode_server.py · Gestión automática del servidor OpenCode (subprocess).

Maneja el lifecycle del servidor `opencode serve` como dependencia del desktop app:
- Arranque automático al iniciar la app
- Health check antes de marcar como listo
- Apagado graceful al cerrar la app
- Reinicio automático si el proceso muere
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("eir_desktop.opencode_server")


class OpenCodeServerManager:
    """Gestiona el lifecycle del servidor `opencode serve`."""

    # M-059 · protección contra crash-loop: si el proceso muere más de
    # MAX_REINTENTOS veces dentro de VENTANA_REINTENTOS segundos, el monitor
    # deja de reiniciar. Sin esto, un binario roto reinicia para siempre,
    # consumiendo CPU sin que el doctor se entere jamás (nunca ve una consola).
    MAX_REINTENTOS = 3
    VENTANA_REINTENTOS = 60.0

    def __init__(
        self,
        port: int = 4096,
        hostname: str = "127.0.0.1",
        startup_timeout: float = 30.0,
        health_check_interval: float = 0.5,
        monitor_interval: float = 2.0,
        restart_delay: float = 1.0,
    ):
        self.port = port
        self.hostname = hostname
        self.startup_timeout = startup_timeout
        self.health_check_interval = health_check_interval
        # M-059 · configurables (antes hardcodeados en el cuerpo de los métodos)
        # para que el arnés pueda probar el crash-loop en milisegundos, no en
        # minutos; en producción los defaults son los mismos de siempre.
        self.monitor_interval = monitor_interval
        self.restart_delay = restart_delay

        self.process: Optional[subprocess.Popen] = None
        # RLock (no Lock): start() adquiere el lock y llama a self.is_running,
        # que vuelve a pedirlo. Con Lock() normal eso es un AUTODEADLOCK —
        # bug real encontrado por el arnés M-059 (start() jamás retornaba).
        self._lock = threading.RLock()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # M-059 · diagnóstico honesto para el doctor (nunca None, L4)
        self._ultimo_motivo: Optional[str] = None
        self._reintentos_consecutivos = 0
        self._fallo_persistente = False
        self._historial_muertes: list[float] = []  # timestamps, para la ventana de crash-loop

        # URL base del servidor
        self.base_url = f"http://{hostname}:{port}"

    @property
    def url(self) -> str:
        return self.base_url

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and self.process is not None and self.process.poll() is None

    def estado(self) -> dict:
        """Diagnóstico honesto (M-059, L4): nunca None, nunca un traceback
        crudo. `motivo` es un código estable — el único vocabulario que el
        doctor (vía la UI, cuando M-061 la construya) o un panel de soporte
        necesitan para entender qué pasó, sin abrir una consola que no existe."""
        with self._lock:
            return {
                "disponible": self.is_running,
                "motivo": self._ultimo_motivo,
                "reintentos": self._reintentos_consecutivos,
                "fallo_persistente": self._fallo_persistente,
            }

    def start(self, _es_reinicio_automatico: bool = False) -> bool:
        """Arranca el servidor OpenCode y espera a que esté saludable.

        `_es_reinicio_automatico` (M-059, uso interno de `_restart_async`):
        un reinicio disparado por el monitor NO limpia el historial de
        muertes al tener éxito — si lo limpiara, un proceso que arranca bien,
        muere a los 3s, y se reinicia una y otra vez JAMÁS activaría la
        protección de crash-loop, porque cada arranque exitoso borraría su
        propia evidencia. Un arranque manual (el default) sí parte de cero.
        """
        with self._lock:
            if self.is_running:
                log.info("OpenCode server ya está corriendo en %s", self.base_url)
                return True

            # Verificar que opencode esté disponible
            if not self._verificar_opencode():
                log.error("OpenCode no está instalado o no está en PATH")
                self._ultimo_motivo = "opencode_no_instalado"
                return False

            # Preparar comando
            cmd = [
                "opencode", "serve",
                "--port", str(self.port),
                "--hostname", self.hostname,
            ]

            # Variables de entorno para el subprocess
            env = os.environ.copy()
            # Asegurar que use el mismo PATH
            env["PATH"] = env.get("PATH", "")

            log.info("Arrancando OpenCode server: %s", " ".join(cmd))

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    # En Windows, CREATE_NEW_PROCESS_GROUP permite terminación limpia
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            except FileNotFoundError:
                log.error("Comando 'opencode' no encontrado. ¿Está instalado? (npm install -g opencode-ai)")
                self._ultimo_motivo = "opencode_no_instalado"
                return False
            except Exception as e:
                log.error("Error arrancando OpenCode: %s", e)
                self._ultimo_motivo = "error_arranque"
                return False

            self._running = True
            self._stop_event.clear()

            # Iniciar hilo monitor para reinicio automático si muere
            self._monitor_thread = threading.Thread(target=self._monitor_process, daemon=True)
            self._monitor_thread.start()

            # Esperar health check
            if not self._wait_healthy():
                log.error("OpenCode server no pasó health check en %ss", self.startup_timeout)
                self.stop()
                return False

            # M-059 · arranque exitoso: limpia el diagnóstico SOLO si fue un
            # arranque manual (ver docstring). Un reinicio automático conserva
            # el historial de muertes: si vuelve a morir pronto, debe contar.
            self._ultimo_motivo = None
            if not _es_reinicio_automatico:
                self._reintentos_consecutivos = 0
                self._fallo_persistente = False
                self._historial_muertes = []

            log.info("OpenCode server listo en %s", self.base_url)
            return True

    def stop(self) -> None:
        """Detiene el servidor OpenCode de forma graceful."""
        with self._lock:
            self._running = False
            self._stop_event.set()

        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)

        if self.process:
            log.info("Deteniendo OpenCode server (PID: %d)...", self.process.pid)
            try:
                if os.name == "nt":
                    # Windows: CTRL_BREAK_EVENT para proceso en grupo
                    self.process.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
                else:
                    self.process.terminate()
                
                self.process.wait(timeout=10.0)
                log.info("OpenCode server detenido correctamente")
            except subprocess.TimeoutExpired:
                log.warning("OpenCode server no terminó a tiempo, forzando kill...")
                self.process.kill()
                self.process.wait()
            except Exception as e:
                log.error("Error deteniendo OpenCode server: %s", e)
            finally:
                self.process = None

    def _verificar_opencode(self) -> bool:
        """Verifica que el comando opencode esté disponible."""
        try:
            result = subprocess.run(
                ["opencode", "--version"],
                capture_output=True,
                timeout=5.0,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _wait_healthy(self) -> bool:
        """Espera a que el servidor responda al health check."""
        deadline = time.time() + self.startup_timeout
        health_url = f"{self.base_url}/health"

        while time.time() < deadline:
            if self._stop_event.is_set():
                return False

            # Verificar si el proceso murió
            if self.process and self.process.poll() is not None:
                # Leer stderr para diagnóstico (queda en el log; NUNCA se expone
                # crudo al doctor — el endpoint de estado solo da el motivo estable)
                if self.process.stderr:
                    stderr_output = self.process.stderr.read().decode("utf-8", errors="ignore")
                    if stderr_output:
                        log.error("OpenCode server stderr: %s", stderr_output[:500])
                self._ultimo_motivo = "proceso_crasheo"
                return False

            try:
                resp = httpx.get(health_url, timeout=2.0)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass

            time.sleep(self.health_check_interval)

        self._ultimo_motivo = "timeout_arranque"
        return False

    def _monitor_process(self) -> None:
        """Hilo monitor: reinicia el servidor si muere inesperadamente.

        M-059 · protección contra crash-loop: cada muerte queda registrada con
        su timestamp. Si se acumulan MAX_REINTENTOS dentro de VENTANA_REINTENTOS
        segundos, deja de reiniciar — sin esto un binario roto reinicia para
        siempre y el doctor nunca lo sabe (no hay consola visible)."""
        while not self._stop_event.is_set():
            time.sleep(self.monitor_interval)

            with self._lock:
                if not self._running:
                    break

                if self.process and self.process.poll() is not None:
                    # El proceso murió
                    log.warning("OpenCode server murió inesperadamente (exit code: %d).",
                                self.process.poll())

                    # Leer stderr si hay
                    if self.process.stderr:
                        stderr_output = self.process.stderr.read().decode("utf-8", errors="ignore")
                        if stderr_output:
                            log.error("OpenCode server stderr antes de morir: %s", stderr_output[:500])

                    self.process = None

                    # Registrar la muerte y podar las que ya salieron de la ventana.
                    ahora = time.time()
                    self._historial_muertes.append(ahora)
                    self._historial_muertes = [
                        t for t in self._historial_muertes if ahora - t <= self.VENTANA_REINTENTOS
                    ]
                    self._reintentos_consecutivos = len(self._historial_muertes)

                    if self._reintentos_consecutivos >= self.MAX_REINTENTOS:
                        log.error(
                            "OpenCode server murió %d veces en %ss: dejo de reiniciar (crash-loop).",
                            self._reintentos_consecutivos, self.VENTANA_REINTENTOS,
                        )
                        self._fallo_persistente = True
                        self._ultimo_motivo = "fallo_persistente"
                        self._running = False
                        break

                    log.warning("Reiniciando OpenCode server (intento %d/%d)...",
                               self._reintentos_consecutivos, self.MAX_REINTENTOS)
                    if not self._stop_event.is_set():
                        threading.Thread(target=self._restart_async, daemon=True).start()
                    break

    def _restart_async(self) -> None:
        """Reinicia el servidor de forma asíncrona."""
        log.info("Reiniciando OpenCode server...")
        time.sleep(self.restart_delay)
        if not self._stop_event.is_set():
            self.start(_es_reinicio_automatico=True)


# Instancia global singleton
_server_manager: Optional[OpenCodeServerManager] = None
_manager_lock = threading.Lock()


def get_server_manager() -> OpenCodeServerManager:
    """Obtiene la instancia singleton del manager."""
    global _server_manager
    with _manager_lock:
        if _server_manager is None:
            port = int(os.getenv("OPENCODE_SERVER_PORT", "4096"))
            hostname = os.getenv("OPENCODE_SERVER_HOST", "127.0.0.1")
            _server_manager = OpenCodeServerManager(port=port, hostname=hostname)
        return _server_manager


def iniciar_opencode_server() -> bool:
    """Función de conveniencia para arrancar el servidor global."""
    return get_server_manager().start()


def detener_opencode_server() -> None:
    """Función de conveniencia para detener el servidor global."""
    global _server_manager
    with _manager_lock:
        if _server_manager:
            _server_manager.stop()
            _server_manager = None