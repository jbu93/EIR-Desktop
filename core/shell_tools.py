"""
core/shell_tools.py · EIR-Desktop (open-source / open core)
═══════════════════════════════════════════════════════════════
Fork público del catálogo de tools del orquestador agéntico.

Este archivo es un fork DEGRADADO del módulo propietario homónimo de
EIR DR. (https://eirdr.com). Las herramientas que en el
producto cerrado consultan el RAG científico curado, el workspace
clínico, las fuentes normativas .gov.co y la telemetría del ecosistema viven
en el backend con suscripción (https://eirdr.com/api/v1/inference).

Aquí esas tools se degradan HONESTO (L2/L4 de AGENTS.md): nunca inventan
datos; declaran que la capacidad completa está disponible con suscripción
EIR DR. La única tool 100% funcional en este fork es ``ver_protocolo``,
que lee el catálogo de protocolos local si existe (``skills/``) y, si no
existe, declara su ausencia sin falsificar contenido.

Cada tool retorna un dict estructurado:
  { "ok": bool, "data": {...}, "card": "nombre_card"|None, "resumen": str }
- resumen: texto que el LLM usa para narrar al usuario
- card: si != None, el frontend hidrata esa plantilla
"""

import sys
from pathlib import Path

_SUSCRIPCION_MSG = (
    "Requiere suscripción EIR DR. Esta capacidad completa está disponible "
    "en https://eirdr.com (eirdr.com/api/v1/inference). En la versión "
    "gratuita no fabrico este dato."
)


def _recurso(relativo: str) -> Path:
    """Resuelve un archivo empaquetado: en bundle PyInstaller usa sys._MEIPASS,
    en el repo la raiz del proyecto. Sin esto, ver_protocolo no encuentra
    skills/<esp>/protocol.md dentro del exe empaquetado."""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / relativo
    return Path(__file__).resolve().parent.parent / relativo


_BASE = _recurso("")
_SPECIALTIES = ["odontologia_general", "endodoncia", "ortodoncia", "cirugia_oral", "odontopediatria", "periodoncia", "rehabilitacion_oral", "tecnico_dental", "auxiliar"]


# ─────────────────────────────────────────────────────────────
# TOOL · ver_protocolo  (REAL — lee el catálogo local si existe)
# ─────────────────────────────────────────────────────────────
def ver_protocolo(especialidad: str) -> dict:
    """Devuelve el protocolo clínico local (fallback offline) de una especialidad."""
    esp = (especialidad or "").strip().lower()
    if esp not in _SPECIALTIES:
        return {"ok": False, "data": {}, "card": None,
                "resumen": f"No tengo un protocolo para '{especialidad}'. "
                           f"Especialidades disponibles: {', '.join(_SPECIALTIES)}."}
    ruta = _BASE / "skills" / esp / "protocol.md"
    if not ruta.exists():
        return {"ok": False, "data": {}, "card": None,
                "resumen": f"El protocolo de {esp} no está disponible en la "
                           "versión gratuita. El catálogo clínico curado "
                           "requiere suscripción EIR DR (https://eirdr.com)."}
    try:
        contenido = ruta.read_text(encoding="utf-8")
        # preview: primeras ~1200 chars para no saturar el chat
        preview = contenido[:1200]
        return {"ok": True,
                "data": {"especialidad": esp, "preview": preview,
                         "completo_len": len(contenido)},
                "card": "protocolo",
                "resumen": f"Aquí tienes el protocolo base de {esp} (vista previa)."}
    except Exception as e:
        print(f"[SHELL_TOOL ver_protocolo] {type(e).__name__}: {e}")
        return {"ok": False, "data": {}, "card": None,
                "resumen": "No pude leer el protocolo en este momento."}


# ─────────────────────────────────────────────────────────────
# TOOL · buscar_doctor  (DEGRADADA — requiere suscripción EIR DR)
# ─────────────────────────────────────────────────────────────
def buscar_doctor(ciudad: str = None, especialidad: str = None,
                  atiende_extranjeros: bool = None) -> dict:
    """Fork público: el catálogo de especialistas vive en el backend EIR DR."""
    return {"ok": True, "data": {}, "card": None, "resumen": _SUSCRIPCION_MSG}


