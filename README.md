# Directorio de Personas – Sismo Venezuela

Aplicación web local para consultar y localizar personas a partir de múltiples fuentes de datos sobre el sismo en Venezuela.

La aplicación importa y sincroniza:

- **Excel local** (`Pacientes_Sismo_Venezuela.xlsx`)
  - **Hoja 1** → Pacientes internados en hospitales y centros de acopio.
  - **Faltantes** → Personas reportadas como faltantes.
- **Localizados Venezuela** → Personas localizadas en hospitales/refugios (`sync_localizados.py`).
- **Venezuela Reporta** → Registro consolidado de personas buscando, a salvo y encontradas (`sync_venezuelareporta.py`).
- **Localiza Pacientes** → Pacientes registrados en el sistema oficial de hospitales (`sync_localizapacientes.py`).
- **War Room** → Personas encontradas/verificadas desde la API de damnificadosterremotovenezuela (`sync_warroom.py`).

Los datos se almacenan en una base de datos SQLite (`data/personas.db`) y se pueden buscar por nombre, cédula, hospital, procedencia, lugar u observaciones.

## Estructura del proyecto

```
.
├── app.py                       # Aplicación Flask
├── import_data.py               # Importa el Excel a SQLite
├── sync_localizados.py          # Sincroniza Localizados Venezuela
├── sync_venezuelareporta.py     # Sincroniza Venezuela Reporta (por lotes)
├── sync_localizapacientes.py    # Sincroniza Localiza Pacientes (por lotes)
├── load_lp_to_db.py             # Carga registros descargados de Localiza Pacientes a SQLite
├── sync_warroom.py              # Sincroniza personas encontradas desde War Room
├── recalc_duplicados.py         # Recalcula duplicados tras reimportar el Excel
├── export_json.py               # Exporta el directorio consolidado a JSON
├── requirements.txt             # Dependencias de Python
├── data/
│   ├── Pacientes_Sismo_Venezuela.xlsx
│   ├── personas.db              # Generada automáticamente
│   ├── directorio.json          # Exportación consolidada
│   ├── .vr_progress             # Progreso de descarga de Venezuela Reporta
│   ├── .lp_progress             # Progreso de descarga de Localiza Pacientes
│   └── .warroom_progress        # Progreso de descarga de War Room
├── static/
│   └── style.css                # Estilos de la interfaz
└── templates/
    ├── base.html
    ├── index.html               # Buscador y resultados
    ├── detail.html              # Detalle de una persona
    └── 404.html
```

## Instalación y uso

1. Crea y activa un entorno virtual (ya se encuentra creado `.venv`):

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Instala las dependencias:

   ```powershell
   pip install -r requirements.txt
   ```

3. Genera la base de datos desde el Excel (solo la primera vez, o cuando cambie el Excel):

   ```powershell
   python import_data.py
   ```

4. Sincroniza las fuentes externas que quieras incluir:

   ```powershell
   python sync_localizados.py
   python sync_venezuelareporta.py       # repetir hasta completar
   python sync_localizapacientes.py      # repetir hasta completar
   python sync_warroom.py                # repetir hasta completar
   ```

5. Inicia la aplicación:

   ```powershell
   python app.py
   ```

6. Abre tu navegador en:

   ```
   http://localhost:5000
   ```

## Funciones principales

- Búsqueda por texto libre sobre nombres, cédulas, hospitales, procedencias, lugares y observaciones.
- Filtro por tipo de registro: **todos**, **pacientes internados**, **personas faltantes** o **personas localizadas**.
- Cada filtro agrupa las fuentes locales y las fuentes externas equivalentes.
- Integración con las APIs públicas de:
  - [Localizados Venezuela](https://localizadosvenezuela.com/api)
  - [Venezuela Reporta](https://venezuelareporta.org/api-abierta)
  - [Localiza Pacientes](https://localizapacientes.com)
- Paginación de resultados.
- Vista de detalle con toda la información disponible y enlace a la ficha original cuando existe.
- API JSON de prueba: `/api/buscar?q=...&tipo=...&page=...&duplicados=...`
- Exportación consolidada a JSON: `python export_json.py` (opción `--con-duplicados`).

## Actualizar la base de datos

Si recibes una nueva versión del archivo Excel, reemplaza `data/Pacientes_Sismo_Venezuela.xlsx` y ejecuta:

```powershell
python import_data.py --force
```

> Al forzar la reimportación del Excel se eliminan y recrean las tablas locales. Las tablas de fuentes externas no se ven afectadas, pero conviene volver a ejecutar los scripts de sincronización para rehacer la detección de duplicados.

## Sincronizar fuentes externas

### Localizados Venezuela

```powershell
python sync_localizados.py
python sync_localizados.py --force
```

### Venezuela Reporta

La API pública tiene un límite de 120 peticiones/minuto. El script descarga por lotes de 2.000 registros y guarda el progreso. Ejecútalo varias veces hasta que indique que no hay más datos:

```powershell
python sync_venezuelareporta.py
python sync_venezuelareporta.py
# ...repetir hasta completar
```

Para reiniciar desde cero:

```powershell
python sync_venezuelareporta.py --force
```

### Localiza Pacientes

La API de búsqueda requiere al menos 2 caracteres y devuelve máximo 50 resultados. El script recorre combinaciones de letras (bigramas) y guarda el progreso. Ejecútalo varias veces hasta completar todos los bigramas:

```powershell
python sync_localizapacientes.py
python sync_localizapacientes.py
# ...repetir hasta completar
```

Cuando termine, carga los registros a SQLite:

```powershell
python load_lp_to_db.py
```

Para reiniciar desde cero:

```powershell
python sync_localizapacientes.py --force
```

## Evitar duplicados con la lista local de pacientes

Los scripts de sincronización comparan cada registro externo contra los pacientes del Excel local (hoja 1). Si detectan una coincidencia de nombre/lugar suficientemente alta, marcan el registro como **posible duplicado**.

- En la búsqueda unificada se **ocultan los duplicados por defecto**.
- Puedes activar el checkbox **"Incluir posibles duplicados con la lista local"** para verlos también.
- En la vista de detalle de un registro marcado como duplicado se muestra una advertencia con enlace al paciente local equivalente.

## Exportar el directorio consolidado

### JSON

```powershell
python export_json.py
python export_json.py --con-duplicados
```

Genera `data/directorio.json` con los datos consolidados. El JSON incluye secciones separadas por fuente y metadatos con totales.

### Excel (para Google Sheets / Google Drive)

```powershell
python json_to_excel.py
```

Genera `data/directorio.xlsx` con una hoja de resumen y una hoja por cada fuente de datos, lista para subir a Google Drive y abrir con Google Sheets.

## Notas

- La búsqueda ignora acentos, mayúsculas/minúsculas y puntos en las cédulas.
- La aplicación está pensada para ejecutarse de forma local.
- Trata los datos de fuentes externas como señales ciudadanas, no como fuentes oficiales verificadas.
