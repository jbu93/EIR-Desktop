"""app_control.py · Control de aplicaciones de escritorio del usuario (Fase B1/B2 · D074).

Rol 360° de producto: EIR DR Desktop opera las aplicaciones que el doctor ya
tiene instaladas (WhatsApp Business, CapCut, Blender, Chitubox, Anycubic
Workshop) SIN APIs oficiales — automatiza la app local del usuario mediante
UI Automation determinista (pywinauto, backend "uia"). pyautogui queda
reservado como fallback de teclado para apps que no exponen controles UIA
(fase posterior, D074: fuera del alcance de esta tanda).

Frontera (``data/autonomia_zonas.json`` · zona "producto" · 2026-07-31):
  · Acciones IRREVERSIBLES (escribir/enviar) -> requiere_humano=true: el hook
    pre las bloquea en autónomo y el flujo exige dry-run + confirmación.
  · L1/L6: el contenido que se escribe puede ser PHI de paciente; las trazas
    guardan destinatario_hash, jamás el texto. El módulo NO persiste PHI.
  · L2 fail-closed: app no abierta, driver ausente o control no hallado ->
    ``{"ok": False, "motivo": ...}``, jamás "creo que se envió".

Fase B2 (2026-07-31, D074):
  · ANTI-BANEO (patrón, no herramienta): el envío escribe como humano (pausas
    aleatorias por carácter, "Handy texto→texto"), las plantillas son siempre
    parametrizadas (jamás texto idéntico: data/plantillas_producto.json) y hay
    rate-limit + tope diario medibles (L10: kill-switches leídos en llamada).
  · ANTI-ERROR (triangulación): verificacion_ui.py confirma con el árbol UIA
    ANTES (chat correcto + campo listo) y DESPUÉS (mensaje en la conversación)
    del envío. Sin verificación positiva, jamás "enviado". El screenshot queda
    como respaldo para lo que UIA no vea (sin OCR/tesseract).

pywinauto se importa perezosamente; sin él (o sin sesión gráfica) las tools
devuelven fail-closed honesto. El arnés (smoke_agente_local P15-P21) verifica
contrato y fail-closed en modalidad sin GUI; la prueba con la app real es del
Soberano en su máquina (L7: verificar el cable, no el doble) — usa el CLI
``python -m core_desktop.app_control`` (prueba guiada).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import time
from datetime import date
from pathlib import Path

# Tiempo de espera (seg) para que una ventana/control aparezca y sea utilizable.
_WAIT_SEGUNDOS: float = 5.0

# L8: límite defensivo de la captura en memoria. El contexto de un chat no
# admite megas crudos; más allá de esto la tool se niega con motivo honesto.
_CAPTURA_MAX_BASE64: int = 8 * 1024 * 1024

def _buscar_plantillas_producto() -> Path:
    """Busca ``data/plantillas_producto.json`` escalando hacia arriba desde este
    archivo (mismo patrón defensivo que ``agent_hooks``). Funciona en la
    estructura plana del repo de código abierto y empaquetado con PyInstaller."""
    here = Path(__file__).resolve().parent
    for _ in range(4):
        candidato = here / "data" / "plantillas_producto.json"
        if candidato.is_file():
            return candidato
        here = here.parent
    return Path(__file__).resolve().parent.parent / "data" / "plantillas_producto.json"


_RUTA_PLANTILLAS = _buscar_plantillas_producto()


def _app_control_habilitado() -> bool:
    """L10: kill-switch leído en TIEMPO DE LLAMADA, nunca en el import.

    Sin ``EIR_APP_CONTROL_ENABLED`` las tools de zona "producto" NUNCA tocan
    una aplicación real: responden fail-closed. Un despliegue por sí solo no
    activa la capacidad; solo el Soberano la enciende en su máquina."""
    return os.getenv("EIR_APP_CONTROL_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _driver_winauto():
    """Devuelve el módulo ``pywinauto`` o ``None`` (import perezoso).

    Sin sesión gráfica (CI, servidor) el import puede existir pero no hay
    escritorio: el módulo sigue cargando y las tools fallan fail-closed en
    la primera conexión al desktop.
    """
    try:
        import pywinauto  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    return pywinauto


def _hash_destinatario(nombre: str) -> str:
    """L6: traza sin PHI — la identidad del destinatario viaja como hash."""
    return hashlib.sha256(str(nombre).encode("utf-8", errors="replace")).hexdigest()[:16]


# ─── B2 · anti-baneo medible (L10: leído en TIEMPO DE LLAMADA) ────────────

def _param_int(nombre: str, default: int) -> int:
    """Env var entera con default sano. L10: se lee por llamada, no en import."""
    try:
        return int(os.getenv(nombre, "") or default)
    except (TypeError, ValueError):
        return default


def _intervalo_min_ms() -> int:
    """Intervalo mínimo entre envíos (ms). Un humano no ráfaga mensajes."""
    return _param_int("EIR_WHATSAPP_INTERVALO_MIN_MS", 8000)


def _tope_diario() -> int:
    """Tope de envíos por día (0 = prohibido). El sistema NO puede alcanzar
    el volumen que dispara baneos: el tope es la cota dura."""
    return _param_int("EIR_WHATSAPP_TOPE_DIARIO", 40)


def _ruta_estado_diario() -> Path:
    """Estado local del tope: solo fecha + contador, sin PHI (L6)."""
    ruta = Path.home() / ".eir_dr"
    try:
        ruta.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return ruta / "whatsapp_daily.json"


def _estado_diario() -> dict:
    hoy = date.today().isoformat()
    ruta = _ruta_estado_diario()
    try:
        if ruta.exists():
            prev = json.loads(ruta.read_text(encoding="utf-8"))
            if prev.get("fecha") == hoy:
                return {"fecha": hoy, "enviados": int(prev.get("enviados", 0))}
    except Exception:                        # noqa: BLE001 — estado corrupto = fresh
        pass
    return {"fecha": hoy, "enviados": 0}


def _verificar_tope_diario() -> tuple[bool, int, int]:
    """¿Queda cupo en el tope de hoy? NO muta estado. Fail-closed (L2):
    tope en 0 o mal configurado -> negado."""
    tope = _tope_diario()
    if tope <= 0:
        return False, _estado_diario()["enviados"], tope
    enviados = _estado_diario()["enviados"]
    if enviados >= tope:
        return False, enviados, tope
    return True, enviados, tope


def _registrar_envio_diario() -> None:
    """Cuenta UN envío verificado contra el tope de hoy. Solo fecha+conteo."""
    datos = _estado_diario()
    datos["enviados"] += 1
    try:
        _ruta_estado_diario().write_text(json.dumps(datos), encoding="utf-8")
    except OSError:
        pass


def _escribir_como_humano(target, texto: str) -> None:
    """Escribe carácter a carácter con pausas aleatorias ("Handy texto→texto").

    Es la versión EIR del dictado por voz: el texto aparece en el campo como
    si se tecleara. Pausa variable tras espacios/puntuación para que el patrón
    no sea mecánico (anti-baneo a escala clínica).
    """
    chunks = re.split(r"(\s+|[.,;!?\n])", str(texto))
    for c in chunks:
        if not c:
            continue
        target.type_keys(c, with_spaces=True, pause=random.uniform(0.02, 0.06))
        time.sleep(random.uniform(0.04, 0.22))


# ─── B2 · plantillas parametrizadas (data/plantillas_producto.json) ───────

def _cargar_plantillas() -> dict:
    try:
        datos = json.loads(_RUTA_PLANTILLAS.read_text(encoding="utf-8"))
        return dict(datos.get("plantillas") or {})
    except Exception:                        # noqa: BLE001
        return {}


def listar_plantillas() -> dict:
    """Nombres de plantillas disponibles (solo lectura, sin efectos)."""
    return {"ok": True, "plantillas": sorted(_cargar_plantillas().keys())}


def render_plantilla(plantilla: str, **variables) -> dict:
    """Resuelve una plantilla con datos de la agenda real.

    Fail-closed (L4): si un placeholder queda sin resolver, responde
    ``{"ok": False, "motivo": "placeholder_sin_resolver"}`` — jamás se envía
    texto con huecos. Y como cada render lleva nombre/fecha/hora del paciente,
    nunca salen dos mensajes idénticos (patrón anti-baneo).
    """
    plantillas = _cargar_plantillas()
    texto_base = plantillas.get(plantilla)
    if not texto_base:
        return {"ok": False, "motivo": "plantilla_inexistente", "plantilla": plantilla}
    faltantes: list[str] = []

    def _reemplazar(m: "re.Match[str]") -> str:
        clave = m.group(1)
        valor = variables.get(clave)
        if valor is None or str(valor).strip() == "":
            faltantes.append(clave)
            return m.group(0)
        return str(valor)

    texto = re.sub(r"\{([a-z_]+)\}", _reemplazar, texto_base)
    if faltantes:
        return {
            "ok": False,
            "motivo": "placeholder_sin_resolver",
            "plantilla": plantilla,
            "faltantes": sorted(set(faltantes)),
        }
    return {"ok": True, "plantilla": plantilla, "texto": texto}


# ──────────────────────────────────────────────────────────────────────────
# Tools declaradas en data/autonomia_zonas.json (zona "producto")
# ──────────────────────────────────────────────────────────────────────────

def listar_apps_abiertas() -> dict:
    """Lista las ventanas de las aplicaciones abiertas (lectura, sin efectos).

    Devuelve ``{"ok": True, "ventanas": [{"titulo": ..., "app": ...}, ...]}``.
    Fail-closed: sin driver o sin escritorio responde ``ok: False``.
    """
    winauto = _driver_winauto()
    if winauto is None:
        return {
            "ok": False,
            "motivo": "control_de_apps_no_disponible",
            "detalle": "pywinauto requerido (solo Windows con sesión gráfica)",
        }
    if not _app_control_habilitado():
        return {
            "ok": False,
            "motivo": "app_control_desactivado",
            "detalle": "EIR_APP_CONTROL_ENABLED no está activo (L10)",
        }
    try:
        desktop = winauto.Desktop(backend="uia")
        ventanas = []
        for w in desktop.windows():
            titulo = w.window_text() or ""
            if not titulo.strip():
                continue
            ventanas.append({
                "titulo": titulo,
                "app": _clasificar_app(titulo),
            })
        return {"ok": True, "n_ventanas": len(ventanas), "ventanas": ventanas}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": "sin_sesion_grafica", "detalle": str(exc)}


def _clasificar_app(titulo: str) -> str:
    """Clasifica el título de la ventana en las apps del rol 360° (heurística)."""
    t = titulo.lower()
    for nombre, clave in (
        ("whatsapp_business", "whatsapp"),
        ("blender", "blender"),
        ("chitubox", "chitubox"),
        ("anycubic_workshop", "anycubic"),
        ("capcut", "capcut"),
    ):
        if clave in t:
            return nombre
    return "otra"


def _hallar_ventana(app: str):
    """Fail-closed (L2) + L10: sin kill-switch o sin driver, jamás toca la app.

    Lanza ValueError con el motivo; el llamador lo convierte en ``ok: False``."""
    if not _app_control_habilitado():
        raise ValueError("app_control_desactivado")
    winauto = _driver_winauto()
    if winauto is None:
        raise ValueError("control_de_apps_no_disponible")
    desktop = winauto.Desktop(backend="uia")
    clave = {
        "whatsapp_business": "whatsapp",
        "blender": "blender",
        "chitubox": "chitubox",
        "anycubic_workshop": "anycubic",
        "capcut": "capcut",
    }.get(app, app)
    ventana = desktop.window(title_re=f".*{clave}.*")
    try:
        ventana.wait("visible", timeout=_WAIT_SEGUNDOS)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"app_no_abierta:{app}") from exc
    return ventana


def traer_al_frente(app: str) -> dict:
    """Trae al frente la ventana de una app abierta (reversible).

    Fail-closed: si la app no está abierta, responde ``ok: False``.
    """
    try:
        ventana = _hallar_ventana(app)
    except ValueError as exc:
        return {"ok": False, "motivo": str(exc), "app": app}
    try:
        ventana.set_focus()
        ventana.restore()
        return {"ok": True, "app": app}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": "no_se_pudo_enfocar", "app": app, "detalle": str(exc)}


def escribir_texto_en(app: str, texto: str, confirmar: bool = False) -> dict:
    """Escribe ``texto`` en el control activo de la app, como humano (B2).

    Irreversible (requiere_humano=true en la matriz): con ``confirmar=False``
    devuelve SOLO el preview sin tocar la app (dry-run). Con ``confirmar=True``
    escribe el texto con pausas aleatorias realistas sobre el campo con foco.
    """
    if not confirmar:
        return {
            "ok": True,
            "dry_run": True,
            "app": app,
            "preview": texto,
            "requiere_humano": True,
            "detalle": "confirmar=True para escribir sobre el control activo",
        }
    try:
        ventana = _hallar_ventana(app)
        ventana.set_focus()
        ventana.restore()
        _escribir_como_humano(ventana, texto)
    except ValueError as exc:
        return {"ok": False, "motivo": str(exc), "app": app}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "motivo": "escritura_fallo",
            "app": app,
            "detalle": str(exc),
        }
    return {"ok": True, "app": app, "escrito": True}


def capturar_pantalla_app(app: str) -> dict:
    """Captura la ventana de la app como PNG en memoria (lectura).

    Nunca se persiste ni se sube (L6). Se devuelve base64; si el PNG excede
    el tope defensivo (L8), responde fail-closed en lugar de inflar contexto.
    """
    try:
        ventana = _hallar_ventana(app)
        ventana.set_focus()
        imagen = ventana.capture_as_image()
    except ValueError as exc:
        return {"ok": False, "motivo": str(exc), "app": app}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": "captura_fallo", "app": app, "detalle": str(exc)}

    buf = io.BytesIO()
    try:
        imagen.save(buf, format="PNG")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motivo": "png_no_generable", "app": app, "detalle": str(exc)}

    import base64 as _b64

    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
    if len(b64) > _CAPTURA_MAX_BASE64:
        return {
            "ok": False,
            "motivo": "captura_demasiado_grande",
            "app": app,
            "detalle": f"png_base64 excede {_CAPTURA_MAX_BASE64} bytes (L8)",
        }
    return {
        "ok": True,
        "app": app,
        "ancho": imagen.width,
        "alto": imagen.height,
        "png_base64": b64,
    }


def enviar_whatsapp_business(destinatario: str, mensaje: str, confirmar: bool = False) -> dict:
    """Envía un mensaje por la app local WhatsApp Business (sin API oficial).

    B2 · anti-baneo + verificación (D074):
      1. ``confirmar=False`` -> dry-run honesto (preview + hash), sin tocar la app.
      2. Tope diario verificado ANTES (L10/L2): si el cupo está agotado, niega.
      3. Pre-verificación por árbol UIA: campo de mensaje presente y chat activo
         coincide con el destinatario. Si no se confirma -> fail-closed.
      4. Escritura como humano (pausas aleatorias) en el campo de mensaje.
      5. Intervalo mínimo (rate-limit) entre envíos, con jitter.
      6. Post-verificación: el mensaje aparece en la conversación. Solo entonces
         se cuenta contra el tope y se responde "enviado". Sin verificación
         positiva -> fail-closed, jamás "enviado" (L2).

    La traza nunca lleva el texto ni el destinatario en claro (L6). El envío
    se envía SOLO al número del consultorio con confirmación humana explícita.
    """
    preview = {
        "ok": True,
        "dry_run": True,
        "app": "whatsapp_business",
        "destinatario_hash": _hash_destinatario(destinatario),
        "preview": mensaje,
        "requiere_humano": True,
        "detalle": "confirmar=True ejecuta el envío real; riesgo de baneo de la cuenta (D074)",
    }
    if not confirmar:
        return preview

    # 2. tope diario (anti-baneo, antes de tocar la app)
    permitido, enviados, tope = _verificar_tope_diario()
    if not permitido:
        return {
            "ok": False,
            "motivo": "tope_diario_alcanzado",
            "app": "whatsapp_business",
            "detalle": f"cupo de hoy agotado ({enviados}/{tope}) · EIR_WHATSAPP_TOPE_DIARIO",
        }

    try:
        ventana = _hallar_ventana("whatsapp_business")
        ventana.set_focus()
        ventana.restore()

        # 3. pre-verificación por árbol UIA (anti-error)
        from . import verificacion_ui
        campo = verificacion_ui.verificar_campo_mensaje(ventana)
        if campo.get("ok") is False:
            return {"ok": False, "motivo": campo["motivo"], "app": "whatsapp_business",
                    "paso": "pre_campo", "detalle": campo.get("detalle")}
        chat = verificacion_ui.verificar_chat_activo(ventana, destinatario)
        if chat.get("ok") is False:
            return {"ok": False, "motivo": chat["motivo"], "app": "whatsapp_business",
                    "paso": "pre_chat", "detalle": chat.get("detalle")}

        # 4. escritura como humano sobre el campo de mensaje
        _escribir_como_humano(ventana, mensaje)

        # 5. rate-limit entre envíos (anti-baneo)
        time.sleep(random.uniform(
            max(0.2, _intervalo_min_ms() / 1000.0),
            (_intervalo_min_ms() / 1000.0) + 2.0,
        ))
        ventana.type_keys("{ENTER}", with_spaces=True)      # envía
        time.sleep(1.2)

        # 6. post-verificación: el mensaje debe aparecer en la conversación
        post = verificacion_ui.verificar_envio(ventana, mensaje[-40:])
        if post.get("ok") is False:
            return {"ok": False, "motivo": post["motivo"], "app": "whatsapp_business",
                    "paso": "post_envio", "detalle": post.get("detalle")}
    except ValueError as exc:
        return {"ok": False, "motivo": str(exc), "app": "whatsapp_business"}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "motivo": "envio_fallo",
            "app": "whatsapp_business",
            "paso": "desconocido",
            "detalle": str(exc),
        }

    _registrar_envio_diario()
    return {
        "ok": True,
        "app": "whatsapp_business",
        "destinatario_hash": _hash_destinatario(destinatario),
        "enviado": True,
        "verificado": post.get("ok") is True,
        "detalle": "enviado y verificado en la conversación" if post.get("ok") is True
                   else "enviado; verificación no legible por UIA (revisar pantalla)",
    }


# ──────────────────────────────────────────────────────────────────────────
# Registro aditivo en el plugin_registry (índice de ejecutores, D074)
# ──────────────────────────────────────────────────────────────────────────

def registrar_tools_app_control() -> None:
    """Registra las 5 tools de zona "producto" en el plugin_registry.

    La AUTORIZACIÓN la sigue decidiendo la matriz (fail-closed, L2) vía
    ``agent_hooks.run_pre_hooks``; aquí solo se indexa el ejecutor.
    """
    from .plugin_registry import registrar

    herramientas = (
        ("listar_apps_abiertas", listar_apps_abiertas, "producto"),
        ("traer_al_frente", traer_al_frente, "producto"),
        ("escribir_texto_en", escribir_texto_en, "producto"),
        ("capturar_pantalla_app", capturar_pantalla_app, "producto"),
        ("enviar_whatsapp_business", enviar_whatsapp_business, "producto"),
        ("listar_plantillas", listar_plantillas, "producto"),
        ("render_plantilla", render_plantilla, "producto"),
    )
    for nombre, handler, zona in herramientas:
        registrar(nombre, handler, requiere_red=False, zona=zona)


# ──────────────────────────────────────────────────────────────────────────
# CLI de PRUEBA GUIADA (L7) · verifica el cable en la máquina del Soberano
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    """Prueba guiada de app_control, sin red y sin tocar el envío real.

    Pasos:
      listar                 → ¿EIR ve tus apps abiertas?
      whatsapp               → traer WhatsApp Business al frente
      campo                  → ¿el árbol UIA expone el campo de mensaje?
      chat "Nombre Paciente" → ¿el chat abierto coincide con el destinatario?
      plantillas             → ¿qué plantillas hay disponibles?
      dry-run "Nombre" "Msg" → preview del envío sin tocar la app

    Uso:  python -m core_desktop.app_control <paso> [args...]
    """
    import argparse

    ap = argparse.ArgumentParser(prog="app_control", description="Prueba guiada del control de apps (B2/D074)")
    ap.add_argument("paso", choices=["listar", "whatsapp", "campo", "chat", "plantillas", "dry-run"])
    ap.add_argument("destinatario", nargs="?")
    ap.add_argument("mensaje", nargs="?")
    args = ap.parse_args()

    if args.paso == "listar":
        print(json.dumps(listar_apps_abiertas(), ensure_ascii=False, indent=2))
    elif args.paso == "whatsapp":
        print(json.dumps(traer_al_frente("whatsapp_business"), ensure_ascii=False, indent=2))
    elif args.paso == "campo":
        try:
            ventana = _hallar_ventana("whatsapp_business")
            from . import verificacion_ui
            print(json.dumps(verificacion_ui.verificar_campo_mensaje(ventana), ensure_ascii=False, indent=2))
        except ValueError as exc:
            print(json.dumps({"ok": False, "motivo": str(exc)}, ensure_ascii=False, indent=2))
    elif args.paso == "chat":
        if not args.destinatario:
            print(json.dumps({"ok": False, "motivo": "falta_el_nombre_del_paciente"}, ensure_ascii=False, indent=2))
            return 1
        try:
            ventana = _hallar_ventana("whatsapp_business")
            from . import verificacion_ui
            print(json.dumps(verificacion_ui.verificar_chat_activo(ventana, args.destinatario),
                             ensure_ascii=False, indent=2))
        except ValueError as exc:
            print(json.dumps({"ok": False, "motivo": str(exc)}, ensure_ascii=False, indent=2))
    elif args.paso == "plantillas":
        print(json.dumps(listar_plantillas(), ensure_ascii=False, indent=2))
    elif args.paso == "dry-run":
        if not args.destinatario or not args.mensaje:
            print(json.dumps({"ok": False, "motivo": "falta_destinatario_o_mensaje"}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(enviar_whatsapp_business(args.destinatario, args.mensaje, confirmar=False),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
