#!/usr/bin/env python3
"""Carga los registros de Localiza Pacientes descargados en el archivo de progreso
a la base de datos local, sin detección de duplicados (rápido)."""
import json
import sqlite3
from pathlib import Path

from import_data import DB_PATH, normalize_text, safe_str

PROGRESS_FILE = Path(__file__).resolve().parent / "data" / ".lp_progress"


def create_table(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS lp_pacientes;
        CREATE TABLE lp_pacientes (
            id TEXT PRIMARY KEY,
            nombre_completo TEXT,
            edad INTEGER,
            condicion TEXT,
            hospital TEXT,
            ciudad TEXT,
            estado TEXT,
            fecha_ingreso TEXT,
            lat REAL,
            lng REAL,
            direccion TEXT,
            posible_duplicado INTEGER DEFAULT 0,
            paciente_id INTEGER,
            score_duplicado REAL,
            texto_busqueda TEXT
        );
        """
    )
    conn.commit()


def load():
    with PROGRESS_FILE.open("r", encoding="utf-8", errors="ignore") as f:
        progress = json.load(f)
    records = progress.get("records", {})
    if not records:
        print("No hay registros descargados.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lp_pacientes'"
        )
        if not cur.fetchone():
            create_table(conn)
        rows = []
        for rid, item in records.items():
            nombre_completo = safe_str(item.get("nombreCompleto"))
            texto_busqueda = " ".join([
                normalize_text(nombre_completo),
                normalize_text(item.get("hospital")),
                normalize_text(item.get("ciudad")),
                normalize_text(item.get("estado")),
                normalize_text(item.get("condicion")),
                normalize_text(item.get("direccion")),
            ]).strip()
            rows.append((
                rid,
                nombre_completo,
                item.get("edad") if item.get("edad") is not None else None,
                safe_str(item.get("condicion")),
                safe_str(item.get("hospital")),
                safe_str(item.get("ciudad")),
                safe_str(item.get("estado")),
                safe_str(item.get("fechaIngreso")),
                item.get("lat") if item.get("lat") is not None else None,
                item.get("lng") if item.get("lng") is not None else None,
                safe_str(item.get("direccion")),
                0,
                None,
                0.0,
                texto_busqueda,
            ))
        conn.executemany(
            """
            INSERT OR REPLACE INTO lp_pacientes
            (id, nombre_completo, edad, condicion, hospital, ciudad, estado, fecha_ingreso,
             lat, lng, direccion, posible_duplicado, paciente_id, score_duplicado, texto_busqueda)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    print(f"Cargados {len(rows)} registros de Localiza Pacientes a la base de datos.")


if __name__ == "__main__":
    load()
