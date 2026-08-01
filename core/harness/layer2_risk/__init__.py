"""layer2_risk · Capa 2 del harness: clasificación de riesgo y aprobación humana.

La capa 1 responde "¿qué llega al handler?" (benigno o no). La capa 2 responde
"¿cuánto duele si esto sale mal, y quién lo autoriza?":

  · ``risk_engine``: CLASIFICA una tool call ya normalizada en bajo/medio/alto.
    No autoriza — el PDP de zona/efecto sigue siendo ``core/autonomia.py`` (L14).
  · ``hitl``: emite y verifica aprobaciones humanas. Token HMAC-SHA256 de un
    solo uso, con TTL corto, atado a la huella de los argumentos aprobados.

Fail-closed (L2): ante cualquier duda —tool desconocida, evaluación que lanza,
firma que no cuadra— se clasifica ALTO y no se ejecuta nada.
"""
