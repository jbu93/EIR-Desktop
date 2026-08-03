"""fish_audio_tools.py · Cliente nativo Fish Audio TTS para EIR Desktop.

100% Python, sin depender del MCP server Node.js. Se integra directamente
en el loop agéntico de EIR via plugin_registry.

Uso desde runners:
    from core_desktop.fish_audio_tools import hablar_fish_audio, listar_voces_fish_audio, establecer_voz_fish_audio

    resultado = hablar_fish_audio(texto="Hola paciente", reference_id="voz_id_opcional")
"""

from __future__ import annotations

import os
import json
import tempfile
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

import httpx

# Configuración
# L15: sin secretos en código — antes tenía una key de Fish Audio ajena
# hardcodeada como default (viajaba dentro del .exe publicado). Sin la key
# en el entorno, las llamadas devuelven 401 y ya se manejan como fallo
# honesto (ver el `resp.status_code != 200` en hablar_fish_audio()).
API_KEY = os.getenv("FISH_AUDIO_API_KEY", "").strip()
BASE_URL = "https://api.fish.audio"
DOWNLOADS_DIR = Path.home() / "Downloads"

# Resolver modelo TTS por tier (import perezoso para evitar dependencias circulares)
def _get_tts_model(tier: Optional[str] = None) -> str:
    """Obtiene el modelo Fish Audio TTS según el tier del usuario."""
    if tier is None:
        try:
            from core_desktop import sesion
            estado = sesion.estado()
            tier = estado.get("tier", "freemium")
        except Exception:
            tier = "freemium"
    
    # Normalizar tier a uppercase para el mapa
    tier_norm = str(tier).upper()
    tier_map = {
        "FREEMIUM": "s2.1-pro-free",
        "PAGO": "s2.1-pro",
        "ULTRA": "s2.1-pro",
    }
    return tier_map.get(tier_norm, "s2.1-pro-free")

# Estado de sesión (voz por defecto)
_voz_por_defecto: Optional[str] = None
_lock = threading.Lock()


def _headers_json(tier: Optional[str] = None) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "model": _get_tts_model(tier),
    }


def _headers_binary(tier: Optional[str] = None) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/msgpack",
        "model": _get_tts_model(tier),
    }


def _generar_nombre_archivo(prefix: str = "eir_audio") -> Path:
    """Genera nombre único con timestamp en Downloads."""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return DOWNLOADS_DIR / f"{prefix}_{timestamp}.mp3"


