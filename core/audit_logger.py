"""
core/audit_logger.py
────────────────────
Audit log estructurado en JSON Lines.

Cada evento es una línea JSON parseable con:
    timestamp_utc, level, category, payload

Niveles: INFO | WARN | ERROR | CRITICAL
Categorías: CONSULTATION | VALIDATOR_BLOCK | OFFLINE | GROQ_OK | GROQ_ERROR | RATE_LIMIT | BOOT | HEALTH

Reemplaza el `audit_trail.log` plano. Compatible hacia atrás: si el archivo
existía como texto plano, se respeta y se agrega líneas JSON al final.

Lectura recomendada con jq:
    cat audit_trail.log | grep -v '^\\[' | jq 'select(.level=="CRITICAL")'
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

# §AGENTS.md Ley #3 — path absoluto
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_PROJECT_ROOT, "audit_trail.log")


def _log_path() -> str:
    """§M-021 · La ruta se resuelve en cada escritura para poder redirigir el log a un
    temporal en tests (EIR_AUDIT_LOG) y no contaminar el audit_trail.log de producción.
    En prod la variable no existe → se usa el path de siempre."""
    return os.environ.get("EIR_AUDIT_LOG", "").strip() or _LOG_PATH

LEVELS = ("INFO", "WARN", "ERROR", "CRITICAL")
CATEGORIES = (
    "CONSULTATION",      # consulta clínica completada (OK)
    "VALIDATOR_BLOCK",   # guardrail clínico bloqueó
    "OFFLINE",           # modo offline (sin Groq)
    "GROQ_OK",           # respuesta Groq exitosa
    "GROQ_ERROR",        # fallo de Groq
    "RATE_LIMIT",        # rate limit aplicado
    "BOOT",              # arranque del sistema
    "HEALTH",            # health check
    "PAYLOAD_INVALID",   # Pydantic rechazó payload
    "GUARDA",            # §M-018 · una guarda de runtime DISPARÓ (core/guardas.py)
    "INPUT_GUARD",       # §M-018 · entrada peligrosa detectada (estaba fuera → se degradaba)
    "SECURITY",          # perímetro / tarpit (estaba fuera → se degradaba a CONSULTATION)
    "SONRISA_SIM",       # simulador de sonrisas (evento sin PHI)
)


def log(level: str, category: str, payload: dict | None = None) -> None:
    """
    Escribe una línea JSON al audit log.
    Tolerante a fallos: si no puede escribir, falla silencioso (nunca rompe el flujo).
    """
    if level not in LEVELS:
        level = "INFO"
    if category not in CATEGORIES:
        category = "CONSULTATION"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "level": level,
        "category": category,
        "payload": payload or {},
    }
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Fallback: stderr. NUNCA bloqueamos el flujo por un log.
        try:
            print(f"[audit_logger] fallback stderr: {entry} (error: {e})")
        except Exception:
            pass


# Helpers semánticos para no escribir el nombre de la categoría a mano
def info(category: str, **payload):    log("INFO", category, payload)
def warn(category: str, **payload):    log("WARN", category, payload)
def error(category: str, **payload):   log("ERROR", category, payload)
def critical(category: str, **payload): log("CRITICAL", category, payload)


if __name__ == "__main__":
    info("BOOT", source="audit_logger_smoke_test", pid=os.getpid())
    warn("RATE_LIMIT", uid="test-uid", route="/api/eir/narrativa")
    critical("VALIDATOR_BLOCK", specialty="endodoncia", reason="NaOCl+CHX")
    print(f"OK: 3 entradas escritas a {_LOG_PATH}")
