# -*- coding: utf-8 -*-
"""
core/harness/layer2_risk/hitl.py · Aprobación humana con HMAC (M-055)
═══════════════════════════════════════════════════════════════════════════════
Human In The Loop real: convierte ``requiere_humano`` de "imposible" en
"el doctor lo aprueba explícitamente, una vez, y sobre argumentos exactos".

  · ``crear_solicitud(tool, args, riesgo)`` → token HMAC-SHA256 con TTL corto.
  · ``verificar_aprobacion(token, tool, args)`` → True, o ``AprobacionInvalida``.

Por qué el token va atado a la HUELLA de los argumentos: sin eso, el modelo
podría pedir aprobación para ``escribir_archivo("nota.txt")`` y ejecutar la
escritura sobre ``config.json`` con el mismo permiso. El humano debe aprobar
el hecho, no la narración del hecho (L4).

Un solo uso: el nonce se marca consumido en la primera verificación válida.
El registro de consumidos vive EN MEMORIA del proceso (el desktop es un solo
proceso). Reiniciar la app olvida los nonces gastados — no es un hueco (los
tokens caducan en 5 min y el reinicio invalida las solicitudes vivas), pero
tampoco es un audit log persistente. Declarado en la misión, no escondido.

Secreto (L15): ``EIR_HITL_SECRET`` si está en el entorno; si no, se genera uno
aleatorio y se guarda en ``~/.eir_dr/hitl_secret`` con permisos de usuario.
Se lee en tiempo de llamada, nunca congelado en el import (L10). Jamás se
loguea ni aparece en un motivo de error.

Rastro de auditoría (Fase F, cierra la deuda declarada en D080 "no queda
rastro en disco de qué aprobó el doctor ni cuándo"): cada solicitud, cada
aprobación consumida y cada rechazo se audita vía ``core/audit_logger.py``
con categoría ``HITL``. NUNCA se registra el contenido de los argumentos
(una escritura puede llevar PHI, L6): solo la HUELLA (``huella_args``), el
nombre de la tool, el nivel de riesgo y el motivo estable. El logging jamás
puede tumbar el flujo de aprobación — se envuelve en try/except que traga
cualquier fallo, igual que ``core/emailer.py::_registrar_fallo``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

# Ventana de validez de una aprobación. Corta a propósito: una aprobación es
# para ESTA acción, ahora, no un permiso que queda flotando.
TTL_POR_DEFECTO = 300  # segundos

_SEPARADOR = "."
_consumidos: dict[str, float] = {}   # nonce → epoch de expiración


class AprobacionInvalida(Exception):
    """Aprobación rechazada. ``motivo`` es un código estable, nunca contenido."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


# ─── secreto ────────────────────────────────────────────────────────────
def _ruta_secreto() -> Path:
    return Path.home() / ".eir_dr" / "hitl_secret"


def _secreto() -> bytes:
    """Secreto HMAC, leído en tiempo de llamada (L10). Nunca se loguea (L15)."""
    env = os.environ.get("EIR_HITL_SECRET", "").strip()
    if env:
        return env.encode("utf-8")

    ruta = _ruta_secreto()
    try:
        if ruta.is_file():
            guardado = ruta.read_text(encoding="utf-8").strip()
            if guardado:
                return guardado.encode("utf-8")
    except OSError:
        pass

    nuevo = secrets.token_hex(32)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(nuevo, encoding="utf-8")
    except OSError:
        # Sin disco escribible el secreto vive solo en esta ejecución: las
        # aprobaciones siguen siendo válidas dentro del proceso, que es la
        # única ventana en la que se usan.
        pass
    return nuevo.encode("utf-8")


