# -*- coding: utf-8 -*-
"""
core/harness/layer1_schema/validator.py · Validación de esquema de la Capa 1 (M-054)
═══════════════════════════════════════════════════════════════════════════════
``validar_tool_call(nombre, argumentos, sandbox_root)`` es la ÚNICA puerta de
entrada de la Capa 1:

  1. Esquema Pydantic v2 por herramienta (tipos, requeridos, longitudes máximas).
  2. Sanitizadores por herramienta (ruta confinada, comando verificado).

Si algo no pasa → ToolCallInvalida(motivo) con código estable. Si pasa →
devuelve un dict NORMALIZADO (rutas ya absolutas y confinadas; comando ya
tokenizado como argv) que es exactamente lo que recibe el handler.

La autorización de ZONA/EFECTO/requiere_humano NO vive aquí: esa es la L2
(core/autonomia.py + agent_hooks). Esta capa solo decide si lo que llega es
BENÉIGNO — no si está AUTORIZADO.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from core.harness.layer1_schema.sanitizers import (
    ToolCallInvalida,
    confinar_ruta,
    verificar_comando,
)


# ─── esquemas por herramienta ───────────────────────────────────────────
class LeerArchivoArgs(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)


class ListarArchivosArgs(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)


class BuscarTextoArgs(BaseModel):
    patron: str = Field(min_length=1, max_length=200)
    ruta: str = Field(default=".", max_length=512)


class EscribirArchivoArgs(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)
    contenido: str = Field(max_length=1_000_000)


class EjecutarComandoArgs(BaseModel):
    comando: str = Field(min_length=1, max_length=1000)


# ─── LSP tools (M-055 Fase 2B) · solo lectura, misma raíz de sandbox ────
class PosicionEnArchivoArgs(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)
    linea: int = Field(ge=1, le=1_000_000)
    columna: int = Field(ge=1, le=10_000)


class DiagnosticosArgs(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)


_ESQUEMAS: dict[str, type[BaseModel]] = {
    "leer_archivo": LeerArchivoArgs,
    "listar_archivos": ListarArchivosArgs,
    "buscar_texto": BuscarTextoArgs,
    "escribir_archivo": EscribirArchivoArgs,
    "ejecutar_comando": EjecutarComandoArgs,
    "lsp_definicion": PosicionEnArchivoArgs,
    "lsp_referencias": PosicionEnArchivoArgs,
    "lsp_diagnosticos": DiagnosticosArgs,
}

# Herramientas cuyo argumento de ruta se confina a la raíz del sandbox.
_CONFINAN_RUTA = frozenset({
    "leer_archivo", "listar_archivos", "buscar_texto", "escribir_archivo",
    "lsp_definicion", "lsp_referencias", "lsp_diagnosticos",
})


def validar_tool_call(nombre: str, argumentos: dict[str, Any], sandbox_root: Path) -> dict[str, Any]:
    """Valida y normaliza una tool call. Lanza ToolCallInvalida ante cualquier fallo.

    Returns
    -------
    dict
        Argumentos normalizados: rutas absolutas confinadas y, para
        ejecutar_comando, el campo ``argv`` tokenizado (reemplaza a ``comando``).
    """
    modelo_cls = _ESQUEMAS.get(nombre)
    if modelo_cls is None:
        raise ToolCallInvalida("tool_no_soportada")

    try:
        modelo = modelo_cls(**dict(argumentos or {}))
    except ValidationError:
        raise ToolCallInvalida("argumentos_invalidos") from None

    normalizado = dict(modelo.model_dump())

    if nombre in _CONFINAN_RUTA:
        raiz = Path(sandbox_root)
        normalizado["ruta"] = str(confinar_ruta(normalizado["ruta"], raiz))

    if nombre == "ejecutar_comando":
        normalizado["argv"] = verificar_comando(normalizado.pop("comando"))

    return normalizado
