"""plugin_registry.py · Registro dinámico de plugins para EIR DR. Desktop.

Permite registrar y consultar herramientas ejecutables de forma centralizada.
La autorización NO la decide el registro: la decide la matriz determinista
``data/autonomia_zonas.json`` (fail-closed, L2) a través de
``agent_hooks.run_pre_hooks``. Este registro es SOLO el índice de ejecutores.

Funciones
---------
registrar(nombre, handler, requiere_red=False, zona="lectura")
obtener(nombre) -> Callable | None
listar() -> list[str]
validar_autorizado(nombre) -> bool

El registro es global al proceso (módulo) y se puebla una vez al arrancar
cada runner (o desde el orquestador), sin duplicar el estado del proyecto.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# Módulo de estado global del proceso. Clave: nombre de la tool.
# Valor: dict con "handler", "requiere_red" y "zona".
_REGISTRO_PLUGINS: dict[str, dict[str, Any]] = {}


def registrar(nombre: str, handler: Callable[..., Any],
              requiere_red: bool = False, zona: str = "lectura") -> None:
    """Registra una función ejecutable como plugin del agente.

    Parameters
    ----------
    nombre : str
        Nombre único con el que el LLM invocará la herramienta.
    handler : Callable
        Función que ejecuta la lógica local (recibe ``**kwargs`` del paso).
    requiere_red : bool
        ``True`` si la tool necesita conexión a un servicio externo (el
        hook offline lo consultará en fases futuras).
    zona : str
        Zona declarada en la matriz de autonomía ("lectura" por defecto).
    """
    _REGISTRO_PLUGINS[nombre] = {
        "handler": handler,
        "requiere_red": bool(requiere_red),
        "zona": zona,
    }


def obtener(nombre: str) -> Optional[Callable[..., Any]]:
    """Devuelve la función ejecutable de un plugin registrado, o ``None``."""
    plugin = _REGISTRO_PLUGINS.get(nombre)
    return plugin["handler"] if plugin else None


def listar() -> list[str]:
    """Lista los nombres de todos los plugins registrados."""
    return list(_REGISTRO_PLUGINS.keys())


def validar_autorizado(nombre: str) -> bool:
    """Fail-closed (L2): un plugin existe y está autorizado solo si está registrado.

    Nota: el control de AUTONOMÍA real (zona/efecto/requiere_humano) vive en
    ``data/autonomia_zonas.json`` y lo aplica ``agent_hooks.run_pre_hooks``.
    Esta función solo responde "¿está en el índice de ejecutores?".
    """
    return nombre in _REGISTRO_PLUGINS
