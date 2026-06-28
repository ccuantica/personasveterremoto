#!/usr/bin/env python3
"""
Importa el archivo Excel de pacientes/faltantes del sismo en Venezuela
a una base de datos SQLite local.
"""
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXCEL_PATH = DATA_DIR / "Pacientes_Sismo_Venezuela.xlsx"
DB_PATH = DATA_DIR / "personas.db"


def normalize_text(text):
    """Devuelve texto en minúsculas, sin acentos y sin espacios extra."""
    if text is None:
        return ""
    text = str(text).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def clean_cedula(cedula):
    """Elimina puntos, espacios y guiones de la cédula para facilitar búsquedas."""
    if cedula is None:
        return ""
    cedula = str(cedula).strip().lower()
    if cedula in ("", "no documentado", "sin documento", "nan"):
        return ""
    return re.sub(r"[.\s-]", "", cedula)


def safe_str(value):
    """Convierte un valor a cadena, dejando vacío si es nulo/nan."""
    if value is None:
        return ""
    value = str(value).strip()
    if value.lower() in ("nan", "nat", "none"):
        return ""
    return value


def safe_int(value):
    """Convierte un valor a entero si es posible, de lo contrario cadena."""
    try:
        if pd.isna(value):
            return ""
        return str(int(float(value)))
    except (ValueError, TypeError):
        return safe_str(value)


def create_tables(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS pacientes;
        DROP TABLE IF EXISTS faltantes;

        CREATE TABLE pacientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital TEXT,
            apellido TEXT,
            nombre TEXT,
            nombre_completo TEXT,
            cedula TEXT,
            cedula_limpia TEXT,
            edad TEXT,
            estado TEXT,
            observaciones TEXT,
            ultima_actualizacion TEXT,
            fecha_registro TEXT,
            texto_busqueda TEXT
        );

        CREATE TABLE faltantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT,
            nombre_completo TEXT,
            nombres TEXT,
            apellidos TEXT,
            cedula TEXT,
            cedula_limpia TEXT,
            edad TEXT,
            procedencia TEXT,
            servicio_sala TEXT,
            observaciones TEXT,
            seccion TEXT,
            fecha TEXT,
            texto_busqueda TEXT
        );
        """
    )
    conn.commit()


def import_pacientes(conn, df):
    rows = []
    for _, row in df.iterrows():
        hospital = safe_str(row.get("Hospital"))
        apellido = safe_str(row.get("Apellido"))
        nombre = safe_str(row.get("Nombre"))
        nombre_completo = f"{apellido} {nombre}".strip()
        cedula = safe_str(row.get("Cédula"))
        cedula_limpia = clean_cedula(cedula)
        edad = safe_int(row.get("Edad"))
        estado = safe_str(row.get("Estado"))
        observaciones = safe_str(row.get("Observaciones"))
        ultima_actualizacion = safe_str(row.get("Última Actualización"))
        fecha_registro = safe_str(row.get("Fecha Registro"))

        texto_busqueda = " ".join(
            [
                normalize_text(hospital),
                normalize_text(apellido),
                normalize_text(nombre),
                normalize_text(nombre_completo),
                normalize_text(cedula),
                cedula_limpia,
                normalize_text(edad),
                normalize_text(estado),
                normalize_text(observaciones),
                normalize_text(ultima_actualizacion),
                normalize_text(fecha_registro),
            ]
        ).strip()

        rows.append(
            (
                hospital,
                apellido,
                nombre,
                nombre_completo,
                cedula,
                cedula_limpia,
                edad,
                estado,
                observaciones,
                ultima_actualizacion,
                fecha_registro,
                texto_busqueda,
            )
        )

    conn.executemany(
        """
        INSERT INTO pacientes
        (hospital, apellido, nombre, nombre_completo, cedula, cedula_limpia,
         edad, estado, observaciones, ultima_actualizacion, fecha_registro, texto_busqueda)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def import_faltantes(conn, df):
    rows = []
    for _, row in df.iterrows():
        numero = safe_int(row.get("N°"))
        nombre_completo_raw = safe_str(row.get("APELLIDOS_Y_NOMBRES"))
        # Si existe información separada, úsala; de lo contrario queda vacío.
        nombres = safe_str(row.get("NOMBRES"))
        apellidos = safe_str(row.get("APELLIDOS"))
        nombre_completo = (
            f"{apellidos} {nombres}".strip() or nombre_completo_raw
        )
        cedula = safe_str(row.get("CEDULA"))
        cedula_limpia = clean_cedula(cedula)
        edad = safe_int(row.get("EDAD"))
        procedencia = safe_str(row.get("PROCEDENCIA"))
        servicio_sala = safe_str(row.get("SERVICIO_SALA"))
        observaciones = safe_str(row.get("OBSERVACIONES"))
        seccion = safe_str(row.get("SECCION"))
        fecha = safe_str(row.get("FECHA"))

        texto_busqueda = " ".join(
            [
                normalize_text(nombre_completo),
                normalize_text(nombre_completo_raw),
                normalize_text(nombres),
                normalize_text(apellidos),
                normalize_text(cedula),
                cedula_limpia,
                normalize_text(edad),
                normalize_text(procedencia),
                normalize_text(servicio_sala),
                normalize_text(observaciones),
                normalize_text(seccion),
                normalize_text(fecha),
            ]
        ).strip()

        rows.append(
            (
                numero,
                nombre_completo,
                nombres,
                apellidos,
                cedula,
                cedula_limpia,
                edad,
                procedencia,
                servicio_sala,
                observaciones,
                seccion,
                fecha,
                texto_busqueda,
            )
        )

    conn.executemany(
        """
        INSERT INTO faltantes
        (numero, nombre_completo, nombres, apellidos, cedula, cedula_limpia,
         edad, procedencia, servicio_sala, observaciones, seccion, fecha, texto_busqueda)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def import_all(force=False):
    """Lee el Excel y carga/actualiza la base de datos SQLite."""
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"No se encontró el archivo Excel: {EXCEL_PATH}")

    if DB_PATH.exists() and not force:
        # Si las tablas ya existen, no reimportamos para no perder datos.
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pacientes', 'faltantes')"
            )
            if len(cur.fetchall()) == 2:
                print("La base de datos ya existe. Usa force=True para reimportar.")
                return

    print(f"Leyendo {EXCEL_PATH} ...")
    pacientes_df = pd.read_excel(EXCEL_PATH, sheet_name="Hoja 1", dtype=str)
    faltantes_df = pd.read_excel(EXCEL_PATH, sheet_name="Faltantes", dtype=str)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        create_tables(conn)
        n_pac = import_pacientes(conn, pacientes_df)
        n_fal = import_faltantes(conn, faltantes_df)

    print(f"Importación completa: {n_pac} pacientes, {n_fal} faltantes.")
    print(f"Base de datos guardada en: {DB_PATH}")


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv or "-f" in sys.argv
    import_all(force=force)
