"""model_catalog.py · Catálogo unificado de modelos por tier y modo.

Fuente de verdad para routing de modelos en EIR Desktop.
Centraliza: OpenCode Zen, OpenRouter, NVIDIA, Groq, Cloudflare.

Nota (build público): este archivo NO incluye la tabla de costos por
proveedor (PROVIDER_COSTS) ni la lógica de cálculo de costo en USD —
esa pieza vive en el backend privado, donde realmente se usa para
facturar. Aquí solo queda el catálogo de qué modelo se usa para cada
tier/modo, que es la parte útil para quien construye con su propia
key (BYOK).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class Tier(str, Enum):
    FREEMIUM = "freemium"
    PAGO = "pago"
    ULTRA = "ultra"


class Modo(str, Enum):
    PLAN = "plan"      # Razonamiento clínico, lectura/escritura
    BUILD = "build"    # Código, tools, inferencia técnica


class Preferencia(str, Enum):
    AUTO = "auto"          # El sistema decide
    SPEED = "speed"        # Priorizar latencia/velocidad
    QUALITY = "quality"    # Priorizar calidad/razonamiento


# ─── CATÁLOGO COMPLETO POR TIER Y MODO ───
# Estructura: MODEL_CATALOG[Tier][Modo] = [modelos en orden de preferencia]
MODEL_CATALOG = {
    Tier.FREEMIUM: {
        Modo.PLAN: [
            # OpenCode Zen Free (requiere login OpenCode)
            "opencode/nemotron-3-ultra-free",      # 128K ctx, gratis
            "opencode/deepseek-v4-flash-free",     # 200K ctx, gratis
            "opencode/laguna-s-2.1-free",          # 100K ctx, gratis
            "opencode/ling-3.0-flash-free",        # 128K ctx, gratis
            "opencode/mimo-v2.5-free",             # 256K ctx, gratis
            "opencode/big-pickle",                 # 200K ctx, gratis
            "opencode/north-mini-code-free",       # 32K ctx, gratis
            # OpenRouter Free
            "openrouter/deepseek/deepseek-r1:free",
            "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/google/gemini-2.0-flash-exp:free",
            # NVIDIA Free (casi todo el catálogo)
            "nvidia/meta/llama-3.3-70b-instruct",
            "nvidia/qwen/qwen3.5-397b",
            "nvidia/nvidia/nemotron-3-ultra-550b",
            "nvidia/mistralai/mistral-large-3",
            # Groq Free
            "groq/llama-3.3-70b-versatile",
            "groq/groq/compound",
            "groq/groq/compound-mini",
            # Cloudflare Free
            "cloudflare/@cf/zai-org/glm-4.7-flash",
        ],
        Modo.BUILD: [
            # Mismo pool que PLAN para Free - modelos gratuitos potentes
            "opencode/nemotron-3-ultra-free",
            "opencode/deepseek-v4-flash-free",
            "opencode/mimo-v2.5-free",
            "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
            "nvidia/qwen/qwen3-coder-480b",
            "groq/qwen/qwen3.6-27b",
            "cloudflare/@cf/zai-org/glm-4.7-flash",
        ],
    },
    Tier.PAGO: {
        Modo.PLAN: [
            # Frontier cerrada - Anthropic para clínico
            "anthropic/claude-sonnet-4-5",
            "anthropic/claude-opus-4",
            # Fallback a modelos gratis potentes
            "opencode/nemotron-3-ultra-free",
            "nvidia/nvidia/nemotron-3-ultra-550b",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        ],
        Modo.BUILD: [
            # Pool gratis potenciado + baratos OpenCode
            "opencode/deepseek-v4-pro",
            "opencode/deepseek-v4-flash",
            "opencode/gpt-5.4-mini",
            "opencode/gpt-5.6-sol",
            "nvidia/nvidia/nemotron-3-ultra-550b",
            "nvidia/qwen/qwen3-coder-480b",
            "nvidia/mistralai/mistral-large-3",
            "groq/llama-3.3-70b-versatile",
            "groq/groq/compound",
            "cloudflare/@cf/zai-org/glm-4.7-flash",
        ],
    },
    Tier.ULTRA: {
        Modo.PLAN: [
            # True frontier - gratis en NVIDIA
            "nvidia/nemotron-3-ultra-550b",
            "nvidia/nvidia/nemotron-3-ultra-550b",
            # Frontier Anthropic (pagado, para casos críticos)
            "anthropic/claude-opus-4-1",
            "anthropic/claude-fable-5",
            # Frontier OpenRouter (si disponible)
            "openrouter/moonshot/kimi-k3",
            "openrouter/qwen/qwen3-235b",
        ],
        Modo.BUILD: [
            # Frontier open source / barato nivel Opus
            "nvidia/z-ai/glm-5.2",
            "openrouter/moonshot/kimi-k3",
            "openrouter/qwen/qwen3-235b",
            "nvidia/nvidia/nemotron-3-ultra-550b",
            "anthropic/claude-sonnet-4-5",          # fallback
        ],
    },
}

# ─── TTS FISH AUDIO ───
TTS_MODELS = {
    Tier.FREEMIUM: "s2.1-pro-free",
    Tier.PAGO: "s2.1-pro",
    Tier.ULTRA: "s2.1-pro",
}


def resolver_modelo(
    tier: Tier,
    modo: Modo,
    preferencia: Preferencia = Preferencia.AUTO
) -> str:
    """
    Resuelve el mejor modelo disponible según tier, modo y preferencia.

    Args:
        tier: FREEMIUM, PAGO, ULTRA
        modo: PLAN (razonamiento) o BUILD (código/tools)
        preferencia: AUTO, SPEED, QUALITY

    Returns:
        ID del modelo a usar

    Fallback: Si el pool está vacío, cae al tier inferior.
    """
    # Normaliza tier defensivamente: Tier guarda sus valores en minuscula
    # ("pago"), asi que un caller que pase "PAGO" (mayuscula) no encontraria
    # nada en MODEL_CATALOG y caeria en silencio al fallback FREEMIUM.
    if not isinstance(tier, Tier):
        try:
            tier = Tier(str(tier).lower())
        except ValueError:
            tier = Tier.FREEMIUM

    pool = MODEL_CATALOG.get(tier, {}).get(modo, [])

    if not pool:
        # Fallback a tier inferior
        fallback_order = {
            Tier.ULTRA: Tier.PAGO,
            Tier.PAGO: Tier.FREEMIUM,
            Tier.FREEMIUM: Tier.FREEMIUM,
        }
        fallback = fallback_order.get(tier, Tier.FREEMIUM)
        return resolver_modelo(fallback, modo, preferencia)

    # Lógica de selección según preferencia
    if preferencia == Preferencia.SPEED:
        # Priorizar modelos conocidos por velocidad (Groq, Cloudflare)
        speed_models = [m for m in pool if any(p in m for p in ("groq/", "cloudflare/"))]
        if speed_models:
            return speed_models[0]
    elif preferencia == Preferencia.QUALITY:
        # Priorizar modelos frontier (Anthropic, Nemotron Ultra)
        quality_models = [m for m in pool if any(p in m for p in ("anthropic/", "nemotron-3-ultra", "opus"))]
        if quality_models:
            return quality_models[0]

    # Por defecto: primer modelo del pool (ordenado por prioridad)
    return pool[0]


def resolver_tts(tier: Tier) -> str:
    """Resuelve el modelo Fish Audio TTS por tier."""
    return TTS_MODELS.get(tier, TTS_MODELS[Tier.FREEMIUM])


def listar_modelos_disponibles(tier: Tier, modo: Modo) -> list[str]:
    """Lista todos los modelos disponibles para un tier/modo (para UI/debug)."""
    return MODEL_CATALOG.get(tier, {}).get(modo, [])


def obtener_info_modelo(modelo: str) -> dict:
    """Retorna info del modelo: proveedor y tier mínimo requerido."""
    # Detectar proveedor por prefijo
    if modelo.startswith("opencode/"):
        proveedor = "OpenCode Zen"
    elif modelo.startswith("openrouter/"):
        proveedor = "OpenRouter"
    elif modelo.startswith("nvidia/"):
        proveedor = "NVIDIA NIM"
    elif modelo.startswith("groq/"):
        proveedor = "Groq"
    elif modelo.startswith("cloudflare/"):
        proveedor = "Cloudflare"
    elif modelo.startswith("anthropic/"):
        proveedor = "Anthropic"
    elif modelo.startswith("openai/"):
        proveedor = "OpenAI"
    else:
        proveedor = "Desconocido"

    # Tier mínimo estimado
    tier_min = Tier.FREEMIUM
    for t in [Tier.ULTRA, Tier.PAGO, Tier.FREEMIUM]:
        found = False
        for m in [Modo.PLAN, Modo.BUILD]:
            if modelo in MODEL_CATALOG.get(t, {}).get(m, []):
                tier_min = t
                found = True
                break
        if found:
            break

    return {
        "modelo": modelo,
        "proveedor": proveedor,
        "tier_minimo": tier_min.value,
    }