# ─────────────────────────────────────────────────────────────
# TOOL · pulso_ecosistema  (DEGRADADA — telemetría del backend)
# ─────────────────────────────────────────────────────────────
def pulso_ecosistema(tipo: str = None) -> dict:
    """Fork público: la telemetría del ecosistema vive en el backend EIR DR."""
    return {"ok": True, "data": {}, "card": None, "resumen": _SUSCRIPCION_MSG}


# ─────────────────────────────────────────────────────────────
# TOOL · info_cad_protesis  (DEGRADADA — flujo CAD propietario)
# ─────────────────────────────────────────────────────────────
def info_cad_protesis(tooth: str = None) -> dict:
    """Fork público: el flujo CAD de prótesis fija vive en el backend EIR DR."""
    return {"ok": True, "data": {}, "card": None, "resumen": _SUSCRIPCION_MSG}


# ─────────────────────────────────────────────────────────────
# TOOL · buscar_evidencia_cientifica  (DEGRADADA — RAG curado)
# ─────────────────────────────────────────────────────────────
def buscar_evidencia_cientifica(consulta: str = None, especialidad: str = None) -> dict:
    """Fork público: el RAG científico curado vive en el backend EIR DR."""
    return {"ok": True, "data": {}, "card": None, "resumen": _SUSCRIPCION_MSG}


# ─────────────────────────────────────────────────────────────
# TOOL · buscar_normativa_salud  (DEGRADADA — fuentes oficiales)
# ─────────────────────────────────────────────────────────────
def buscar_normativa_salud(consulta: str = None) -> dict:
    """Fork público: la consulta normativa .gov.co vive en el backend EIR DR."""
    return {"ok": True, "data": {}, "card": None, "resumen": _SUSCRIPCION_MSG}


# ─────────────────────────────────────────────────────────────
# ESQUEMAS PARA FUNCTION-CALLING
# ─────────────────────────────────────────────────────────────
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "ver_protocolo",
            "description": "Muestra el protocolo clínico local de una especialidad "
                           "cuando está disponible en la versión gratuita.",
            "parameters": {
                "type": "object",
                "properties": {
                    "especialidad": {"type": "string", "enum": _SPECIALTIES},
                },
                "required": ["especialidad"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_doctor",
            "description": "Busca odontólogos/especialistas disponibles para agendar una cita. "
                           "Requiere suscripción EIR DR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ciudad": {"type": "string", "description": "Ciudad, ej. Cali"},
                    "especialidad": {"type": "string", "enum": _SPECIALTIES},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pulso_ecosistema",
            "description": "Consulta la actividad reciente del ecosistema. "
                           "Requiere suscripción EIR DR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["EIR_CONSULTA", "WS_RESERVA_CREADA", "SHELL_CONVERSAR"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_evidencia_cientifica",
            "description": "Busca artículos científicos revisados por pares con DOI/PMID. "
                           "Requiere suscripción EIR DR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string",
                                 "description": "Tema clínico a buscar, ej. 'supervivencia de carillas cerámicas'"},
                    "especialidad": {"type": "string", "enum": _SPECIALTIES},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_cad_protesis",
            "description": "Explica el flujo de diseño CAD de prótesis fija. "
                           "Requiere suscripción EIR DR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tooth": {"type": "string", "description": "Número FDI del diente, ej. 16"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_normativa_salud",
            "description": "Consulta fuentes OFICIALES colombianas (.gov.co) sobre temas "
                           "administrativos/legales de salud. Requiere suscripción EIR DR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string",
                                 "description": "Tema normativo/administrativo a consultar."},
                },
                "required": ["consulta"],
            },
        },
    },
]

# Whitelist de ejecución: solo estas funciones son invocables.
TOOLS_EXEC = {
    "buscar_doctor": buscar_doctor,
    "ver_protocolo": ver_protocolo,
    "pulso_ecosistema": pulso_ecosistema,
    "info_cad_protesis": info_cad_protesis,
    "buscar_evidencia_cientifica": buscar_evidencia_cientifica,
    "buscar_normativa_salud": buscar_normativa_salud,
}
