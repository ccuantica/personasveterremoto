#!/usr/bin/env python3
"""
Exporta todo el directorio a un archivo JSON, excluyendo los registros de
fuentes externas que estén marcados como posibles duplicados de pacientes locales.

Uso:
    python export_json.py
    python export_json.py --con-duplicados   # incluye también los duplicados

Salida:
    data/directorio.json
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from import_data import DB_PATH

OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "directorio.json"


def row_to_dict(row):
    """Convierte una fila sqlite3.Row a un dict limpio."""
    return {key: row[key] for key in row.keys()}


def query_all(conn, table, where=None):
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return [row_to_dict(r) for r in conn.execute(sql)]


def export(include_duplicados=False):
    if not DB_PATH.exists():
        print(f"No se encontró la base de datos: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    pacientes = query_all(conn, "pacientes")
    faltantes = query_all(conn, "faltantes")
    localizados = query_all(conn, "localizados",
                            where=None if include_duplicados else "posible_duplicado = 0")
    reporta = query_all(conn, "reporta_personas",
                        where=None if include_duplicados else "posible_duplicado = 0")
    lp = query_all(conn, "lp_pacientes",
                   where=None if include_duplicados else "posible_duplicado = 0")
    warroom = query_all(conn, "warroom_found",
                        where=None if include_duplicados else "posible_duplicado = 0")

    total_localizados = conn.execute("SELECT COUNT(*) FROM localizados").fetchone()[0]
    duplicados_localizados = conn.execute(
        "SELECT COUNT(*) FROM localizados WHERE posible_duplicado = 1"
    ).fetchone()[0]

    total_reporta = conn.execute("SELECT COUNT(*) FROM reporta_personas").fetchone()[0]
    duplicados_reporta = conn.execute(
        "SELECT COUNT(*) FROM reporta_personas WHERE posible_duplicado = 1"
    ).fetchone()[0]

    total_lp = conn.execute("SELECT COUNT(*) FROM lp_pacientes").fetchone()[0]
    duplicados_lp = conn.execute(
        "SELECT COUNT(*) FROM lp_pacientes WHERE posible_duplicado = 1"
    ).fetchone()[0]

    total_warroom = conn.execute("SELECT COUNT(*) FROM warroom_found").fetchone()[0]
    duplicados_warroom = conn.execute(
        "SELECT COUNT(*) FROM warroom_found WHERE posible_duplicado = 1"
    ).fetchone()[0]

    conn.close()

    data = {
        "meta": {
            "generado": datetime.now(timezone.utc).isoformat(),
            "incluye_duplicados": include_duplicados,
            "totales": {
                "pacientes": len(pacientes),
                "faltantes": len(faltantes),
                "localizados_sin_duplicados": len(localizados),
                "localizados_total": total_localizados,
                "localizados_duplicados": duplicados_localizados,
                "reporta_sin_duplicados": len(reporta),
                "reporta_total": total_reporta,
                "reporta_duplicados": duplicados_reporta,
                "lp_pacientes_sin_duplicados": len(lp),
                "lp_pacientes_total": total_lp,
                "lp_pacientes_duplicados": duplicados_lp,
                "warroom_sin_duplicados": len(warroom),
                "warroom_total": total_warroom,
                "warroom_duplicados": duplicados_warroom,
                "total_sin_duplicados": len(pacientes) + len(faltantes) + len(localizados) + len(reporta) + len(lp) + len(warroom),
            },
        },
        "pacientes": pacientes,
        "faltantes": faltantes,
        "localizados": localizados,
        "reporta_personas": reporta,
        "lp_pacientes": lp,
        "warroom_found": warroom,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Exportación completa: {OUTPUT_PATH}")
    print(json.dumps(data["meta"]["totales"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Exporta el directorio a JSON."
    )
    parser.add_argument(
        "--con-duplicados",
        action="store_true",
        help="Incluye los registros marcados como posibles duplicados.",
    )
    args = parser.parse_args()
    export(include_duplicados=args.con_duplicados)
