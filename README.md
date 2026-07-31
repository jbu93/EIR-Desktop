# EIR-Desktop

Copiloto clínico odontológico de escritorio — versión de código abierto
(open core) de **EIR DR.** (https://eirdr.com).

Este repositorio contiene el cliente de escritorio (Flask + Jinja + pywebview,
sin React) y el harness agéntico genérico: loop de agente, frontera de
autonomía (fail-closed), herramientas locales de archivo 100% offline, y un
arnés de verificación de 15 propiedades.

## Qué es esto y qué no es

- **Sí:** un sandbox de escritorio con 4 roles (odontólogo, recepción,
  laboratorio, marketing), loop agéntico con presupuesto de pasos, 3
  herramientas locales reales (leer STL, listar archivos del paciente, leer
  texto), y una frontera de autonomía declarativa.
- **No:** el motor clínico completo de EIR DR. El RAG científico curado, el
  validador clínico, los protocolos clínicos, el catálogo de especialistas y la
  consulta normativa colombiana viven en el backend propietario de
  `eirdr.com/api/v1/inference` (suscripción). En esta versión esas herramientas
  se degradan **honesto**: nunca inventan un dato, declaran que la capacidad
  completa requiere suscripción.

## Stack

- Flask + Jinja (no React)
- pywebview (ventana nativa)
- numpy-stl (metadatos de mallas STL locales)
- Pydantic (schemas)
- BYOK (bring your own key) opcional: si el doctor define su propia clave de
  un proveedor compatible, el cliente puede usarla localmente.

## Requisitos

- Python 3.12+
- `pip install -r requirements.txt`

## Uso

```bash
set PYTHONPATH=<este-repo>
python app.py
```

Abre `http://127.0.0.1:5175/`. Endpoints:

- `GET /health` — estado
- `GET /api/shell/roles` — roles disponibles
- `GET /api/shell/contrato` — contrato JSON del gateway cloud
- `POST /api/shell/conversar` — `{rol, mensaje, historial?}`

## Frontera de autonomía

`data/autonomia_zonas.json` declara, por herramienta, zona (clínica /
investigación / administrativa) y efecto (lectura / escritura). Toda
herramienta NO declarada queda **denegada** (fail-closed). Ver
`core/autonomia.py`.

## Arnés de verificación

```bash
python scripts/smoke_agente_local.py
```

Afirma 15 propiedades (fail-closed, presupuesto de pasos, declaración honesta
de fallos, herramientas locales reales, mutaciones de frontera). Sin red, sin
Flask, sin GUI.

## Estructura

```
app.py                     Flask + pywebview
core/                      harness agéntico genérico (loop, autonomía, contexto, guardas)
core_desktop/              runners por rol, hooks, herramientas locales
data/                      frontera de autonomía (JSON versionado)
templates/ static_desktop/ interfaz
scripts/                   arnés de verificación
spike_probe/               STL sintético de prueba (sin PHI)
```

## Licencia

MIT — ver `LICENSE`.

Los protocolos clínicos (`skills/`), el RAG científico y el validador clínico
son propietarios de EIR DR. y no forman parte de este repositorio. Las
herramientas locales de este fork no interpretan geometría clínica ni tocan
datos de pacientes: solo metadatos de archivos.
