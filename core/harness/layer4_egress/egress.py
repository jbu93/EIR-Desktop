# -*- coding: utf-8 -*-
"""
core/harness/layer4_egress/egress.py · Egress default-deny (Fase F)
═══════════════════════════════════════════════════════════════════════════════
``verificar_destino(host, tool) -> (bool, motivo)`` decide si un host puede
ser destino de red para una tool call. El PDP sigue siendo ``data/egress_allowlist.json``
como DATA versionada (mismo patrón que ``data/autonomia_zonas.json`` y D048):
la lógica de este módulo no decide por sí sola qué host es bueno, solo aplica
la declaración.

Fail-closed (L2): si el archivo de allowlist no existe, no se puede leer o
está corrupto → SE DENIEGA TODO con el motivo estable ``allowlist_no_disponible``.
Un archivo ausente no es "sin opinión", es "la peor opinión posible".

Host no declarado → denegado (``host_no_declarado``), sin excepciones tácitas.

Subdominios: NO se permiten implícitamente. Cada entrada del allowlist es una
identidad EXACTA, no un patrón de sufijo. Dos razones, no solo una:
  1. Aceptar sufijos abriría la puerta a que un atacante registre
     ``evil-api.fish.audio.atacante.com`` y cuele por contener la cadena del
     host legítimo si la comparación fuera ingenua.
  2. Aunque la comparación de sufijo se hiciera bien (con un punto delante),
     seguiría regalando acceso a subdominios que NADIE declaró ni auditó
     explícitamente. Si mañana hace falta ``access.api.fish.audio``, se
     declara aparte, a propósito — no por herencia automática de su padre.

Kill-switch ``EIR_EGRESS_ENFORCE``, leído en TIEMPO DE LLAMADA (L10). Nace
ENCENDIDO (default ``"1"`` si la variable no está definida) — esto INVIERTE a
propósito el patrón de D078 (auto-update nace APAGADO): allá lo seguro era no
activar una capacidad nueva sin permiso explícito; aquí lo seguro es DENEGAR
salida de red no declarada, así que la guarda nace activa y el Soberano tiene
que apagarla explícitamente si la necesita. Con ``EIR_EGRESS_ENFORCE=0`` el
destino se PERMITE (incluso uno que de otro modo se denegaría), PERO SE SIGUE
REGISTRANDO en el audit log (categoría ``EGRESS``): apagar una guarda no la
vuelve invisible. Si algo salió hacia un host no declarado, tiene que quedar
rastro de que ocurrió Y de que la guarda estaba apagada cuando pasó — sin eso,
el kill-switch sería un agujero silencioso, no un interruptor auditable.

El logging JAMÁS puede tumbar la decisión de egress (mismo criterio que
``core/emailer.py::_registrar_fallo``): se envuelve en try/except que traga
cualquier fallo de escritura.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Motivos estables (nunca contenido, L6): el caller decide qué mostrar.
MOTIVO_ALLOWLIST_NO_DISPONIBLE = "allowlist_no_disponible"
MOTIVO_HOST_NO_DECLARADO = "host_no_declarado"
MOTIVO_HOST_PERMITIDO = "host_permitido"
MOTIVO_ENFORCE_APAGADO = "enforce_apagado"


def _ruta_allowlist() -> Path:
    """Ruta del allowlist, leída en TIEMPO DE LLAMADA (L10).

    ``EIR_EGRESS_ALLOWLIST`` permite que arneses/tests apunten a un archivo
    temporal (o a uno inexistente, para probar G4) sin tocar el
    ``data/egress_allowlist.json`` real del repo — mismo patrón que
    ``EIR_SANDBOX_ROOT``, ``EIR_HITL_SECRET`` y ``EIR_AUDIT_LOG``.
    """
    override = os.environ.get("EIR_EGRESS_ALLOWLIST", "").strip()
    if override:
        return Path(override)
    # core/harness/layer4_egress/egress.py -> layer4_egress -> harness -> core -> RAIZ
    raiz = Path(__file__).resolve().parent.parent.parent.parent
    return raiz / "data" / "egress_allowlist.json"


def _cargar_hosts() -> dict[str, Any] | None:
    """Devuelve el dict de hosts declarados, o ``None`` si el archivo falta,
    no se puede leer o tiene una forma inesperada (fail-closed: cualquier
    duda sobre la allowlist se trata como "no hay allowlist")."""
    try:
        with open(_ruta_allowlist(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    hosts = data.get("hosts") if isinstance(data, dict) else None
    return hosts if isinstance(hosts, dict) else None


def _normalizar_host(host: str) -> str:
    """Normaliza un host suelto o una URL completa a solo el hostname, en
    minúsculas, sin esquema ni puerto. Acepta indistintamente
    ``"api.fish.audio"``, ``"https://api.fish.audio/v1/tts"``,
    ``"API.FISH.AUDIO"`` o ``"api.fish.audio:443"``."""
    valor = (host or "").strip()
    if not valor:
        return ""
    # urlparse solo reconoce el host si hay "//" delante; sin esquema, se le
    # antepone uno vacío para reusar el parser en vez de reinventarlo.
    con_esquema = valor if "//" in valor else f"//{valor}"
    parsed = urlparse(con_esquema)
    hostname = parsed.hostname
    if not hostname:
        # último recurso: recorte manual si urlparse no pudo (valor raro)
        hostname = valor.split("/")[0].split(":")[0]
    return hostname.lower().strip()


def _enforce_activo() -> bool:
    """Kill-switch leído en tiempo de llamada (L10). Nace ENCENDIDO: solo
    ``EIR_EGRESS_ENFORCE=0`` explícito lo apaga (ver docstring del módulo)."""
    return os.environ.get("EIR_EGRESS_ENFORCE", "1").strip() != "0"


def _auditar(permitido: bool, host: str, tool: str, motivo: str) -> None:
    """Deja rastro en el audit log (categoría EGRESS). Nunca contenido: solo
    el host normalizado (no es PHI, es infraestructura), la tool y un motivo
    estable. Envuelto en try/except: el logging jamás tumba la decisión de
    egress, igual que ``core/emailer.py::_registrar_fallo``."""
    try:
        from core import audit_logger
        nivel = audit_logger.info if permitido else audit_logger.warn
        nivel("EGRESS", host=host, tool=tool, permitido=permitido, motivo=motivo)
    except Exception:
        pass


def verificar_destino(host: str, tool: str) -> tuple[bool, str]:
    """Decide si ``host`` es un destino de red permitido para ``tool``.

    Returns
    -------
    tuple[bool, str]
        ``(permitido, motivo)``. ``motivo`` es siempre un código estable:
        ``allowlist_no_disponible`` · ``host_no_declarado`` ·
        ``host_permitido`` · ``enforce_apagado``.
    """
    host_norm = _normalizar_host(host)
    hosts = _cargar_hosts()
    activo = _enforce_activo()

    if not activo:
        # El kill-switch está apagado a propósito: se permite (incluso un
        # host que de otro modo se negaría), pero se audita SIEMPRE — apagar
        # la guarda no la vuelve invisible.
        _auditar(True, host_norm or host, tool, MOTIVO_ENFORCE_APAGADO)
        return True, MOTIVO_ENFORCE_APAGADO

    if hosts is None:
        _auditar(False, host_norm or host, tool, MOTIVO_ALLOWLIST_NO_DISPONIBLE)
        return False, MOTIVO_ALLOWLIST_NO_DISPONIBLE

    if not host_norm or host_norm not in hosts:
        _auditar(False, host_norm or host, tool, MOTIVO_HOST_NO_DECLARADO)
        return False, MOTIVO_HOST_NO_DECLARADO

    _auditar(True, host_norm, tool, MOTIVO_HOST_PERMITIDO)
    return True, MOTIVO_HOST_PERMITIDO