def _reproducir_audio(ruta_archivo: Path) -> bool:
    """Reproduce un archivo MP3 usando pygame en un hilo separado."""
    try:
        # Usamos python -c para evitar importar pygame en el hilo principal
        # si no está disponible, o si hay conflictos de inicialización
        cmd = [
            "python", "-c",
            f"""
import pygame
pygame.mixer.init()
pygame.mixer.music.load(r'{ruta_archivo}')
pygame.mixer.music.play()
while pygame.mixer.music.get_busy():
    pygame.time.Clock().tick(10)
"""
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        # Fallback: intentar con player script si existe
        player_script = Path(__file__).parent.parent / "scripts" / "fish_audio_player.py"
        if player_script.exists():
            try:
                result = subprocess.run(["python", str(player_script), str(ruta_archivo)],
                                       capture_output=True, timeout=60)
                return result.returncode == 0
            except Exception:
                pass
        return False


def hablar_fish_audio(
    texto: str,
    reference_id: Optional[str] = None,
    format: str = "mp3",
    reproducir: bool = True,
    guardar: bool = True,
    tier: Optional[str] = None
) -> Dict[str, Any]:
    """
    Genera audio TTS con Fish Audio y opcionalmente lo reproduce y guarda.

    Args:
        texto: Texto a convertir en voz
        reference_id: ID de voz personalizada (opcional, usa voz por defecto de la sesión si no se especifica)
        format: Formato de salida (mp3, wav, pcm, opus)
        reproducir: Si True, reproduce en parlantes inmediatamente
        guardar: Si True, guarda el MP3 en Downloads
        tier: Tier del usuario (FREEMIUM, PAGO, ULTRA). Si None, se obtiene de la sesión.

    Returns:
        Dict con ok, archivo (ruta), bytes, mensaje
    """
    global _voz_por_defecto

    # Resolver tier si no se proporciona
    if tier is None:
        try:
            from core_desktop import sesion
            estado = sesion.estado()
            tier = estado.get("tier", "freemium").upper()
        except Exception:
            tier = "FREEMIUM"

    # Resolver reference_id: parámetro > voz por defecto sesión > None
    ref_id = reference_id
    if not ref_id:
        with _lock:
            ref_id = _voz_por_defecto

    payload = {
        "text": texto,
        "format": format,
    }
    if ref_id:
        payload["reference_id"] = ref_id

    try:
        # Usamos httpx síncrono (el loop agéntico ya corre en hilo propio)
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{BASE_URL}/v1/tts",
                headers=_headers_json(tier),
                json=payload,
            )

        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"API error {resp.status_code}",
                "detalle": resp.text[:500],
            }

        audio_bytes = resp.content
        if not audio_bytes:
            return {"ok": False, "error": "Respuesta vacía de Fish Audio"}

        resultado: Dict[str, Any] = {
            "ok": True,
            "bytes": len(audio_bytes),
            "reference_id_usado": ref_id,
        }

        # Guardar en Downloads
        if guardar:
            archivo_path = _generar_nombre_archivo()
            try:
                archivo_path.write_bytes(audio_bytes)
                resultado["archivo"] = str(archivo_path)
                resultado["archivo_nombre"] = archivo_path.name
            except Exception as e:
                resultado["guardado_error"] = str(e)

        # Reproducir
        if reproducir and "archivo" in resultado:
            try:
                exito = _reproducir_audio(Path(resultado["archivo"]))
                resultado["reproducido"] = exito
                if not exito:
                    resultado["reproduccion_error"] = "Falló la reproducción (ver logs pygame)"
            except Exception as e:
                resultado["reproducido"] = False
                resultado["reproduccion_error"] = str(e)
        elif reproducir and not guardar:
            # Reproducir desde memoria sin guardar (archivo temporal)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)
            try:
                exito = _reproducir_audio(tmp_path)
                resultado["reproducido"] = exito
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        return resultado

    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout llamando a Fish Audio"}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Error de red: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Error inesperado: {e}"}


def listar_voces_fish_audio(
    page_size: int = 20,
    page_number: int = 1,
    solo_propias: bool = True
) -> Dict[str, Any]:
    """
    Lista las voces disponibles en la cuenta Fish Audio.
    """
    try:
        params = {
            "page_size": page_size,
            "page_number": page_number,
        }
        if solo_propias:
            params["self"] = "true"

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{BASE_URL}/model",
                headers={"Authorization": f"Bearer {API_KEY}"},
                params=params,
            )

        if resp.status_code != 200:
            return {"ok": False, "error": f"API error {resp.status_code}", "detalle": resp.text[:500]}

        data = resp.json()
        items = data.get("items", [])

        voces = []
        for v in items:
            voces.append({
                "id": v.get("_id"),
                "titulo": v.get("title"),
                "estado": v.get("state"),
                "idiomas": v.get("languages", []),
                "visibilidad": v.get("visibility"),
                "tags": v.get("tags", []),
            })

        return {
            "ok": True,
            "total": data.get("total", 0),
            "voces": voces,
            "pagina": page_number,
            "tamano_pagina": page_size,
        }

    except Exception as e:
        return {"ok": False, "error": f"Error: {e}"}


def establecer_voz_fish_audio(voice_id: str) -> Dict[str, Any]:
    """
    Establece la voz por defecto para la sesión actual (en memoria).
    """
    global _voz_por_defecto
    with _lock:
        _voz_por_defecto = voice_id
    return {"ok": True, "voz_por_defecto": voice_id, "mensaje": f"Voz por defecto establecida: {voice_id}"}


def obtener_voz_por_defecto() -> Dict[str, Any]:
    """Obtiene la voz por defecto actual de la sesión."""
    global _voz_por_defecto
    with _lock:
        return {
            "ok": True,
            "voz_por_defecto": _voz_por_defecto,
            "configurada": _voz_por_defecto is not None,
        }


# Funciones de conveniencia para usar desde runners
def tts_rapido(texto: str, voice_id: Optional[str] = None) -> str:
    """Función simple que devuelve la ruta del archivo o mensaje de error."""
    res = hablar_fish_audio(texto, reference_id=voice_id)
    if res.get("ok") and "archivo" in res:
        return res["archivo"]
    return f"ERROR: {res.get('error', 'desconocido')}"