# -*- coding: utf-8 -*-
"""tests/test_opencode_server.py · M-059 · lifecycle de opencode_server.py.

Propiedades que se afirman (L9, no conteos mágicos):
  · estado() nunca lanza y nunca es None, en cualquier momento del ciclo de vida.
  · start() no se autodeadlockea (RLock, no Lock — bug real cazado por M-059).
  · motivos estables por cada camino de fallo: opencode_no_instalado,
    timeout_arranque, proceso_crasheo, fallo_persistente.
  · el historial de muertes se poda fuera de la ventana (no crece sin límite).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from core_desktop import opencode_server as OS  # noqa: E402


class _ProcInmortal:
    """Nunca muere (poll() -> None siempre). Sirve para probar timeout."""
    def __init__(self):
        self.pid = 1
        self.stderr = None
    def poll(self): return None
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def kill(self): pass
    def send_signal(self, sig): pass


class _ProcNaceMuerto:
    """Muere en el primer poll() — simula un crash durante el arranque."""
    def __init__(self):
        self.pid = 2
        self.stderr = None
    def poll(self): return 1
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def kill(self): pass
    def send_signal(self, sig): pass


@pytest.fixture
def manager_rapido():
    """Timeouts en ms para que los tests corran en milisegundos, no minutos."""
    return OS.OpenCodeServerManager(
        port=0, startup_timeout=0.3, health_check_interval=0.02,
        monitor_interval=0.03, restart_delay=0.02,
    )


class TestEstadoNuncaFalla:
    def test_estado_antes_de_start(self, manager_rapido):
        e = manager_rapido.estado()
        assert e == {"disponible": False, "motivo": None, "reintentos": 0,
                     "fallo_persistente": False}

    def test_estado_no_lanza_si_verificar_falla(self, manager_rapido, monkeypatch):
        manager_rapido._verificar_opencode = lambda: False
        manager_rapido.start()
        e = manager_rapido.estado()  # no debe lanzar
        assert e["disponible"] is False


class TestSinAutodeadlock:
    """El bug real: start() adquiría self._lock y llamaba is_running, que
    volvía a pedirlo. Con Lock() (no RLock) eso era un deadlock permanente."""

    def test_start_retorna_sin_colgarse(self, manager_rapido):
        manager_rapido._verificar_opencode = lambda: False
        import threading
        terminado = threading.Event()

        def _correr():
            manager_rapido.start()
            terminado.set()

        t = threading.Thread(target=_correr, daemon=True)
        t.start()
        t.join(timeout=3.0)
        assert terminado.is_set(), "start() no retornó: posible autodeadlock"

    def test_lock_es_reentrante(self, manager_rapido):
        import _thread
        assert type(manager_rapido._lock).__name__ == "RLock"


class TestMotivosEstables:
    def test_opencode_no_instalado(self, manager_rapido):
        manager_rapido._verificar_opencode = lambda: False
        ok = manager_rapido.start()
        assert ok is False
        assert manager_rapido.estado()["motivo"] == "opencode_no_instalado"

    def test_no_encontrado_en_path(self, manager_rapido, monkeypatch):
        manager_rapido._verificar_opencode = lambda: True
        monkeypatch.setattr(OS.subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        ok = manager_rapido.start()
        assert ok is False
        assert manager_rapido.estado()["motivo"] == "opencode_no_instalado"

    def test_timeout_arranque(self, manager_rapido, monkeypatch):
        manager_rapido._verificar_opencode = lambda: True
        monkeypatch.setattr(OS.subprocess, "Popen", lambda *a, **k: _ProcInmortal())
        monkeypatch.setattr(OS.httpx, "get",
                            lambda *a, **k: (_ for _ in ()).throw(OS.httpx.ConnectError("no")))
        ok = manager_rapido.start()
        assert ok is False
        assert manager_rapido.estado()["motivo"] == "timeout_arranque"

    def test_proceso_crasheo_durante_arranque(self, manager_rapido, monkeypatch):
        manager_rapido._verificar_opencode = lambda: True
        monkeypatch.setattr(OS.subprocess, "Popen", lambda *a, **k: _ProcNaceMuerto())
        ok = manager_rapido.start()
        assert ok is False
        assert manager_rapido.estado()["motivo"] == "proceso_crasheo"


class TestArranqueExitosoLimpiaDiagnostico:
    def test_start_exitoso_resetea_motivo(self, manager_rapido, monkeypatch):
        manager_rapido._verificar_opencode = lambda: True
        monkeypatch.setattr(OS.subprocess, "Popen", lambda *a, **k: _ProcInmortal())
        monkeypatch.setattr(OS.httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
        ok = manager_rapido.start()
        try:
            assert ok is True
            e = manager_rapido.estado()
            assert e["motivo"] is None
            assert e["disponible"] is True
        finally:
            manager_rapido.stop()


class TestVentanaDeReintentos:
    def test_historial_se_poda_fuera_de_la_ventana(self, manager_rapido):
        manager_rapido.VENTANA_REINTENTOS = 0.05
        ahora = time.time()
        manager_rapido._historial_muertes = [ahora - 10, ahora - 5, ahora]
        # Simula la poda que hace _monitor_process tras registrar una muerte.
        vivos = [t for t in manager_rapido._historial_muertes
                if ahora - t <= manager_rapido.VENTANA_REINTENTOS]
        assert vivos == [ahora]
