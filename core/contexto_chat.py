"""
core/contexto_chat.py · §M-043 · El amarre del contexto de la sesión
═══════════════════════════════════════════════════════════════════════════════
**EIR tenía tres LLM potentes y les daba amnesia inducida.**

Medición real antes de este módulo (no una impresión — el código decía esto):

    front  · historial.push({assistant, content: acc.slice(0, 600)})  → mutila al 17%
    front  · historial.slice(-6)                                       → 3 turnos
    server · (historial or [])[-6:]                                    → recorta otra vez
    ─────────────────────────────────────────────────────────────────────────
    total enviado al modelo ≈ 3.600 caracteres ≈ 900 tokens
    ventana real de llama-3.3-70b (Groq)        = 128.000 tokens
    → SE USABA EL 0,7% DE LA CAPACIDAD YA PAGADA

Por eso el chat "se sentía limitado" frente a Gemini/ChatGPT/Claude: no era el
modelo, era el contexto que le llegaba.

LA LEY DE ESTE MÓDULO
---------------------
**Un límite de contexto se expresa en PRESUPUESTO DE CARACTERES, no en número
de turnos.** Seis turnos cortos y seis turnos largos son cosas distintas; contar
mensajes es contar la unidad equivocada.

**Y el recorte vive en UN SOLO lugar.** Antes había tres recortes independientes
(front + dos ramas del orquestador). Tres relojes nunca coinciden.

Determinista, sin red, sin LLM: qué se recuerda no es criterio de un modelo.
"""
from __future__ import annotations

import os

# 1 token ≈ 4 caracteres en español (aproximación estándar y conservadora:
# el español real ronda 3,5-4, así que estimar 4 SUBESTIMA los tokens, que es
# el lado seguro cuando se compara contra un límite duro).
CHARS_POR_TOKEN = 4

# Presupuesto por defecto: 24.000 caracteres ≈ 6.000 tokens.
# Es 25x lo que se enviaba antes y sigue siendo <5% de la ventana de 128k.
# NO se sube "al máximo" a propósito: el coste por token es real y una
# conversación infinita degrada la atención del modelo sobre lo importante.
PRESUPUESTO_DEFECTO = 24_000

# Ningún mensaje individual puede acaparar el presupuesto entero: un pegado
# gigante del usuario dejaría fuera toda la conversación previa.
MAX_CHARS_POR_MENSAJE = 8_000

_ROLES_VALIDOS = ("user", "assistant", "system")


def presupuesto() -> int:
    """Presupuesto de contexto en caracteres.

    Se lee en TIEMPO DE LLAMADA (nunca en el import): así se ajusta desde
    Railway sin desplegar, y los tests pueden moverlo. Es la familia F6 del
    cazador de bugs aplicada a nuestro propio módulo.
    """
    try:
        v = int(os.getenv("EIR_CTX_CHARS", "").strip() or PRESUPUESTO_DEFECTO)
    except ValueError:
        return PRESUPUESTO_DEFECTO
    # Un presupuesto absurdo (0, negativo) dejaría al chat sin memoria: se
    # ignora y se usa el defecto. Fail-safe, no fail-open.
    return v if v > 0 else PRESUPUESTO_DEFECTO


def _limpiar(mensaje) -> dict | None:
    """Normaliza un mensaje del historial. Devuelve None si no es utilizable.

    Tolerante con la forma (el historial viaja desde el navegador y puede traer
    cualquier cosa) pero estricto con el contenido: un rol inventado o un
    contenido vacío no entra al contexto del modelo.
    """
    if not isinstance(mensaje, dict):
        return None
    rol = str(mensaje.get("role", "")).strip().lower()
    if rol not in _ROLES_VALIDOS:
        return None
    contenido = mensaje.get("content")
    if contenido is None:
        return None
    texto = str(contenido).strip()
    if not texto:
        return None
    return {"role": rol, "content": texto[:MAX_CHARS_POR_MENSAJE]}


def recortar(historial, presupuesto_chars: int | None = None) -> list[dict]:
    """Devuelve la ventana de conversación que CABE en el presupuesto.

    Se llena desde el mensaje MÁS RECIENTE hacia atrás: lo viejo se cae primero,
    que es exactamente lo que hace un humano al recordar una conversación. El
    orden final se devuelve cronológico (viejo → nuevo), como espera la API.

    Determinista: mismo historial + mismo presupuesto = misma ventana, siempre.
    """
    tope = presupuesto_chars if presupuesto_chars is not None else presupuesto()
    if tope <= 0:
        return []

    seleccionados: list[dict] = []
    usados = 0
    for bruto in reversed(list(historial or [])):
        msg = _limpiar(bruto)
        if msg is None:
            continue
        costo = len(msg["content"])
        if usados + costo > tope:
            break            # se acabó el presupuesto: lo más viejo queda fuera
        seleccionados.append(msg)
        usados += costo
    seleccionados.reverse()  # cronológico para la API
    return seleccionados


def construir_mensajes(sistema: str, historial, mensaje_actual: str,
                       presupuesto_chars: int | None = None) -> list[dict]:
    """Arma la lista completa de mensajes para el modelo: system + ventana + turno.

    El `system` y el `mensaje_actual` NO compiten por el presupuesto del
    historial: son obligatorios. El presupuesto gobierna solo cuánta memoria
    de la conversación entra — que es lo único que puede crecer sin fin.
    """
    mensajes: list[dict] = []
    if sistema:
        mensajes.append({"role": "system", "content": str(sistema)})
    mensajes.extend(recortar(historial, presupuesto_chars))
    if mensaje_actual:
        mensajes.append({"role": "user", "content": str(mensaje_actual)})
    return mensajes


def diagnostico(historial, presupuesto_chars: int | None = None) -> dict:
    """Qué entró, qué se cayó y cuánto del presupuesto se usó.

    Existe para que el recorte sea AUDITABLE: si mañana alguien vuelve a sentir
    que "el chat olvida", esto lo responde con números en vez de con opiniones —
    la lección de M-043, que nació justo de esa sensación sin medir.
    """
    tope = presupuesto_chars if presupuesto_chars is not None else presupuesto()
    entrantes = [m for m in (_limpiar(x) for x in (historial or [])) if m]
    ventana = recortar(historial, tope)
    usados = sum(len(m["content"]) for m in ventana)
    return {
        "presupuesto_chars": tope,
        "tokens_aprox": usados // CHARS_POR_TOKEN,
        "mensajes_recibidos": len(entrantes),
        "mensajes_en_contexto": len(ventana),
        "mensajes_descartados": len(entrantes) - len(ventana),
        "chars_usados": usados,
        "uso_pct": round(100 * usados / tope, 1) if tope else 0.0,
        "completo": len(ventana) == len(entrantes),
    }
