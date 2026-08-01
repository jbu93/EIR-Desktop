"""verificacion_ui.py · Verificación del estado real de la app (Fase B2 · D074).

Anti-error, no anti-baneo. Antes de escribir y después de enviar, EIR verifica
contra el árbol UIA de la ventana que el estado sea el esperado. WhatsApp
Business (Electron) expone sus controles a UIA: el campo de mensaje, el nombre
del chat activo y los mensajes son elementos legibles SIN OCR ni tesseract
(dependencia pesada innecesaria). El screenshot queda como respaldo visual.

Tri-estado honesto (L2 sin paranoia):
  · ok=True  → el estado coincide con lo esperado.
  · ok=False → el estado CONTRADICE lo esperado (abortar: fail-closed).
  · ok=None  → el estado no es legible por UIA (no asumir nada: el humano
               decide con el preview; jamás afirmar "verificado").

Ninguna de estas funciones muta el estado de la app: solo leen. Sin ventana
real responden fail-closed sin lanzar excepciones.
"""
from __future__ import annotations

# Tipos de control UIA que cuentan como "campo de texto editable".
_CAMPOS_EDITABLES = ("Edit", "Document")
_TIPOS_TEXTO = ("Text", "Static", "ListItem")


def _es_wrapper_valido(ventana) -> bool:
    """True solo si hay una ventana real que responder a UIA (no None/fake)."""
    return ventana is not None and not isinstance(ventana, str)


def leer_arbol(ventana) -> list[dict]:
    """Devuelve los controles del árbol UIA (tipo + texto) para diagnóstico.

    Solo lectura. Sin ventana real -> lista vacía.
    """
    if not _es_wrapper_valido(ventana):
        return []
    try:
        controles = []
        for ctrl in ventana.descendants():
            try:
                tipo = ctrl.element_info.control_type or ""
                texto = (ctrl.window_text() or "").strip()
            except Exception:                        # noqa: BLE001
                continue
            controles.append({"tipo": tipo, "texto": texto[:120]})
        return controles
    except Exception:                                # noqa: BLE001
        return []


def verificar_campo_mensaje(ventana) -> dict:
    """¿Existe un campo de texto editable en la ventana? (pre-escritura)

    Tri-estado: True si hay un control editable; False si hay ventana pero no
    se halla campo (fail-closed: no se puede escribir); None si no hay ventana
    real (no verificable).
    """
    if not _es_wrapper_valido(ventana):
        return {"ok": None, "motivo": "ventana_no_real", "detalle": "sin ventana no hay campo que verificar"}
    tipos = {c["tipo"] for c in leer_arbol(ventana)}
    if not tipos:
        return {"ok": False, "motivo": "arbol_no_legible", "detalle": "UIA no devolvió controles"}
    if tipos & set(_CAMPOS_EDITABLES):
        return {"ok": True, "detalle": "campo editable presente en el árbol UIA"}
    return {"ok": False, "motivo": "campo_mensaje_no_hallado", "detalle": f"tipos visibles: {sorted(tipos)[:6]}"}


def verificar_chat_activo(ventana, destinatario: str) -> dict:
    """¿El chat abierto coincide con el destinatario? (pre-envío)

    Heurística por texto visible (header de la conversación). Tri-estado:
    False solo si se lee el árbol y el nombre NO aparece (chat equivocado);
    None si el árbol no deja leer el nombre (no se asume, se reporta).
    """
    if not _es_wrapper_valido(ventana):
        return {"ok": None, "motivo": "ventana_no_real", "detalle": "sin ventana no hay chat que verificar"}
    arbol = leer_arbol(ventana)
    if not arbol:
        return {"ok": None, "motivo": "arbol_no_legible", "detalle": "no se pudo leer la ventana (usar screenshot)"}
    nombre = str(destinatario).strip().lower()
    if not nombre:
        return {"ok": None, "motivo": "destinatario_vacio", "detalle": "sin nombre no se puede confirmar el chat"}
    encontrado = any(nombre in (c.get("texto") or "").lower() for c in arbol)
    if encontrado:
        return {"ok": True, "detalle": f"chat activo contiene '{destinatario}'"}
    return {"ok": False, "motivo": "chat_activo_no_coincide", "detalle": "el nombre del destinatario no aparece en el árbol de la ventana"}


def verificar_envio(ventana, fragmento: str) -> dict:
    """¿El fragmento apareció en la conversación tras enviar? (post-envío)

    Tri-estado: True si el fragmento se lee en el árbol; None si el árbol no
    deja leer los mensajes (no se afirma "verificado"); False solo si se lee
    el árbol y el fragmento no está (algo salió mal: revisar).
    """
    if not _es_wrapper_valido(ventana):
        return {"ok": None, "motivo": "ventana_no_real", "detalle": "sin ventana no hay envío que verificar"}
    arbol = leer_arbol(ventana)
    if not arbol:
        return {"ok": None, "motivo": "arbol_no_legible", "detalle": "no se pudo leer la ventana (usar screenshot)"}
    frag = str(fragmento).strip().lower()
    if not frag:
        return {"ok": False, "motivo": "fragmento_vacio", "detalle": "sin fragmento no se puede verificar el envío"}
    encontrado = any(frag in (c.get("texto") or "").lower() for c in arbol)
    if encontrado:
        return {"ok": True, "detalle": "el mensaje aparece en la conversación"}
    return {"ok": False, "motivo": "envio_no_verificado", "detalle": "el mensaje no aparece en el árbol tras el envío"}
