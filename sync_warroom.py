#!/usr/bin/env python3
"""
Sincroniza personas encontradas/hospitalizadas desde la API de
Terremoto Venezuela War Room (https://api.damnificadosterremotovenezuela.com).

Descarga por páginas y guarda progreso para poder reanudar.

Uso:
    python sync_warroom.py
    python sync_warroom.py --force
"""
import argparse
import difflib
import json
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from import_data import DB_PATH, normalize_text, safe_str, clean_cedula

API_BASE = "https://api.damnificadosterremotovenezuela.com/api/v1"
USER_AGENT = "Mozilla/5.0 (DirectorioPersonasSismoVenezuela/1.0; +https://localhost)"
PAGE_SIZE = 100
MAX_RETRIES = 3
REQUEST_DELAY = 0.5
PROGRESS_FILE = Path(__file__).resolve().parent / "data" / ".warroom_progress"

STOPWORDS = {
    "de", "del", "la", "los", "las", "y", "e", "o", "u",
    "niña", "niño", "menor", "sobreviviente", "paciente", "hospital",
    "clinica", "centro", "av", "avenida", "calle", "urb", "urbanizacion",
    "edificio", "torre", "piso", "apartamento", "apt", "localizado",
}


def api_get(endpoint, params=None):
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{API_BASE}{endpoint}{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(attempt * 2)
                continue
            raise
        except Exception as e:
            last_error = e
            time.sleep(attempt * 2)
    raise last_error


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


def load_pacientes_for_matching(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, nombre_completo, hospital, cedula_limpia FROM pacientes"
    )
    pacientes = []
    name_index = {}
    cedula_index = {}
    exact_names = {}
    for row in cur.fetchall():
        name_norm = normalize_for_match(row["nombre_completo"])
        pacientes.append({
            "id": row["id"],
            "name": name_norm,
            "place": normalize_for_match(row["hospital"]),
            "cedula_limpia": row["cedula_limpia"] or "",
        })
        idx = len(pacientes) - 1
        exact_names.setdefault(name_norm, []).append(idx)
        for token in name_norm.split():
            name_index.setdefault(token, []).append(idx)
        if row["cedula_limpia"]:
            cedula_index[row["cedula_limpia"]] = idx
    return pacientes, name_index, exact_names, cedula_index


def find_best_duplicate(item, pacientes, name_index, exact_names, cedula_index):
    name = normalize_for_match(item.get("full_name"))
    ubicacion = item.get("ubicacion") or {}
    instalacion = ubicacion.get("instalacion") or {}
    place = normalize_for_match(
        " ".join(filter(None, [
            instalacion.get("nombre", ""),
            instalacion.get("direccion", ""),
            item.get("lugar_procedencia") or "",
        ]))
    )
    cedula = clean_cedula(item.get("document_id"))

    if cedula and cedula in cedula_index:
        return pacientes[cedula_index[cedula]]["id"], 1.0

    if name in exact_names:
        return pacientes[exact_names[name][0]]["id"], 1.0

    name_tokens = name.split()
    if not name_tokens:
        return None, 0.0

    candidates = set()
    for token in name_tokens:
        candidates.update(name_index.get(token, []))
        if len(candidates) >= 200:
            break

    best_id = None
    best_score = 0.0
    for idx in candidates:
        p = pacientes[idx]
        name_score = text_similarity(item.get("full_name", ""), p["name"])
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


def create_table(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS warroom_found;
        CREATE TABLE warroom_found (
            id TEXT PRIMARY KEY,
            nombre_completo TEXT,
            cedula TEXT,
            edad INTEGER,
            ubicacion_nombre TEXT,
            ubicacion_tipo TEXT,
            ubicacion_direccion TEXT,
            lat REAL,
            lng REAL,
            lugar_procedencia TEXT,
            relevant_info TEXT,
            fallecido INTEGER,
            source_url TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            posible_duplicado INTEGER DEFAULT 0,
            paciente_id INTEGER,
            score_duplicado REAL,
            texto_busqueda TEXT
        );
        """
    )
    conn.commit()


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"page": 1, "total_pages": None, "inserted": 0}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress), encoding="utf-8")


def row_from_item(item, pacientes, name_index, exact_names, cedula_index):
    nombre = safe_str(item.get("full_name"))
    cedula = safe_str(item.get("document_id"))
    ubicacion = item.get("ubicacion") or {}
    instalacion = ubicacion.get("instalacion") or {}
    paciente_id, score = find_best_duplicate(item, pacientes, name_index, exact_names, cedula_index)
    posible_duplicado = 1 if paciente_id else 0

    texto_busqueda = " ".join([
        normalize_text(nombre),
        normalize_text(cedula),
        clean_cedula(cedula),
        normalize_text(item.get("status")),
        normalize_text(instalacion.get("nombre")),
        normalize_text(instalacion.get("direccion")),
        normalize_text(item.get("lugar_procedencia")),
        normalize_text(item.get("relevant_info")),
    ]).strip()

    return (
        safe_str(item.get("id")),
        nombre,
        cedula,
        item.get("age") if item.get("age") is not None else None,
        safe_str(instalacion.get("nombre")),
        safe_str(instalacion.get("tipo")),
        safe_str(instalacion.get("direccion")),
        instalacion.get("lat") if instalacion.get("lat") is not None else None,
        instalacion.get("lon") if instalacion.get("lon") is not None else None,
        safe_str(item.get("lugar_procedencia")),
        safe_str(item.get("relevant_info")),
        1 if item.get("fallecido") else 0,
        safe_str(item.get("source_url")),
        safe_str(item.get("status")),
        safe_str(item.get("created_at")),
        safe_str(item.get("updated_at")),
        posible_duplicado,
        paciente_id,
        score,
        texto_busqueda,
    )


def sync(force=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if force:
        PROGRESS_FILE.unlink(missing_ok=True)

    with sqlite3.connect(DB_PATH) as conn_check:
        cur = conn_check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pacientes'"
        )
        if not cur.fetchone():
            print("No existe la tabla 'pacientes'. Ejecuta primero: python import_data.py")
            return

    progress = load_progress()
    start_page = progress.get("page", 1)

    # Primera petición para conocer totales
    first = api_get("/found-people", {"page": start_page, "page_size": PAGE_SIZE})
    pagination = first.get("pagination", {})
    total_pages = pagination.get("total_pages", 1)
    progress["total_pages"] = total_pages
    save_progress(progress)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='warroom_found'"
        )
        if not cur.fetchone():
            create_table(conn)
        elif force and start_page == 1:
            create_table(conn)

        pacientes, name_index, exact_names, cedula_index = load_pacientes_for_matching(conn)
        print(f"Comparando contra {len(pacientes)} pacientes locales...")

        page = start_page
        inserted_total = progress.get("inserted", 0)
        while page <= total_pages:
            if page > start_page:
                data = api_get("/found-people", {"page": page, "page_size": PAGE_SIZE})
            else:
                data = first
            items = data.get("data", [])
            if not items:
                break

            rows = [row_from_item(item, pacientes, name_index, exact_names, cedula_index) for item in items]
            conn.executemany(
                """
                INSERT OR REPLACE INTO warroom_found
                (id, nombre_completo, cedula, edad, ubicacion_nombre, ubicacion_tipo,
                 ubicacion_direccion, lat, lng, lugar_procedencia, relevant_info,
                 fallecido, source_url, status, created_at, updated_at,
                 posible_duplicado, paciente_id, score_duplicado, texto_busqueda)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            inserted_total += len(rows)
            progress["page"] = page + 1
            progress["inserted"] = inserted_total
            save_progress(progress)
            print(f"  página {page}/{total_pages}: +{len(rows)} (total {inserted_total})")
            page += 1
            if page <= total_pages:
                time.sleep(REQUEST_DELAY)

    print(f"Sincronización completa: {inserted_total} registros guardados.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sincroniza datos de Terremoto Venezuela War Room con la base de datos local."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza la resincronización desde cero.",
    )
    args = parser.parse_args()
    sync(force=args.force)
