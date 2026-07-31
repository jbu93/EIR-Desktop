"""
core/autonomia.py · §M-027 · Frontera de autonomía de EIR DR.
═══════════════════════════════════════════════════════════════════════════════
**El candado que va antes que el animal.**

La pregunta que ordena este módulo no es "¿cómo hacemos a EIR más autónomo?" sino
**"¿dónde SÍ conviene subir la autonomía y dónde no?"** — y esa frontera se CODIFICA,
no se documenta en un README que nadie lee.

  ZONA clínica        ⛔  autonomía total aquí no es avance: es ilegal. La firma es humana.
  ZONA investigación  ✅  sin PHI, sin efectos irreversibles, auditable contra fuente.
  ZONA administrativa 🟡  reversible, pero con efecto económico/legal → exige confirmación.

Propiedades (deliberadas):
  · DETERMINISTA — sin LLM, sin red, sin estado. La misma pregunta da la misma respuesta.
  · FAIL-CLOSED  — lo que no está declarado, se niega. Añadir una herramienta al sistema
                   OBLIGA a declararla en data/autonomia_zonas.json.
  · DATA         — las zonas viven en JSON versionado (patrón norma-as-data, como
                   reglas_rvc.json y casos_oro.json). Cambiar política ≠ cambiar código.
  · AUDITADO     — cada denegación deja telemetría en core/guardas.py, sin PHI.

Consumidores previstos: M-028 (loop agéntico) y M-032 (equipo multi-agente de
investigación). Ninguno de los dos puede invocar una herramienta sin pasar por aquí.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _recurso(relativo: str) -> Path:
    """Resuelve un archivo empaquetado: en bundle PyInstaller usa sys._MEIPASS,
    en el repo la raiz del proyecto. Sin esto, el desktop empaquetado ve
    fail-closed (todo denegado) porque el JSON no aparece donde __file__ dice."""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / relativo
    return Path(__file__).resolve().parent.parent / relativo

# ─── vocabulario cerrado (si no está aquí, es inválido) ───
ZONAS = frozenset({"clinica", "investigacion", "administrativa"})
EFECTOS = frozenset({"lectura", "escritura_reversible", "escritura_irreversible"})
MODOS = frozenset({"autonomo", "asistido", "firmado"})

GUARDA_ID = "autonomia.denegada"        # debe existir en guardas.CATALOGO (si no, no emite)

# ─── LA MATRIZ · (zona, efecto) -> ¿permitido SIN humano presente? ───
# Esta tabla ES la tesis del plan, en ejecutable. Leerla de arriba a abajo cuenta la
# doctrina completa de EIR DR. sobre autonomía.
_MATRIZ_AUTONOMO: dict[tuple[str, str], bool] = {
    # La historia clínica jamás se escribe sola. Ni una coma.
    ("clinica",        "lectura"):                True,
    ("clinica",        "escritura_reversible"):   False,
    ("clinica",        "escritura_irreversible"): False,
    # Investigación: la zona donde EIR sí puede trabajar solo (aquí vive la Etapa 3).
    ("investigacion",  "lectura"):                True,
    ("investigacion",  "escritura_reversible"):   True,
    ("investigacion",  "escritura_irreversible"): False,
    # Administrativo: reversible, pero mueve dinero y obligaciones legales → confirma.
    ("administrativa", "lectura"):                True,
    ("administrativa", "escritura_reversible"):   False,
    ("administrativa", "escritura_irreversible"): False,
}

_RUTA_DATA = _recurso("data/autonomia_zonas.json")


def _cargar_registro() -> dict[str, dict]:
    """Carga las declaraciones desde el JSON versionado. Si el archivo falta o está
    corrupto devuelve {} — y entonces TODO queda negado (fail-closed hasta el final:
    preferimos un EIR que no actúe solo a un EIR que actúe solo sin frontera)."""
    try:
        datos = json.loads(_RUTA_DATA.read_text(encoding="utf-8-sig"))
        return dict(datos.get("herramientas") or {})
    except Exception:                    # noqa: BLE001 — sin frontera legible, no hay permiso
        return {}


REGISTRO: dict[str, dict] = _cargar_registro()


def _telemetria(tool: str, motivo: str) -> None:
    """Deja rastro de la denegación. Nunca lanza: si la telemetría falla, la frontera
    ya hizo su trabajo (negar) — que es lo que importa."""
    try:
        from core import guardas
        guardas.registrar(GUARDA_ID, herramienta=str(tool)[:40], motivo=motivo)
    except Exception:                    # noqa: BLE001
        pass


def puede_ejecutar(tool: str, modo: str = "autonomo") -> tuple[bool, str]:
    """¿Puede ejecutarse `tool` en este `modo`?

    Devuelve (permitido, motivo). El motivo es un código estable —apto para tests,
    telemetría y mensajes— nunca una frase generada.

    modo:
      · "autonomo" — el agente actúa solo (loop M-028, equipo M-032). Rige la matriz.
      · "asistido" — hay un humano presente confirmando la acción.
      · "firmado"  — el humano ya autenticó explícitamente (contraseña / firma).
    """
    if modo not in MODOS:
        _telemetria(tool, "modo_invalido")
        return False, "modo_invalido"

    decl = REGISTRO.get(tool)
    if not decl:
        _telemetria(tool, "no_declarada")
        return False, "no_declarada"      # ← fail-closed: el corazón de este módulo

    zona, efecto = decl.get("zona"), decl.get("efecto")
    if zona not in ZONAS or efecto not in EFECTOS:
        _telemetria(tool, "declaracion_invalida")
        return False, "declaracion_invalida"

    # ─── modo firmado: el humano ya puso su firma; solo restan zonas válidas ───
    if modo == "firmado":
        return True, "permitido_firmado"

    # ─── modo asistido: hay humano. Lo irreversible en clínica aún exige firma ───
    if modo == "asistido":
        if zona == "clinica" and efecto == "escritura_irreversible":
            _telemetria(tool, "requiere_firma_explicita")
            return False, "requiere_firma_explicita"
        return True, "permitido_asistido"

    # ─── modo autónomo: sin humano. Aquí manda la matriz ───
    if decl.get("requiere_humano") is True:
        _telemetria(tool, "requiere_humano")
        return False, "requiere_humano"   # override explícito: gana sobre la matriz

    if not _MATRIZ_AUTONOMO.get((zona, efecto), False):   # .get con default False = fail-closed
        motivo = f"zona_{zona}_bloquea_{efecto}"
        _telemetria(tool, motivo)
        return False, motivo

    return True, "permitido_autonomo"


def herramientas_autonomas() -> list[str]:
    """Las que un agente PUEDE invocar solo. Útil para armar la whitelist del loop
    de M-028 sin que el modelo elija: se le ofrece únicamente lo permitido."""
    return sorted(t for t in REGISTRO if puede_ejecutar(t, modo="autonomo")[0])


def explicar(tool: str) -> dict:
    """Ficha legible de una herramienta — para el panel de transparencia y para
    que un auditor humano entienda la decisión sin leer código."""
    decl = REGISTRO.get(tool)
    if not decl:
        return {"herramienta": tool, "declarada": False,
                "veredicto": "negada (no declarada · fail-closed)"}
    resultados = {m: puede_ejecutar(tool, modo=m) for m in ("autonomo", "asistido", "firmado")}
    return {
        "herramienta": tool,
        "declarada": True,
        "zona": decl.get("zona"),
        "efecto": decl.get("efecto"),
        "requiere_humano": bool(decl.get("requiere_humano")),
        "descripcion": decl.get("descripcion", ""),
        "modos": {m: {"permitido": r[0], "motivo": r[1]} for m, r in resultados.items()},
    }


def resumen() -> dict:
    """Estado de la frontera — para el panel de guardas del sandbox."""
    por_zona: dict[str, int] = {}
    for d in REGISTRO.values():
        z = d.get("zona", "?")
        por_zona[z] = por_zona.get(z, 0) + 1
    return {
        "declaradas": len(REGISTRO),
        "por_zona": por_zona,
        "autonomas": herramientas_autonomas(),
        "ley": "fail-closed · lo no declarado se niega",
    }


def _recargar() -> None:
    """Solo para tests: relee el JSON de disco."""
    global REGISTRO
    REGISTRO = _cargar_registro()
