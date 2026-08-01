# RELEASE_DESKTOP.md · Runbook de release del EIR DR. Desktop

> **Protocolo para publicar una versión nueva del desktop SIN fallar.**
> Aplica a cualquier agente/IA y al Soberano. Procedimiento exacto, verificable,
> con las 5 trampas conocidas y cómo evitarlas.

## Regla de oro

> **`__version__` (en `core_desktop/__init__.py`) DEBE coincidir con el tag del
> release** (sin la `v`). Si no, el banner de "nueva versión" nunca converge
> (bug real detectado en D077: código `1.0.0` vs release `1.0.1-beta`).

## El ciclo completo (9 pasos)

| # | Paso | Dónde | Quién |
|---|---|---|---|
| 1 | Cambios en el sandbox | `eir_desktop_v1/` (repo privado) | agente |
| 2 | Sync al repo público | `jbu93/EIR-Desktop` (estructura plana) | agente |
| 3 | **Bump `__version__`** en `core_desktop/__init__.py` | público | agente |
| 4 | Compuertas: `python scripts/smoke_agente_local.py` + `pytest` | clon público | agente |
| 5 | `git commit` + `push origin main` | público → CI (smoke + build) | agente |
| 6 | `git tag vX.Y.Z-beta` + `push origin vX.Y.Z-beta` | público → CI publica release | agente |
| 7 | Calcular SHA256 del `.exe` publicado (descargar del release y hashear ESO) | agente |
| 8 | **Railway (panel): 4 env vars** | panel web | Soberano |
| 9 | Verificar `/api/desktop/version` + cadena `/release/windows` | prod | agente |

## Paso 8 — las 4 env vars exactas (Railway → Variables del proyecto EIR-DR)

```
EIR_DESKTOP_VERSION=X.Y.Z-beta
EIR_DESKTOP_DOWNLOAD_URL=https://github.com/jbu93/EIR-Desktop/releases/download/vX.Y.Z-beta/EIR_DR_Desktop.exe
EIR_DESKTOP_SHA256=<hash exacto del .exe publicado>
EIR_DESKTOP_NOTAS=<una línea: qué cambió>
```

⚠️ La URL lleva el **tag EXACTO** (`vX.Y.Z-beta`), idéntico al paso 6.

## Las 5 trampas conocidas (checklist anti-cagada)

1. **`__version__` ≠ tag** → banner infinito. Verifica siempre: `__version__` == tag sin `v`.
2. **SHA256 calculado de un `.exe` distinto al publicado** (ej. build local viejo).
   → Siempre descargar del release de GitHub y hashear ESE archivo.
3. **Typo en env var** (espacio, `https//`, mayúsculas). → Copiar-pegar, no teclear.
4. **Tag pusheado antes que el build del `main` esté verde** → release incompleto.
   → Esperar CI verde en `main` antes del tag.
5. **`EIR_DESKTOP_NOTAS` con caracteres raros** → texto plano simple.

## Auto-update (D078) — cómo llega la versión al usuario

- El desktop consulta `eirdr.com/api/desktop/version` → si hay versión más nueva
  y el kill-switch `EIR_DESKTOP_AUTOUPDATE` está encendido, muestra
  **"Actualizar ahora"** → confirmación → descarga con barra de progreso →
  verifica SHA256 (L4) → reinicia sola. El enlace "Descargar" queda como
  respaldo manual.
- **Kill-switch (L10):** el código nace APAGADO (default `0`). El release lo
  enciende vía `eir_desktop_runtime_hook.py` (runtime hook de PyInstaller en
  `build_desktop.spec`). Publicar un build = activación deliberada.
- Si se quiere un build SIN auto-update: quitar el hook del spec y reconstruir.

## Verificación final (paso 9)

```bash
curl -s https://eirdr.com/api/desktop/version
# debe responder ok:true con version_desktop = X.Y.Z-beta y el sha256 del paso 7

curl -sL -o NUL -w "%{http_code}" https://eirdr.com/release/windows
# 200 + descarga del .exe nuevo
```
