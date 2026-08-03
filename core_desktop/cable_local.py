# -*- coding: utf-8 -*-
"""cable_local.py · M-056 · El cable del chat local: agente_loop ↔ plugin_registry.

Cierra el hueco entre el cerebro (``core.agente_loop``) y las manos locales
(``plugin_registry``): el chat del desktop nunca pasaba ``ejecutores`` ni
``tools_schema``, así que el loop caía al default del web
(``core.shell_tools.TOOLS_EXEC`` / ``TOOLS_SCHEMA``) y las 21 tools locales
registradas en el plugin_registry eran inalcanzables para el agente.

Este módulo construye el cable:

  · ``construir_ejecutores()``   → merge de los ejecutores del web (se conservan,
    sin regresión) + los handlers del plugin_registry (Capa 1 ya dentro de cada
    handler). El local gana en colisión (hoy no hay ninguna).
  · ``construir_tools_schema()`` → los esquemas de function-calling que recibe el
    LLM: los del web (intactos) + las tools locales registradas y declaradas en
    la matriz de autonomía. Determinista (ordenado) y fail-closed: nada fuera de
    ``data/autonomia_zonas.json`` se ofrece jamás.
  · ``resolver_texto_final()``   → decide qué texto ve el doctor al final del
    turno (D097). Sin esto, los 4 runners del desktop siempre mostraban
    ``Resultado.resumen_para_narrar()`` — un resumen técnico interno ("[tool] NO
    DISPONIBLE...") — salvo con el gateway cloud de pago (M-052), que es el
    único cliente que deja ``ultimo_texto_final`` seteado. Porta al desktop el
    mismo patrón que ya usa en producción ``routes_inference.py:229-249``: si el
    LLM no pidió ninguna herramienta, se le pide el texto conversacional real en
    una llamada aparte, en vez de fingir que "no se ejecutó nada" fue la
    respuesta.

Reglas del harness que este cable respeta sin duplicar (L17: aditivo, no toca
nada existente):
  · La autorización de ZONA/EFECTO/requiere_humano la decide la Capa 2
    (``core/autonomia.py`` + ``agent_hooks``) dentro del loop, por paso.
  · Toda tool local pasa por la Capa 1 (esquema + sanitizadores) en su handler.
  · ``EIR_SANDBOX_ROOT`` y ``EIR_HITL_SECRET`` se leen en tiempo de llamada (L10).
"""
from __future__ import annotations

from typing import Any

# Tope del fragmento narrable que devuelve el loop al modelo.
_MAX_RESUMEN = 200


def _importar_web():
    """Esquemas y ejecutores del web (core.shell_tools). Lazy, tolerante."""
    from core import shell_tools
    return shell_tools


def _registro():
    """Índice de ejecutores locales. Se puebla aquí (idempotente) para que el
    cable funcione solo, sin depender del orden del runner."""
    from . import plugin_registry
    plugin_registry.poblar_registro_completo()
    return plugin_registry


def _resumen_amigable(res: dict) -> str:
    """Convierte el dict de resultado de una tool local (que no lleva clave
    ``resumen``) en un fragmento narrable para el loop. Nunca rebasa
    ``_MAX_RESUMEN`` caracteres. La Capa 1 ya limitó tamaño y confinó rutas."""
    contenido = res.get("contenido")
    if isinstance(contenido, str) and contenido:
        return contenido if len(contenido) <= _MAX_RESUMEN else contenido[:_MAX_RESUMEN] + "…"
    salida = res.get("salida")
    if isinstance(salida, str) and salida.strip():
        s = salida.strip()
        return s if len(s) <= _MAX_RESUMEN else s[:_MAX_RESUMEN] + "…"
    if isinstance(res.get("archivos"), list):
        return f"{len(res['archivos'])} archivo(s)"
    if res.get("coincidencias") is not None:
        return f"{res['coincidencias']} coincidencia(s)"
    if res.get("archivo") is not None and res.get("bytes") is not None:
        return f"escrito {res['archivo']} ({res['bytes']} bytes)"
    return ""


def _envolver(nombre: str, handler) -> Any:
    """Envuelve un handler local para que su resultado sea narrable por el loop
    (añade ``resumen`` solo si el handler no lo trae). No cambia nada más."""

    def _h(**kwargs):
        res = handler(**kwargs)
        if isinstance(res, dict) and not res.get("resumen"):
            res = dict(res)
            res["resumen"] = _resumen_amigable(res)
        return res

    return _h


