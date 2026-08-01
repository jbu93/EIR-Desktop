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
poblar_registro_fish_audio() -> None
poblar_registro_completo() -> None  # incluye fish_audio + tools existentes

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


def requiere_red(nombre: str) -> bool:
    """``True`` si el plugin se registró con ``requiere_red=True``.

    Fail-closed en el sentido contrario al resto del módulo: un plugin que NO
    está en el registro (nombre desconocido, o registro aún no poblado)
    devuelve ``False`` — no puede requerir red algo que ni siquiera se sabe
    que existe. Quien decide si esa tool se ejecuta sigue siendo
    ``agent_hooks.run_pre_hooks`` contra la matriz; esto es solo el metadato
    que ``run_pre_hooks`` usa para saber si vale la pena preguntarle a la
    Capa 4 (egress) por un destino de red (Fase F).
    """
    plugin = _REGISTRO_PLUGINS.get(nombre)
    return bool(plugin and plugin.get("requiere_red"))


# ─── Poblar registro con Terminal Tools (M-054) ──────────────────────────
def _importar_terminal():
    """Importa terminal_tools de forma perezosa (relativo, con fallback absoluto)."""
    try:
        from . import terminal_tools
        return terminal_tools
    except ImportError:
        import terminal_tools
        return terminal_tools


def poblar_registro_terminal() -> None:
    """Registra las Terminal Tools nativas del desktop (M-054 · Capa 1).

    Cada tool se registra con un dispatcher que pasa TODA la call por
    ``terminal_tools.ejecutar_tool`` (Schema Validation + sanitizadores).
    La autorización de zona/efecto/requiere_humano la aplica
    ``agent_hooks.run_pre_hooks`` contra data/autonomia_zonas.json — aquí solo
    se declara el ejecutor y su zona (metadato, no autoridad).
    """
    term = _importar_terminal()

    def _dispatcher(nombre: str):
        def _h(**kwargs):
            return term.ejecutar_tool(nombre, kwargs)
        return _h

    registrar("leer_archivo", _dispatcher("leer_archivo"), requiere_red=False, zona="investigacion")
    registrar("listar_archivos", _dispatcher("listar_archivos"), requiere_red=False, zona="investigacion")
    registrar("buscar_texto", _dispatcher("buscar_texto"), requiere_red=False, zona="investigacion")
    registrar("escribir_archivo", _dispatcher("escribir_archivo"), requiere_red=False, zona="producto")
    registrar("ejecutar_comando", _dispatcher("ejecutar_comando"), requiere_red=False, zona="producto")


# ─── Poblar registro con las LSP tools (M-055 Fase 2B) ────────────────────
def _importar_lsp():
    """Importa lsp_tools de forma perezosa (relativo, con fallback absoluto)."""
    try:
        from . import lsp_tools
        return lsp_tools
    except ImportError:
        import lsp_tools
        return lsp_tools


def poblar_registro_lsp() -> None:
    """Registra las LSP Tools (lectura de código con analizador real).

    Mismo patrón que ``poblar_registro_terminal``: el dispatcher pasa toda la
    call por ``lsp_tools.ejecutar_tool`` (Capa 1 + confinamiento). Las tres son
    de zona investigacion · lectura: autónomas, porque leer código no daña nada
    y pedir aprobación por cada consulta convertiría el HITL en ruido.
    """
    lsp = _importar_lsp()

    def _dispatcher(nombre: str):
        def _h(**kwargs):
            return lsp.ejecutar_tool(nombre, kwargs)
        return _h

    for nombre in ("lsp_definicion", "lsp_referencias", "lsp_diagnosticos"):
        registrar(nombre, _dispatcher(nombre), requiere_red=False, zona="investigacion")


# ─── Poblar registro con tools de Fish Audio ──────────────────────────────
def _importar_fish_audio():
    """Importa las funciones de fish_audio_tools de forma perezosa."""
    try:
        from . import fish_audio_tools
        return fish_audio_tools
    except ImportError:
        import fish_audio_tools
        return fish_audio_tools


def poblar_registro_fish_audio() -> None:
    """Registra las tools de Fish Audio TTS en el registro global.

    Tools registradas:
    - hablar_fish_audio: Genera TTS, reproduce y guarda en Downloads
    - listar_voces_fish_audio: Lista voces disponibles en la cuenta
    - establecer_voz_fish_audio: Establece voz por defecto para la sesión
    """
    fish = _importar_fish_audio()

    registrar(
        "hablar_fish_audio",
        fish.hablar_fish_audio,
        requiere_red=True,  # Llama a api.fish.audio
        zona="audio",
    )
    registrar(
        "listar_voces_fish_audio",
        fish.listar_voces_fish_audio,
        requiere_red=True,
        zona="audio",
    )
    registrar(
        "establecer_voz_fish_audio",
        fish.establecer_voz_fish_audio,
        requiere_red=False,  # Solo cambia estado en memoria
        zona="audio",
    )


# ─── Poblar registro completo (para runners) ──────────────────────────────
def poblar_registro_completo() -> None:
    """Puebla el registro con todas las tools disponibles (existentes + fish_audio).

    Se llama desde los runners al arrancar para garantizar que el
    plugin_registry tiene todas las tools declaradas en autonomia_zonas.json.
    """
    # Tools existentes Fase 2 (local_tools)
    try:
        from . import local_tools
        registrar("leer_stl_local", local_tools.leer_stl_local, requiere_red=False, zona="clinica")
        registrar("listar_archivos_paciente", local_tools.listar_archivos_paciente, requiere_red=False, zona="clinica")
        registrar("leer_texto_local", local_tools.leer_texto_local, requiere_red=False, zona="clinica")
    except ImportError:
        pass  # Si no está disponible, continúa

    # Tools Fish Audio
    poblar_registro_fish_audio()

    # Terminal Tools (M-054)
    poblar_registro_terminal()

    # LSP Tools (M-055 Fase 2B)
    poblar_registro_lsp()

    # Tools Fase B1 (producto) - se registran condicionalmente
    try:
        from . import app_control
        registrar("listar_apps_abiertas", app_control.listar_apps_abiertas, requiere_red=False, zona="producto")
        registrar("traer_al_frente", app_control.traer_al_frente, requiere_red=False, zona="producto")
        registrar("escribir_texto_en", app_control.escribir_texto_en, requiere_red=False, zona="producto")
        registrar("capturar_pantalla_app", app_control.capturar_pantalla_app, requiere_red=False, zona="producto")
        registrar("enviar_whatsapp_business", app_control.enviar_whatsapp_business, requiere_red=True, zona="producto")
    except ImportError:
        pass

    # Tools Fase B2 (plantillas)
    try:
        from . import local_tools
        if hasattr(local_tools, "listar_plantillas"):
            registrar("listar_plantillas", local_tools.listar_plantillas, requiere_red=False, zona="producto")
        if hasattr(local_tools, "render_plantilla"):
            registrar("render_plantilla", local_tools.render_plantilla, requiere_red=False, zona="producto")
    except ImportError:
        pass