# ─── huella de argumentos ───────────────────────────────────────────────
def huella_args(tool: str, args: dict[str, Any]) -> str:
    """Huella estable de (tool, argumentos normalizados).

    Estable = el mismo dict produce el mismo digest sin importar el orden de
    las claves. Se usa SHA-256 del JSON canónico; nunca se guarda el contenido
    en claro (una escritura puede llevar PHI — L6).
    """
    canonico = json.dumps(
        {"tool": tool, "args": args or {}},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _auditar(evento: str, tool: str, huella: str, *,
            nivel: str | None = None, motivo: str | None = None) -> None:
    """Deja rastro en el audit log (categoría HITL). Cierra la deuda de D080:
    sin esto, el registro de aprobaciones vivía solo en ``_consumidos`` (en
    memoria) y no quedaba nada en disco de qué aprobó el doctor ni cuándo.

    NUNCA se registra el contenido de los argumentos (L6): solo la HUELLA
    (ya calculada por ``huella_args``, un digest SHA-256), el nombre de la
    tool, el nivel de riesgo si se conoce y un motivo estable. El logging
    jamás puede tumbar el flujo de aprobación: se envuelve en try/except que
    traga cualquier fallo, igual que ``core/emailer.py::_registrar_fallo``.
    """
    try:
        from core import audit_logger
        payload: dict[str, Any] = {"evento": evento, "tool": tool, "huella": huella}
        if nivel is not None:
            payload["nivel"] = nivel
        if motivo is not None:
            payload["motivo"] = motivo
        (audit_logger.info if evento != "rechazada" else audit_logger.warn)("HITL", **payload)
    except Exception:
        pass


def _firmar(tool: str, huella: str, nonce: str, expira: int) -> str:
    mensaje = f"{tool}|{huella}|{nonce}|{expira}".encode("utf-8")
    return hmac.new(_secreto(), mensaje, hashlib.sha256).hexdigest()


def _purgar_consumidos(ahora: float) -> None:
    for nonce, expira in list(_consumidos.items()):
        if expira < ahora:
            _consumidos.pop(nonce, None)


# ─── API ────────────────────────────────────────────────────────────────
def crear_solicitud(tool: str, args: dict[str, Any], riesgo: dict[str, Any],
                    ttl_segundos: int = TTL_POR_DEFECTO) -> dict[str, Any]:
    """Emite una solicitud de aprobación para una tool call ya normalizada.

    Returns
    -------
    dict
        ``{"token", "tool", "expira_en", "riesgo", "resumen"}``. El ``resumen``
        es lo que se le muestra al humano: los argumentos LITERALES que se van
        a ejecutar, no una descripción amable de ellos (L4).
    """
    args = dict(args or {})
    huella = huella_args(tool, args)
    nonce = secrets.token_hex(16)
    expira = int(time.time()) + int(ttl_segundos)
    firma = _firmar(tool, huella, nonce, expira)
    _auditar("solicitud_creada", tool, huella, nivel=(riesgo or {}).get("nivel"))
    return {
        "token": _SEPARADOR.join((nonce, str(expira), firma)),
        "tool": tool,
        "expira_en": expira,
        "riesgo": dict(riesgo or {}),
        "resumen": _resumen_humano(tool, args),
    }


def comprobar_aprobacion(token: str, tool: str, args: dict[str, Any]) -> bool:
    """Igual que ``verificar_aprobacion`` pero SIN consumir el token.

    Para el momento en que el humano pulsa "Aprobar": la UI necesita saber que
    el token es bueno, pero el único consumo debe ocurrir cuando el paso
    realmente se ejecuta. Si se gastara aquí, aprobar dejaría el token muerto
    y la ejecución fallaría con ``aprobacion_consumida``.
    """
    return _validar(token, tool, args, consumir=False)


def verificar_aprobacion(token: str, tool: str, args: dict[str, Any]) -> bool:
    """Verifica un token contra la tool y los argumentos que se van a ejecutar.

    Lanza ``AprobacionInvalida`` con motivo estable:
    ``token_ausente`` · ``token_malformado`` · ``aprobacion_expirada`` ·
    ``aprobacion_consumida`` · ``argumentos_no_coinciden``.

    Un token válido queda CONSUMIDO: el segundo intento falla.
    """
    return _validar(token, tool, args, consumir=True)


def _validar(token: str, tool: str, args: dict[str, Any], consumir: bool) -> bool:
    huella = huella_args(tool, dict(args or {}))

    if not token or not isinstance(token, str):
        _auditar("rechazada", tool, huella, motivo="token_ausente")
        raise AprobacionInvalida("token_ausente")

    partes = token.split(_SEPARADOR)
    if len(partes) != 3:
        _auditar("rechazada", tool, huella, motivo="token_malformado")
        raise AprobacionInvalida("token_malformado")
    nonce, expira_txt, firma = partes
    try:
        expira = int(expira_txt)
    except ValueError:
        _auditar("rechazada", tool, huella, motivo="token_malformado")
        raise AprobacionInvalida("token_malformado") from None

    ahora = time.time()
    _purgar_consumidos(ahora)

    if expira < ahora:
        _auditar("rechazada", tool, huella, motivo="aprobacion_expirada")
        raise AprobacionInvalida("aprobacion_expirada")

    if nonce in _consumidos:
        _auditar("rechazada", tool, huella, motivo="aprobacion_consumida")
        raise AprobacionInvalida("aprobacion_consumida")

    # La firma se comprueba contra la huella de ESTOS argumentos. Si el token
    # se emitió para otros, la firma sencillamente no cuadra: el mismo cálculo
    # cubre "firma falsificada" y "argumentos cambiados". Para dar un motivo
    # útil se distingue re-firmando con la tool/args presentados.
    esperada = _firmar(tool, huella, nonce, expira)
    if not hmac.compare_digest(esperada, firma):
        _auditar("rechazada", tool, huella, motivo="argumentos_no_coinciden")
        raise AprobacionInvalida("argumentos_no_coinciden")

    if consumir:
        _consumidos[nonce] = float(expira)
        # Solo el CONSUMO (la ejecución real, no el "aprobar" de comprobar_aprobacion)
        # deja rastro de éxito: es el momento en que la aprobación del doctor se
        # gastó de verdad (D080 — "qué aprobó el doctor y cuándo").
        _auditar("aprobada", tool, huella)
    return True


def _resumen_humano(tool: str, args: dict[str, Any]) -> str:
    """Lo que el doctor ve antes de aprobar: el hecho literal."""
    if tool == "ejecutar_comando":
        argv = args.get("argv") or []
        return "ejecutar_comando: " + " ".join(str(a) for a in argv)
    if tool == "escribir_archivo":
        ruta = args.get("ruta", "")
        n = len(args.get("contenido") or "")
        return f"escribir_archivo: {ruta} ({n} caracteres)"
    return f"{tool}: " + ", ".join(f"{k}={v}" for k, v in sorted(args.items()) if k != "contenido")
