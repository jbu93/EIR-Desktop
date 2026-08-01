"""core/harness · Harness Security by Design de EIR DR. (M-054).

Estructura de capas (se construyen por fases):
  L1 · Schema Validation  — esta fase
  L2 · PDP/PEP           — ya existe (core/autonomia.py + agent_hooks)
  L3 · Ephemeral Sandbox — Fase 3
  L4 · Egress Proxy/DLP  — Fase 3
  L5 · Risk Engine/HITL  — Fase 2
  L6 · Audit Telemetry   — ya existe (core/audit_logger.py)
"""
