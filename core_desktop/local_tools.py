"""local_tools.py · Tools locales 100% offline (Fase 2).

Cada tool local nueva del sandbox debe registrarse aditivamente en
``data/autonomia_zonas.json`` (zona=lectura) para no quedar denegada por
el fail-closed de ``core/autonomia.py``.

Fase 2 activa (2026-07-31): las 3 tools locales estan implementadas y
registradas en ``data/autonomia_zonas.json`` como zona=lectura.

Verificadas via exploracion del repo principal:
  - data/autonomia_zonas.json ya declara 6+3 tools zona=lectura:
      buscar_doctor, ver_protocolo, pulso_ecosistema,
      info_cad_protesis, buscar_evidencia_cientifica, buscar_normativa_salud,
      leer_stl_local, listar_archivos_paciente, leer_texto_local
  - ninguna requiere_humano=true
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np


def leer_stl_local(ruta: str) -> dict:
    """Lee metadatos de un STL local sin subirlo a la nube.

    Usa ``numpy-stl`` (validado en probe Fase 0). Devuelve vertices,
    bounding box, volumen y nombre del archivo. NO interpreta geometria
    clinica — solo mide la malla. L4: sin cifras inventadas.
    """
    p = Path(ruta)
    if not p.is_file():
        return {"ok": False, "motivo": "archivo_no_existe", "ruta": ruta}
    if p.suffix.lower() != ".stl":
        return {"ok": False, "motivo": "no_es_stl", "ruta": ruta}

    try:
        from stl import mesh as stl_mesh
    except ImportError:
        return {
            "ok": False,
            "motivo": "numpy_stl_no_disponible",
            "detalle": "instala numpy-stl para activar leer_stl_local",
        }

    try:
        m = stl_mesh.Mesh.from_file(str(p))
    except Exception as exc:
        return {"ok": False, "motivo": "stl_ilegible", "detalle": str(exc)}

    vertices = np.vstack((m.v0, m.v1, m.v2))
    bbox_min = vertices.min(axis=0).tolist()
    bbox_max = vertices.max(axis=0).tolist()
    tamano_mm = [round(bbox_max[i] - bbox_min[i], 4) for i in range(3)]

    # get_mass_properties() -> (volume, center_of_gravity, inertia)
    volumen_escalar = float(m.get_mass_properties()[0])
    volumen = float(round(volumen_escalar, 6))
    n_caras = int(len(m.vectors))
    n_vertices = int(len(np.unique(vertices, axis=0)))

    return {
        "ok": True,
        "archivo": p.name,
        "ruta": str(p),
        "n_caras": n_caras,
        "n_vertices": n_vertices,
        "bounding_box_mm": {
            "min": [round(c, 4) for c in bbox_min],
            "max": [round(c, 4) for c in bbox_max],
            "tamano_mm": tamano_mm,
        },
        "volumen_mm3": volumen,
        "formato": "binario" if _es_binario(p) else "ascii",
    }


def _es_binario(p: Path) -> bool:
    with open(p, "rb") as f:
        head = f.read(80)
    return b"facet" not in head.lower()


def listar_archivos_paciente(carpeta: str) -> dict:
    """Lista archivos .stl/.dcm/.pdf de una carpeta senalada por el doctor.

    Sin subir nada a la nube. Recorre solo el nivel inmediato (sin subdir)
    para evitar fuga de estructura. L6: no lee contenido, solo nombres.
    """
    c = Path(carpeta)
    if not c.is_dir():
        return {"ok": False, "motivo": "carpeta_no_existe", "carpeta": carpeta}

    extensiones = {".stl", ".dcm", ".pdf"}
    archivos = []
    for entrada in sorted(c.iterdir()):
        if entrada.is_file() and entrada.suffix.lower() in extensiones:
            try:
                tam = entrada.stat().st_size
            except OSError:
                continue
            archivos.append({
                "nombre": entrada.name,
                "extension": entrada.suffix.lower(),
                "tamano_bytes": tam,
            })

    return {
        "ok": True,
        "carpeta": str(c),
        "n_archivos": len(archivos),
        "archivos": archivos,
    }


def leer_texto_local(ruta: str) -> dict:
    """Lee un .txt simple para dar contexto adicional al agente.

    NO lee .pdf binario (requiere parser pesado y riesgo de PHI). Solo texto
    plano UTF-8/latin-1, con tope defensivo (256 KB) para no inyectar megabytes
    en el contexto del chat. L8: limite calibrado a la capacidad del contexto.
    """
    p = Path(ruta)
    if not p.is_file():
        return {"ok": False, "motivo": "archivo_no_existe", "ruta": ruta}

    if p.suffix.lower() == ".pdf":
        return {
            "ok": False,
            "motivo": "pdf_no_soportado_texto_plano",
            "detalle": "leer_texto_local solo lee .txt; los PDF requieren parser aparte",
        }

    TOPE_BYTES = 256 * 1024
    try:
        tam = p.stat().st_size
    except OSError:
        return {"ok": False, "motivo": "no_accesible", "ruta": ruta}

    truncado = tam > TOPE_BYTES
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            if truncado:
                contenido = f.read(TOPE_BYTES)
            else:
                contenido = f.read()
    except OSError as exc:
        return {"ok": False, "motivo": "lectura_fallo", "detalle": str(exc)}

    return {
        "ok": True,
        "archivo": p.name,
        "ruta": str(p),
        "tamano_bytes": tam,
        "truncado": truncado,
        "tope_bytes": TOPE_BYTES,
        "contenido": contenido,
    }
