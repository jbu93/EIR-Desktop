"""layer1_schema · Capa 1 del harness: validación estricta de toda tool call.

La capa 1 responde "¿qué llega al handler?". Nada se ejecuta sin pasar por aquí:
  · esquema Pydantic v2 (tipos, campos requeridos, longitudes máximas),
  · sanitizadores (path traversal, allowlist de binarios, metacharacteres).
Fail-closed (L2): argumento inválido → ToolCallInvalida → la call completa se niega.
"""
