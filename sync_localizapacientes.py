#!/usr/bin/env python3
"""
Sincroniza pacientes del sistema oficial Localiza Pacientes
(https://localizapacientes.com) con la base de datos local.

La API de búsqueda requiere al menos 2 caracteres y devuelve máximo 50
resultados. Se descargan combinaciones de 2 letras (bigramas) y se
deduplican por ID. El progreso se guarda para poder reanudar.

Uso:
    python sync_localizapacientes.py
    python sync_localizapacientes.py --force
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

API_BASE = "https://localizapacientes.com"
USER_AGENT = "Mozilla/5.0 (DirectorioPersonasSismoVenezuela/1.0; +https://localhost)"
MAX_RETRIES = 3
REQUEST_DELAY = 0.5
BATCH_BIGRAMS = 50
PROGRESS_FILE = Path(__file__).resolve().parent / "data" / ".lp_progress"

# Alfabeto para generar bigramas de búsqueda
ALPHABET = "abcdefghijklmnopqrstuvwxyzáéíóúñ"

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
    name = normalize_for_match(item.get("nombreCompleto"))
    place = normalize_for_match(f"{item.get('hospital', '')} {item.get('ciudad', '')} {item.get('estado', '')}")
    cedula = clean_cedula(item.get("cedula")) if item.get("cedula") else ""
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


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed": [], "records": {}}


def save_progress(processed, records):
    PROGRESS_FILE.write_text(
        json.dumps({"processed": processed, "records": records}, ensure_ascii=False),
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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lp_pacientes'"
        )
        if not cur.fetchone():
            create_table(conn)

    progress = load_progress()
    processed = set(progress.get("processed", []))
    records = progress.get("records", {})

    all_bigrams = [a + b for a in ALPHABET for b in ALPHABET]
    remaining = [b for b in all_bigrams if b not in processed]

    if not remaining:
        print("Todos los bigramas ya fueron procesados. Usa --force para reiniciar.")
        return

    batch = remaining[:BATCH_BIGRAMS]
    print(f"Procesando {len(batch)} bigramas ({len(remaining)} restantes)...")

    new_records = 0
    for bigram in batch:
        try:
            data = api_get("/api/search", {"q": bigram})
            for item in data.get("resultados", []):
                rid = safe_str(item.get("id"))
                if rid not in records:
                    new_records += 1
                records[rid] = item
        except Exception as e:
            print(f"  Error en bigrama '{bigram}': {e}")
        processed.add(bigram)
        time.sleep(REQUEST_DELAY)

    save_progress(sorted(processed), records)
    print(f"Bigramas procesados: {len(processed)}/{len(all_bigrams)}. "
          f"Registros únicos acumulados: {len(records)}. Nuevos en este lote: {new_records}.")

    if len(processed) == len(all_bigrams):
        print("Descarga completa. Cargando a la base de datos y detectando duplicados...")
        with sqlite3.connect(DB_PATH) as conn:
            pacientes_norm, name_index = load_pacientes_for_matching(conn)
            rows = []
            duplicados = 0
            for rid, item in records.items():
                nombre_completo = safe_str(item.get("nombreCompleto"))
                paciente_id, score = find_best_duplicate(item, pacientes_norm, name_index)
                posible_duplicado = 1 if paciente_id else 0
                if posible_duplicado:
                    duplicados += 1
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
                    item.get("edad") if item.get("edad") is not None else None,
                    safe_str(item.get("condicion")),
                    safe_str(item.get("hospital")),
                    safe_str(item.get("ciudad")),
                    safe_str(item.get("estado")),
                    safe_str(item.get("fechaIngreso")),
                    item.get("lat") if item.get("lat") is not None else None,
                    item.get("lng") if item.get("lng") is not None else None,
                    safe_str(item.get("direccion")),
                    posible_duplicado,
                    paciente_id,
                    score,
                    texto_busqueda,
                ))
            conn.executemany(
                """
                INSERT OR REPLACE INTO lp_pacientes
                (id, edad, condicion, hospital, ciudad, estado, fecha_ingreso,
                 lat, lng, direccion, posible_duplicado, paciente_id, score_duplicado, texto_busqueda)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        print(f"Base de datos actualizada: {len(rows)} registros ({duplicados} posibles duplicados).")


def load_to_db():
    progress = load_progress()
    records = progress.get("records", {})
    if not records:
        print("No hay registros en el archivo de progreso.")
        return
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lp_pacientes'"
        )
        if not cur.fetchone():
            create_table(conn)
        pacientes_norm, name_index = load_pacientes_for_matching(conn)
        rows = []
        duplicados = 0
        for rid, item in records.items():
            nombre_completo = safe_str(item.get("nombreCompleto"))
            paciente_id, score = find_best_duplicate(item, pacientes_norm, name_index)
            posible_duplicado = 1 if paciente_id else 0
            if posible_duplicado:
                duplicados += 1
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
                item.get("edad") if item.get("edad") is not None else None,
                safe_str(item.get("condicion")),
                safe_str(item.get("hospital")),
                safe_str(item.get("ciudad")),
                safe_str(item.get("estado")),
                safe_str(item.get("fechaIngreso")),
                item.get("lat") if item.get("lat") is not None else None,
                item.get("lng") if item.get("lng") is not None else None,
                safe_str(item.get("direccion")),
                posible_duplicado,
                paciente_id,
                score,
                texto_busqueda,
            ))
        conn.executemany(
            """
            INSERT OR REPLACE INTO lp_pacientes
            (id, edad, condicion, hospital, ciudad, estado, fecha_ingreso,
             lat, lng, direccion, posible_duplicado, paciente_id, score_duplicado, texto_busqueda)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    print(f"Base de datos actualizada: {len(rows)} registros ({duplicados} posibles duplicados).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sincroniza pacientes de Localiza Pacientes con la base de datos local."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinicia la sincronización desde cero.",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Carga los registros descargados a la base de datos y detecta duplicados.",
    )
    args = parser.parse_args()
    if args.load:
        load_to_db()
    else:
        sync(force=args.force)
