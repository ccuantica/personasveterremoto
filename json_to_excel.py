#!/usr/bin/env python3
"""
Convierte el directorio consolidado en JSON a un archivo Excel con una hoja por fuente.

Uso:
    python json_to_excel.py
    python json_to_excel.py --con-duplicados

Salida:
    data/directorio.xlsx
"""
import argparse
import json
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
JSON_PATH = BASE_DIR / "data" / "directorio.json"
OUTPUT_PATH = BASE_DIR / "data" / "directorio.xlsx"

SHEETS = [
    ("Pacientes", "pacientes"),
    ("Faltantes", "faltantes"),
    ("Localizados", "localizados"),
    ("Venezuela Reporta", "reporta_personas"),
    ("Localiza Pacientes", "lp_pacientes"),
    ("War Room", "warroom_found"),
]


def json_to_excel(include_duplicados=False):
    if not JSON_PATH.exists():
        print(f"No se encontró {JSON_PATH}. Ejecuta primero: python export_json.py")
        return

    with JSON_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    totales = meta.get("totales", {})

    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        # Hoja de resumen
        resumen = []
        for label, key in [
            ("Pacientes", "pacientes"),
            ("Faltantes", "faltantes"),
            ("Localizados (sin duplicados)", "localizados_sin_duplicados"),
            ("Localizados (total)", "localizados_total"),
            ("Localizados (duplicados)", "localizados_duplicados"),
            ("Venezuela Reporta (sin duplicados)", "reporta_sin_duplicados"),
            ("Venezuela Reporta (total)", "reporta_total"),
            ("Venezuela Reporta (duplicados)", "reporta_duplicados"),
            ("Localiza Pacientes", "lp_pacientes_total"),
            ("War Room (sin duplicados)", "warroom_sin_duplicados"),
            ("War Room (total)", "warroom_total"),
            ("War Room (duplicados)", "warroom_duplicados"),
            ("Total sin duplicados", "total_sin_duplicados"),
        ]:
            resumen.append({"Concepto": label, "Cantidad": totales.get(key, 0)})
        pd.DataFrame(resumen).to_excel(writer, sheet_name="Resumen", index=False)

        # Hojas por fuente
        for sheet_name, key in SHEETS:
            records = data.get(key, [])
            if not records:
                # Crear hoja vacía con encabezado mínimo
                pd.DataFrame({"mensaje": ["Sin registros"]}).to_excel(
                    writer, sheet_name=sheet_name, index=False
                )
                continue

            df = pd.json_normalize(records)
            # Reordenar columnas comunes al principio si existen
            common = ["id", "nombre_completo", "nombre", "cedula", "edad", "ubicacion", "hospital", "estado_o_seccion", "condicion", "status", "observaciones", "fecha"]
            cols = [c for c in common if c in df.columns] + [c for c in df.columns if c not in common]
            df = df[cols]
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"Excel generado: {OUTPUT_PATH}")
    print(f"Hojas: Resumen, {', '.join(s for s, _ in SHEETS)}")
    print(f"Total sin duplicados: {totales.get('total_sin_duplicados', 0)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convierte el directorio JSON a Excel."
    )
    parser.add_argument(
        "--con-duplicados",
        action="store_true",
        help="Usa el JSON que incluye duplicados (debe existir o generarse con export_json.py --con-duplicados).",
    )
    args = parser.parse_args()
    json_to_excel(include_duplicados=args.con_duplicados)
