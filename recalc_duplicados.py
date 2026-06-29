#!/usr/bin/env python3
"""
Recalcula los posibles duplicados de las fuentes externas contra los pacientes locales
sin volver a descargar datos. Procesa por lotes y guarda progreso.

Uso:
    python recalc_duplicados.py
    python recalc_duplicados.py --force  # reinicia desde cero
"""
import argparse
import difflib
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

from import_data import DB_PATH, clean_cedula

STOPWORDS = {
    "de", "del", "la", "los", "las", "y", "e", "o", "u",
    "niña", "niño", "menor", "sobreviviente", "paciente", "hospital",
    "clinica", "centro", "av", "avenida", "calle", "urb", "urbanizacion",
    "edificio", "torre", "piso", "apartamento", "apt", "localizado",
}

CHUNK_SIZE = 5000
MAX_CANDIDATES = 200
PROGRESS_FILE = Path(__file__).resolve().parent / "data" / ".recalc_progress"


def normalize_for_match(text):
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t not in STOPWORDS and len(t) > 2]
    return " ".join(tokens)


def text_similarity(a, b):
    a = normalize_for_match(a)
    b = normalize_for_match(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    tokens_a = a.split()
    tokens_b = b.split()
    if not tokens_a or not tokens_b:
        return 0.0
    used = set()
    matches = 0
    for ta in tokens_a:
        best_idx = None
        best_ratio = 0.0
        for i, tb in enumerate(tokens_b):
            if i in used:
                continue
            if ta == tb:
                ratio = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
            if ratio >= 0.75 and ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
        if best_idx is not None:
            used.add(best_idx)
            matches += 1
    return matches / max(len(tokens_a), len(tokens_b))


def load_pacientes(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, nombre_completo, hospital, cedula_limpia FROM pacientes"
    )
    pacientes = []
    name_index = {}
    cedula_index = {}
    exact_names = {}
    token_counter = Counter()

    for row in cur.fetchall():
        name_norm = normalize_for_match(row["nombre_completo"])
        place_norm = normalize_for_match(row["hospital"])
        pacientes.append({
            "id": row["id"],
            "name": name_norm,
            "place": place_norm,
            "cedula_limpia": row["cedula_limpia"] or "",
        })
        idx = len(pacientes) - 1
        exact_names.setdefault(name_norm, []).append(idx)
        for token in name_norm.split():
            name_index.setdefault(token, []).append(idx)
            token_counter[token] += 1
        if row["cedula_limpia"]:
            cedula_index[row["cedula_limpia"]] = idx

    # Ordenar índices por frecuencia de token (menos frecuentes primero) para priorizar mejores candidatos
    for token in name_index:
        name_index[token].sort(key=lambda idx: token_counter.get(token, 0))

    return pacientes, name_index, exact_names, cedula_index


def find_best(name, place, cedula_limpia, pacientes, name_index, exact_names, cedula_index):
    # Coincidencia exacta por cédula
    if cedula_limpia and cedula_limpia in cedula_index:
        return pacientes[cedula_index[cedula_limpia]]["id"], 1.0

    name_norm = normalize_for_match(name)

    # Coincidencia exacta por nombre normalizado
    if name_norm in exact_names:
        return pacientes[exact_names[name_norm][0]]["id"], 1.0

    name_tokens = name_norm.split()
    if not name_tokens:
        return None, 0.0

    # Candidatos que comparten al menos un token; limitar a los más discriminativos
    candidates = set()
    for token in name_tokens:
        if token in name_index:
            candidates.update(name_index[token])
        if len(candidates) >= MAX_CANDIDATES:
            break

    if not candidates:
        return None, 0.0

    best_id = None
    best_score = 0.0
    for idx in candidates:
        p = pacientes[idx]
        name_score = text_similarity(name, p["name"])
        if name_score >= 0.85:
            return p["id"], name_score
        place_score = text_similarity(place, p["place"])
        score = name_score * 0.7 + place_score * 0.3
        if score > best_score:
            best_score = score
            best_id = p["id"]

    if best_score >= 0.80:
        return best_id, best_score
    return None, 0.0


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress), encoding="utf-8")


def recalc_table(conn, table, name_field, place_fields, cedula_field, progress, force):
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    total = cur.fetchone()[0]
    if total == 0:
        print(f"Tabla {table} vacía, se omite.")
        return

    done = 0 if force else progress.get(table, 0)
    if done >= total:
        print(f"Tabla {table} ya recalculada ({done}/{total}).")
        return

    pacientes, name_index, exact_names, cedula_index = load_pacientes(conn)
    print(f"Recalculando {table}: {total} registros contra {len(pacientes)} pacientes locales...")

    while done < total:
        cur = conn.execute(
            f"SELECT * FROM {table} ORDER BY rowid LIMIT ? OFFSET ?",
            (CHUNK_SIZE, done),
        )
        rows = cur.fetchall()
        if not rows:
            break

        updates = []
        for row in rows:
            name = row[name_field]
            place = " ".join(str(row[f]) if row[f] else "" for f in place_fields)
            ced = clean_cedula(row[cedula_field]) if cedula_field else ""
            paciente_id, score = find_best(
                name, place, ced, pacientes, name_index, exact_names, cedula_index
            )
            posible = 1 if paciente_id else 0
            updates.append((posible, paciente_id, score, row["id"]))

        conn.executemany(
            f"UPDATE {table} SET posible_duplicado = ?, paciente_id = ?, score_duplicado = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        done += len(rows)
        progress[table] = done
        save_progress(progress)
        print(f"  {table}: {done}/{total} procesados")

    dup = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE posible_duplicado = 1"
    ).fetchone()[0]
    print(f"{table}: {dup}/{total} posibles duplicados.")


def main(force=False):
    if force and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
    progress = load_progress()

    with sqlite3.connect(DB_PATH) as conn:
        tables = [
            ("reporta_personas", "nombre", ["ciudad", "zona"], "cedula"),
            ("lp_pacientes", "nombre_completo", ["hospital", "ciudad", "estado"], None),
            ("localizados", "nombre_completo", ["lugar_nombre", "direccion", "condicion"], None),
            ("warroom_found", "nombre_completo", ["ubicacion_nombre", "ubicacion_direccion", "lugar_procedencia"], "cedula"),
        ]
        for table, name_field, place_fields, ced_field in tables:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            if not cur.fetchone():
                print(f"Tabla {table} no existe, se omite.")
                continue
            recalc_table(conn, table, name_field, place_fields, ced_field, progress, force)

    print("Recálculo de duplicados completado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recalcula duplicados de fuentes externas contra pacientes locales."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinicia el recálculo desde cero.",
    )
    args = parser.parse_args()
    main(force=args.force)
