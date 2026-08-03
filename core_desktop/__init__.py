"""Paquete core_desktop del sandbox EIR Desktop v1.

Envoltorio delgado sobre ``core/agente_loop.py`` (intacto, sin modificar)
para ejecutar el loop agentico desde un proceso aislado (pywebview + Flask
en 127.0.0.1:5175).

Modulos:
  - cliente_llm   · mock 100% offline que documenta el contrato JSON
                    esperado de ``eirdr.com/api/v1/inference`` (estilo Anthropic
                    Desktop: el cerebro vive en la nube, el cliente no ve claves).
  - cliente_cloud · adapter REAL al gateway eirdr.com/api/v1/inference (M-052)
  - sesion        · token de sesión EIR DR. local (~/.eir_dr/sesion.json)
  - actualizador  · señal de versión / auto-update (M-052)
  - runner_*      · 4 envoltorios por rol clinico (odontologo, recepcion,
                    laboratorio, marketing). Cada uno inyecta su system_prompt
                    y su set de ejecutores (tools locales) en
                    ``core.agente_loop.ejecutar()``. NO duplican el loop.
  - local_tools   · esqueletos de tools locales (STL, listado de archivos,
                    texto) para Fase 2 del plan.

Principio no negociable (AGENTS.md §1, doc 02-arquitectura):
  estos modulos IMPORTAN ``core.agente_loop`` y ``core.autonomia`` pero
   NUNCA los editan. El proyecto principal sigue intacto y en produccion.
"""

__version__ = "1.1.5"
