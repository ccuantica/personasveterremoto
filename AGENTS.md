# Notas para agentes

## Fuentes de datos integradas

- **Excel local** (`data/Pacientes_Sismo_Venezuela.xlsx`):
  - `pacientes` – Hoja 1
  - `faltantes` – Hoja Faltantes
- **Localizados Venezuela** (`sync_localizados.py`)
- **Venezuela Reporta** (`sync_venezuelareporta.py`)
- **Localiza Pacientes** (`sync_localizapacientes.py` + `load_lp_to_db.py`)
- **War Room** (`sync_warroom.py`)

## Sincronización de fuentes externas

Las APIs públicas tienen límites de tasa. Los scripts de Venezuela Reporta, Localiza Pacientes y War Room descargan por lotes y guardan progreso en `data/.vr_progress`, `data/.lp_progress` y `data/.warroom_progress`. Para completar la descarga hay que ejecutar el script varias veces (o dejar corriendo `run_all_vr.py` / `run_all_lp.py`).

Después de reimportar el Excel (`python import_data.py --force`) conviene recalcular los duplicados de las fuentes externas contra los pacientes locales:

```powershell
python recalc_duplicados.py
```

Esto actualiza tanto el flag `posible_duplicado` como el `paciente_id` al que apunta cada registro externo.

## Estructura de tipos en búsqueda

`app.py` agrupa fuentes en categorías de filtro:

- `pacientes` → `paciente` + `lp_paciente`
- `faltantes` → `faltante` + `reporta_buscando`
- `localizados` → `localizado` + `reporta_localizado` + `warroom_found`

## Exportación

`python export_json.py` genera `data/directorio.json` con secciones separadas por fuente y metadatos de totales. `python json_to_excel.py` convierte ese JSON en `data/directorio.xlsx`.

## Tests básicos

```powershell
python test_app.py
```