def construir_ejecutores() -> dict[str, Any]:
    """Merge de ejecutores: web (TOOLS_EXEC, se conservan) + manos locales del
    plugin_registry (Capa 1 dentro de cada handler). El local gana en colisión."""
    web = dict(_importar_web().TOOLS_EXEC)
    reg = _registro()
    for nombre in reg.listar():
        handler = reg.obtener(nombre)
        if handler is not None:
            web[nombre] = _envolver(nombre, handler)
    return web


def _schema_local(nombre: str) -> dict:
    """Parámetros del LLM para una tool local: el Pydantic de la Capa 1 si
    existe (esquema real, tipos y requeridos); si no, genérico — el handler
    valida o degrada honesto (deuda D1 de la misión)."""
    try:
        from core.harness.layer1_schema import validator
        modelo = validator._ESQUEMAS.get(nombre)  # type: ignore[attr-defined]
    except Exception:                     # noqa: BLE001 — sin Capa 1, esquema genérico
        modelo = None
    if modelo is not None:
        esquema = dict(modelo.model_json_schema())
        for k in ("title", "$defs", "definitions"):
            esquema.pop(k, None)
        return esquema
    return {"type": "object", "properties": {}}


def construir_tools_schema() -> list[dict]:
    """Esquemas de function-calling que recibe el LLM: los del web (intactos) +
    las tools locales registradas y declaradas en la matriz. Determinista
    (ordenado) y fail-closed (nada fuera de la matriz se ofrece)."""
    from core import autonomia as A

    web = list(_importar_web().TOOLS_SCHEMA)
    reg = _registro()
    locales = []
    for nombre in sorted(reg.listar()):
        decl = A.REGISTRO.get(nombre)
        if not decl:
            continue
        locales.append({
            "type": "function",
            "function": {
                "name": nombre,
                "description": decl.get("descripcion", ""),
                "parameters": _schema_local(nombre),
            },
        })
    return web + locales


def resolver_texto_final(lc, resultado, *, modelo: str, sistema: str,
                          historial: list, mensaje: str) -> str:
    """Decide el texto final visible al doctor (D097).

    Porta tal cual el patrón ya probado en producción
    (``routes_inference.py:229-249``, ruta real ``/api/v1/inference``): traza
    no vacía -> narrar lo ejecutado; traza vacía -> pedir al MISMO cliente, con
    el MISMO mensaje/historial, el texto conversacional real (sin tools). No
    reconstruye nada nuevo — reutiliza exactamente lo que el runner ya le pasó
    a ``agente_loop.ejecutar()``.

    Orden de decisión:
      1. ``ClienteEirCloud`` (M-052) ya decidió todo server-side: si
         ``lc.ultimo_texto_final`` viene seteado, se usa tal cual — cero
         cambio de comportamiento del gateway de pago.
      2. Cliente de ESTILO M-052 (expone el atributo aunque esté en
         ``None`` -> el gateway cloud falló o erroró): NO se re-consulta.
         Repetir la llamada sería un segundo golpe HTTP y, peor, un segundo
         cobro de crédito por el mismo turno (``routes_inference.py`` ya
         descuenta cuota por request). Se narra la traza que haya quedado.
      3. Cliente sin ese atributo (mock, OpenCode, futuros BYOK) y traza NO
         vacía: se narra lo ejecutado — sin cambios respecto a hoy.
      4. Cliente sin ese atributo y traza VACÍA: el LLM no pidió ninguna
         tool -> se le pide el texto conversacional real en una llamada
         adicional sin tools. Si falla, mensaje honesto (mismo wording que
         ``routes_inference.py``).
    """
    texto_cloud = getattr(lc, "ultimo_texto_final", None)
    if texto_cloud is not None:
        return texto_cloud
    if hasattr(lc, "ultimo_texto_final"):
        return resultado.resumen_para_narrar()
    if resultado.pasos:
        texto = resultado.resumen_para_narrar()
        if resultado.tope_alcanzado:
            texto += ("\n[AVISO] se alcanzó el tope de pasos; la respuesta "
                      "puede estar incompleta y así debe declararse.")
        return texto
    try:
        resp = lc.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": sistema},
                *historial,
                {"role": "user", "content": mensaje},
            ],
            tools=[],
        )
        return resp.choices[0].message.content or resultado.resumen_para_narrar()
    except Exception as e:                      # noqa: BLE001 — motor caído, respuesta honesta
        print(f"[cable_local] texto_final falló: {type(e).__name__}: {e}")
        return ("[EIR · motor offline] No pude obtener una respuesta ahora. "
                "Inténtalo en unos segundos.")
