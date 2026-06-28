# Notas para agentes

## Fuentes de datos integradas

- **Excel local** (`data/Pacientes_Sismo_Venezuela.xlsx`):
  - `pacientes` – Hoja 1
  - `faltantes` – Hoja Faltantes
- **Localizados Venezuela** (`sync_localizados.py`)
- **Venezuela Reporta** (`sync_venezuelareporta.py`)
- **Localiza Pacientes** (`sync_localizapacientes.py` + `load_lp_to_db.py`)

## Sincronización de fuentes externas

Las APIs públicas tienen límites de tasa. Los scripts de Venezuela Reporta y Localiza Pacientes descargan por lotes y guardan progreso en `data/.vr_progress` y `data/.lp_progress`. Para completar la descarga hay que ejecutar el script varias veces (o dejar corriendo `run_all_vr.py` / `run_all_lp.py`).

## Estructura de tipos en búsqueda

`app.py` agrupa fuentes en categorías de filtro:

- `pacientes` → `paciente` + `lp_paciente`
- `faltantes` → `faltante` + `reporta_buscando`
- `localizados` → `localizado` + `reporta_localizado`

## Exportación

`python export_json.py` genera `data/directorio.json` con secciones separadas por fuente y metadatos de totales.

## Tests básicos

```powershell
python test_app.py
```
