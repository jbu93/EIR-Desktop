"""
core/guardas.py · Telemetría de las guardas de runtime (fork de código abierto)
═══════════════════════════════════════════════════════════════════════════════
En el producto propietario de EIR DR. este módulo cataloga todas las guardas
de runtime (candados de visión, simulador, perímetro, validador clínico). En
este fork de código abierto el catálogo contiene SOLO la frontera de
autonomía (``core/autonomia.py``), que es la única guarda que el harness
agéntico expone aquí.

Contrato duro (las tres reglas que no se negocian):
  1. **Nunca rompe el flujo.** La guarda ya hizo su trabajo; un telemetro caído no
     puede tumbar una protección. Todo error se traga.
  2. **Cero PHI.** El payload se construye por WHITELIST de claves declaradas: lo que
     no está declarado, se descarta. Jamás viaja texto de paciente.
  3. **Solo ids del CATALOGO.** Un id suelto sería una métrica huérfana que nadie sabe
     interpretar seis meses después.

API:
    registrar(guarda_id, **contexto) -> bool   # True si se emitió
    contadores() -> dict                        # acumulado desde el arranque del worker
    catalogo_publico() -> list                  # "estas son mis guardas"
    resumen(eventos) -> dict                    # agrega desde el audit trail
"""
from __future__ import annotations

import os
import threading

# ── Catálogo declarativo ─────────────────────────────────────────────────────
# Cada guarda declara QUÉ PROTEGE. Añadir una guarda nueva SIN registrarla
# aquí es un error que la frontera de autonomía caza.
CATALOGO: dict[str, dict] = {
    "autonomia.denegada": {
        "nombre": "Frontera de autonomía",
        "que_protege": "Un agente actuando solo NO puede tocar la historia clínica ni "
                       "firmar. Lo que no está declarado en data/autonomia_zonas.json "
                       "se niega: fail-closed.",
        "modulo": "core/autonomia.py",
    },
}

# Claves de contexto PERMITIDAS en el payload. Todo lo demás se descarta: es la
# barrera anti-PHI. Nunca añadir aquí nada que pueda contener texto de paciente.
_CLAVES_SEGURAS = frozenset({
    "motivo", "tipo", "nivel", "ruta", "modulo", "n", "cantidad", "codigo", "origen",
})
_MAX_VALOR = 80          # los valores se acotan: ni un texto largo se cuela por descuido

CATEGORIA = "GUARDA"     # debe existir en audit_logger.CATEGORIES

_lock = threading.Lock()
_contadores: dict[str, int] = {}


def habilitado() -> bool:
    return os.getenv("EIR_TELEMETRIA_GUARDAS_ENABLED", "1").strip() == "1"


def _payload_seguro(guarda_id: str, contexto: dict | None) -> dict:
    """Construye el payload por WHITELIST: lo no declarado se descarta.
    Esta función es la barrera anti-PHI y por eso es pura y testeable aparte."""
    payload = {"guarda": guarda_id}
    for clave, valor in (contexto or {}).items():
        if clave not in _CLAVES_SEGURAS:
            continue                       # descartado a propósito (posible PHI)
        if isinstance(valor, (int, float)):
            payload[clave] = valor
        else:
            payload[clave] = str(valor)[:_MAX_VALOR]
    return payload


def registrar(guarda_id: str, **contexto) -> bool:
    """Registra que una guarda DISPARÓ. Devuelve True si se emitió.

    Nunca lanza: si la telemetría falla, la guarda ya hizo su trabajo igual."""
    try:
        if not habilitado():
            return False
        if guarda_id not in CATALOGO:
            return False                   # id huérfano: no se emite
        with _lock:
            _contadores[guarda_id] = _contadores.get(guarda_id, 0) + 1
        from core import audit_logger as audit
        audit.warn(CATEGORIA, **_payload_seguro(guarda_id, contexto))
        return True
    except Exception:                      # noqa: BLE001 — un log jamás tumba una guarda
        return False


def contadores() -> dict:
    """Disparos acumulados DESDE EL ARRANQUE de este worker."""
    with _lock:
        return dict(_contadores)


def catalogo_publico() -> list:
    """Para el panel: qué guardas existen y qué protege cada una."""
    return [{"id": gid, **{k: v for k, v in meta.items()}} for gid, meta in CATALOGO.items()]


def resumen(eventos: list) -> dict:
    """Agrega disparos por guarda a partir de entradas del audit trail.
    Puro (recibe los eventos, no los lee)."""
    conteo: dict[str, int] = {}
    for e in eventos or []:
        if (e or {}).get("category") != CATEGORIA:
            continue
        gid = ((e.get("payload") or {}).get("guarda") or "").strip()
        if gid:
            conteo[gid] = conteo.get(gid, 0) + 1
    return {
        "total_disparos": sum(conteo.values()),
        "por_guarda": dict(sorted(conteo.items(), key=lambda kv: -kv[1])),
        "guardas_catalogadas": len(CATALOGO),
        "guardas_que_dispararon": len(conteo),
        "desde_arranque": contadores(),
    }


def _reset() -> None:
    """Solo para tests."""
    with _lock:
        _contadores.clear()
