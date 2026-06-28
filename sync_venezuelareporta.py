#!/usr/bin/env python3
"""
Sincroniza el registro consolidado de Venezuela Reporta
(https://venezuelareporta.org/api/v1/personas) con la base de datos SQLite local.

Descarga por lotes para no saturar la API pública (límite 120 req/min).
Cada ejecución avanza un lote; usa --force para reiniciar desde cero.

Uso:
    python sync_venezuelareporta.py
    python sync_venezuelareporta.py --force
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

API_BASE = "https://venezuelareporta.org/api/v1"
USER_AGENT = "Mozilla/5.0 (DirectorioPersonasSismoVenezuela/1.0; +https://localhost)"
PAGE_LIMIT = 100
MAX_RETRIES = 3
REQUEST_DELAY = 1.0
BATCH_SIZE = 5000
PROGRESS_FILE = Path(__file__).resolve().parent / "data" / ".vr_progress"

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


def token_close_match_score(tokens_a, tokens_b, threshold=0.75):
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
            if ratio >= threshold and ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
        if best_idx is not None:
            used.add(best_idx)
            matches += 1
    return matches / max(len(tokens_a), len(tokens_b))


def text_similarity(a, b):
    a = normalize_for_match(a)
    b = normalize_for_match(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    return token_close_match_score(a.split(), b.split(), threshold=0.75)


def load_pacientes_for_matching(conn):
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, nombre_completo, hospital, cedula_limpia FROM pacientes"
    )
    pacientes = []
    name_index = {}
    for row in cur.fetchall():
        name_norm = normalize_for_match(row["nombre_completo"])
        pacientes.append({
            "id": row["id"],
            "name": name_norm,
            "place": normalize_for_match(row["hospital"]),
            "cedula_limpia": row["cedula_limpia"] or "",
        })
        idx = len(pacientes) - 1
        for token in name_norm.split():
            name_index.setdefault(token, set()).add(idx)
    return pacientes, name_index


def find_best_duplicate(item, pacientes_norm, name_index):
    name = normalize_for_match(item.get("nombre"))
    place = normalize_for_match(f"{item.get('ciudad', '')} {item.get('zona', '')}")
    cedula = clean_cedula(item.get("cedula"))
    numbers = set(re.findall(r"\d+", cedula))

    name_tokens = name.split()
    candidates = set()
    for token in name_tokens:
        candidates.update(name_index.get(token, set()))
    if not candidates:
        candidates = set(range(len(pacientes_norm)))

    best_id = None
    best_score = 0.0
    for idx in candidates:
        p = pacientes_norm[idx]
        if p["cedula_limpia"] and p["cedula_limpia"] in numbers:
            return p["id"], 1.0
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


def create_table(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS reporta_personas;
        CREATE TABLE reporta_personas (
            id TEXT PRIMARY KEY,
            status TEXT,
            nombre TEXT,
            cedula TEXT,
            cedula_limpia TEXT,
            genero TEXT,
            edad INTEGER,
            ciudad TEXT,
            zona TEXT,
            ultima_vez TEXT,
            descripcion TEXT,
            foto_url TEXT,
            origen TEXT,
            verificado INTEGER,
            verificado_por TEXT,
            verificado_at TEXT,
            created_at TEXT,
            ficha_url TEXT,
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
    return {"offset": 0, "total_downloaded": 0}


def save_progress(offset, total_downloaded):
    PROGRESS_FILE.write_text(
        json.dumps({"offset": offset, "total_downloaded": total_downloaded}),
        encoding="utf-8",
    )


def sync(force=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if force:
        PROGRESS_FILE.unlink(missing_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            create_table(conn)

    with sqlite3.connect(DB_PATH) as conn_check:
        cur = conn_check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pacientes'"
        )
        if not cur.fetchone():
            print("No existe la tabla 'pacientes'. Ejecuta primero: python import_data.py")
            return

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reporta_personas'"
        )
        if not cur.fetchone():
            create_table(conn)

    progress = load_progress()
    offset = progress.get("offset", 0)
    total_downloaded = progress.get("total_downloaded", 0)

    print(f"Reanudando desde offset {offset} (ya descargados {total_downloaded})...")

    all_items = []
    total_api = None
    while len(all_items) < BATCH_SIZE:
        data = api_get("/personas", {"limit": PAGE_LIMIT, "offset": offset})
        items = data.get("personas", [])
        if total_api is None:
            total_api = data.get("total")
        if not items:
            break
        all_items.extend(items)
        offset += len(items)
        print(f"  +{len(items)} (lote {len(all_items)}/{BATCH_SIZE}; global {offset}/{total_api})")
        if len(items) < PAGE_LIMIT:
            break
        time.sleep(REQUEST_DELAY)

    if not all_items:
        print("No hay más datos para descargar.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        pacientes_norm, name_index = load_pacientes_for_matching(conn)
        print(f"Comparando {len(all_items)} registros contra {len(pacientes_norm)} pacientes locales...")

        rows = []
        duplicados = 0
        for idx, item in enumerate(all_items, 1):
            nombre = safe_str(item.get("nombre"))
            cedula = safe_str(item.get("cedula"))
            cedula_limpia = clean_cedula(cedula)
            status = safe_str(item.get("status"))
            paciente_id, score = find_best_duplicate(item, pacientes_norm, name_index)
            posible_duplicado = 1 if paciente_id else 0
            if posible_duplicado:
                duplicados += 1

            texto_busqueda = " ".join([
                normalize_text(nombre),
                normalize_text(cedula),
                cedula_limpia,
                normalize_text(status),
                normalize_text(item.get("ciudad")),
                normalize_text(item.get("zona")),
                normalize_text(item.get("ultima_vez")),
                normalize_text(item.get("descripcion")),
                normalize_text(item.get("origen")),
            ]).strip()

            rows.append((
                safe_str(item.get("id")),
                status,
                nombre,
                cedula,
                cedula_limpia,
                safe_str(item.get("genero")),
                item.get("edad") if item.get("edad") is not None else None,
                safe_str(item.get("ciudad")),
                safe_str(item.get("zona")),
                safe_str(item.get("ultima_vez")),
                safe_str(item.get("descripcion")),
                safe_str(item.get("foto_url")),
                safe_str(item.get("origen")),
                1 if item.get("verificado") else 0,
                safe_str(item.get("verificado_por")),
                safe_str(item.get("verificado_at")),
                safe_str(item.get("created_at")),
                safe_str(item.get("ficha_url")),
                posible_duplicado,
                paciente_id,
                score,
                texto_busqueda,
            ))

        conn.executemany(
            """
            INSERT OR REPLACE INTO reporta_personas
            (id, status, nombre, cedula, cedula_limpia, genero, edad, ciudad, zona,
             ultima_vez, descripcion, foto_url, origen, verificado, verificado_por,
             verificado_at, created_at, ficha_url, posible_duplicado, paciente_id,
             score_duplicado, texto_busqueda)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    total_downloaded += len(all_items)
    save_progress(offset, total_downloaded)
    print(
        f"Lote guardado: {len(all_items)} personas (offset ahora {offset}, "
        f"total descargado {total_downloaded}; {duplicados} posibles duplicados)."
    )
    if offset < (total_api or 0):
        print(f"Faltan aprox. {total_api - offset} registros. Vuelve a ejecutar el script para continuar.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sincroniza datos de Venezuela Reporta con la base de datos local."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza la resincronización desde cero.",
    )
    args = parser.parse_args()
    sync(force=args.force)
