"""lsp · Cliente LSP mínimo del harness (M-055 Fase 2B).

Le da al agente lo que un grep no puede dar: dónde se DEFINE un símbolo, dónde
se USA de verdad, y qué está roto según un analizador real. Todo de solo
lectura, y todo confinado a la raíz del sandbox por la capa 1.

Fail-closed y honesto (L2/L4): si el servidor no arranca, muere o no responde,
la tool devuelve ``{ok: False, motivo: ...}``. Jamás una ubicación inventada.
"""
