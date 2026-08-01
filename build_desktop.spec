# -*- mode: python ; coding: utf-8 -*-
"""build_desktop.spec · Spec de PyInstaller para EIR Desktop v1.

Para construir:
    cd <raiz-del-repo-eir-desktop>
    set PYTHONPATH=<raiz-del-repo-eir-desktop>
    py -m PyInstaller build_desktop.spec --clean --noconfirm

Mitigacion del fail-closed silencioso (riesgo #2 del doc 08):
   - Empaqueta ``data/autonomia_zonas.json`` del proyecto principal DENTRO
     del bundle como recurso. Sin el, todas las tools quedan denegadas
     en runtime sin error visible.
"""

import os
from pathlib import Path

# Raiz del proyecto (donde se ejecuta pyinstaller)
PROYECTO_RAIZ = Path(os.getcwd())
DATA_ZONAS = PROYECTO_RAIZ / "data" / "autonomia_zonas.json"
ICON_PATH = PROYECTO_RAIZ / "static" / "img" / "eir_orb_oficial.ico"

block_cipher = None

a = Analysis(
    [
        "app.py",
    ],
    pathex=[
        str(PROYECTO_RAIZ),
        str(PROYECTO_RAIZ / "core"),
    ],
    binaries=[],
    datas=[
        # (origen, destino_relativo_dentro_del_bundle) - all absolute paths from PROYECTO_RAIZ
        (str(PROYECTO_RAIZ / "templates"), "templates"),
        (str(PROYECTO_RAIZ / "static_desktop"), "static_desktop"),
        # Core desktop modules (runners, tools, cliente_llm)
        (str(PROYECTO_RAIZ / "core_desktop"), "core_desktop"),
        # Core modules from the open-source fork (read-only, never modified)
        (str(PROYECTO_RAIZ / "core" / "agente_loop.py"), "core/agente_loop.py"),
        (str(PROYECTO_RAIZ / "core" / "autonomia.py"), "core/autonomia.py"),
        (str(PROYECTO_RAIZ / "core" / "shell_tools.py"), "core/shell_tools.py"),
        (str(PROYECTO_RAIZ / "core" / "contexto_chat.py"), "core/contexto_chat.py"),
        (str(PROYECTO_RAIZ / "core" / "schemas.py"), "core/schemas.py"),
        (str(PROYECTO_RAIZ / "core" / "guardas.py"), "core/guardas.py"),
        (str(PROYECTO_RAIZ / "core" / "audit_logger.py"), "core/audit_logger.py"),
        # Fail-closed silencioso mitigado: el JSON de autonomia VIAJA
        # en el bundle; el sandbox lo monta via PyInstaller.resource_path
        (str(DATA_ZONAS), "data"),
        # Plantillas parametrizadas del rol 360° (D074 B2) — el render se
        # niega si un placeholder queda sin resolver (L4)
        (str(PROYECTO_RAIZ / "data" / "plantillas_producto.json"), "data"),
        # STL sintético de prueba que usa el arnés local (scripts/smoke_agente_local.py)
        (str(PROYECTO_RAIZ / "spike_probe" / "probe_data.stl"), "spike_probe"),
    ],
    hiddenimports=[
        # Importados dinamicamente por los runners
        "core_desktop.cliente_llm",
        "core_desktop.runner_odontologo",
        "core_desktop.runner_recepcion",
        "core_desktop.runner_laboratorio",
        "core_desktop.runner_marketing",
        "core_desktop.local_tools",
        # M-052 · sesión cloud + versión
        "core_desktop.sesion",
        "core_desktop.cliente_cloud",
        "core_desktop.actualizador",
        # D074 · control de apps de escritorio (zona "producto")
        "core_desktop.app_control",
        "core_desktop.verificacion_ui",
        # Importados lazy por agente_loop
        "core.agente_loop",
        "core.autonomia",
        "core.shell_tools",
        "core.contexto_chat",
        "core.schemas",
        "core.guardas",
        "core.audit_logger",
        # Flask & pywebview
        "flask",
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        # numpy-stl (pure python, no MSVC needed)
        "numpy",
        "stl",
        # stdlib modules that pyinstaller sometimes misses
        "json", "pathlib", "uuid", "hashlib", "base64", "mimetypes",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Excluimos dependencias cloud-only del proyecto principal para
        # mantener bundle liviano y BYOK-only para lo cloud:
        "pinecone",
        "google",
        "google.genai",
        "google.generativeai",
        "anthropic",
        "boto3",
        "redis",
        "psycopg2",
        "reportlab",
        "gunicorn",
        "mercadopago",
        # GUI frameworks we don't use
        "tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
        # Heavy stdlib
        "test", "unittest", "pydoc", "doctest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="EIR_DR_Desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)
