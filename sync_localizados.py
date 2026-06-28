#!/usr/bin/env python3
"""
Sincroniza los datos públicos de Localizados Venezuela
(https://localizadosvenezuela.com/api) con la base de datos SQLite local.

Durante la sincronización se detectan posibles duplicados con los pacientes
ya registrados en la hoja "Hoja 1" del Excel local, para evitar repetir
información en las búsquedas unificadas.

Uso:
    python sync_localizados.py
    python sync_localizados.py --force   # fuerza resincronización completa
"""
import argparse
import difflib
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
import json

from import_data import DB_PATH, normalize_text, safe_str

API_BASE = "https://localizadosvenezuela.com"
USER_AGENT = "Mozilla/5.0 (DirectorioPersonasSismoVenezuela/1.0; +https://localhost)"
PAGE_LIMIT = 100
MAX_RETRIES = 3

# Palabras que no aportan a la comparación de nombres/lugares
STOPWORDS = {
    "de", "del", "la", "los", "las", "y", "e", "o", "u",
    "niña", "niño", "menor", "sobreviviente", "paciente", "hospital",
    "clinica", "centro", "av", "avenida", "calle", "urb", "urbanizacion",
    "edificio", "torre", "piso", "apartamento", "apt", "localizado",
}


def api_get(endpoint, params=None):
    """Realiza una petición GET a la API pública."""
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
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


def fetch_all_localizados():
    """Descarga todos los registros paginando la API."""
    all_items = []
    page = 1
    empty_count = 0
    print("Descargando localizados desde la API...")
    while True:
        data = api_get("/api/v1/localizados", {"page": page, "limit": PAGE_LIMIT})
        items = data.get("data", [])
        if not items:
            empty_count += 1
            if empty_count >= 2:
                break
            page += 1
            continue
        empty_count = 0
        all_items.extend(items)
        print(f"  Página {page}: {len(items)} registros (total acumulado: {len(all_items)})")
        page += 1
    print(f"Total descargado: {len(all_items)} localizados")
    return all_items


def normalize_for_match(text):
    """Limpia un texto para comparaciones de similitud."""
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [
        t for t in text.split()
        if t not in STOPWORDS and len(t) > 2
    ]
    return " ".join(tokens)


def token_close_match_score(tokens_a, tokens_b, threshold=0.75):
    """
    Compara dos listas de tokens permitiendo coincidencias aproximadas
    (por ejemplo, typos o variaciones menores en los apellidos).
    Devuelve un score entre 0.0 y 1.0.
    """
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
    """Similitud entre dos textos normalizados (0.0 - 1.0)."""
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
    return token_close_match_score(tokens_a, tokens_b, threshold=0.75)


def find_best_duplicate(localizado, pacientes_norm, name_index):
    """
    Busca el paciente local más parecido a un localizado.
    Devuelve (paciente_id, score). score 0 si no hay coincidencia.
    """
    loc_name = normalize_for_match(localizado.get("nombreCompleto"))
    loc_place = normalize_for_match(
        f"{localizado.get('lugarNombre', '')} {localizado.get('direccion', '')}"
    )
    # Números presentes en observaciones/dirección (pueden contener cédulas)
    loc_obs = f"{localizado.get('observaciones', '')} {localizado.get('direccion', '')}"
    loc_numbers = set(re.findall(r"\d+", loc_obs))

    # Reducimos candidatos usando un índice invertido por token exacto.
    name_tokens = loc_name.split()
    candidate_indices = set()
    for token in name_tokens:
        candidate_indices.update(name_index.get(token, set()))
    if not candidate_indices:
        candidate_indices = set(range(len(pacientes_norm)))

    best_id = None
    best_score = 0.0

    for idx in candidate_indices:
        p = pacientes_norm[idx]
        # Coincidencia fuerte por cédula
        if p["cedula_limpia"] and p["cedula_limpia"] in loc_numbers:
            return p["id"], 1.0

        name_score = text_similarity(loc_name, p["name"])

        # Coincidencia de nombre casi exacta: duplicado fuerte
        if name_score >= 0.85:
            return p["id"], name_score

        place_score = text_similarity(loc_place, p["place"])
        # Pesos: el nombre pesa más que el lugar
        score = name_score * 0.7 + place_score * 0.3

        if score > best_score:
            best_score = score
            best_id = p["id"]

    # Umbral alto para combinaciones nombre + lugar
    if best_score >= 0.80:
        return best_id, best_score
    return None, 0.0


def create_table(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS localizados;

        CREATE TABLE localizados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE,
            nombre_completo TEXT,
            direccion TEXT,
            observaciones TEXT,
            condicion TEXT,
            lugar_slug TEXT,
            lugar_nombre TEXT,
            lugar_tipo TEXT,
            fuente_tipo TEXT,
            fuente_nombre TEXT,
            fuente_notas TEXT,
            fuente_url TEXT,
            fuente_fecha TEXT,
            publicado_en TEXT,
            posible_duplicado INTEGER DEFAULT 0,
            paciente_id INTEGER,
            score_duplicado REAL,
            texto_busqueda TEXT
        );
        """
    )
    conn.commit()


def load_pacientes_for_matching(conn):
    """Carga los pacientes locales con sus textos normalizados y un índice por token."""
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, nombre_completo, hospital, cedula_limpia
        FROM pacientes
        """
    )
    pacientes = []
    name_index = {}
    for row in cur.fetchall():
        name_norm = normalize_for_match(row["nombre_completo"])
        pacientes.append(
            {
                "id": row["id"],
                "name": name_norm,
                "place": normalize_for_match(row["hospital"]),
                "cedula_limpia": row["cedula_limpia"] or "",
            }
        )
        idx = len(pacientes) - 1
        for token in name_norm.split():
            name_index.setdefault(token, set()).add(idx)
    return pacientes, name_index


def sync(force=False):
    """Sincroniza la tabla localizados con la API y detecta duplicados."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Si la tabla ya existe y no se fuerza, no hacemos nada.
    if not force and DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='localizados'"
            )
            if cur.fetchone():
                print("La tabla localizados ya existe. Usa --force para resincronizar.")
                return

    # Asegurar que existan pacientes para comparar
    with sqlite3.connect(DB_PATH) as conn_check:
        cur = conn_check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pacientes'"
        )
        if not cur.fetchone():
            print("No existe la tabla 'pacientes'. Ejecuta primero: python import_data.py")
            return

    items = fetch_all_localizados()
    if not items:
        print("No se obtuvieron datos de la API.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        create_table(conn)
        pacientes_norm, name_index = load_pacientes_for_matching(conn)
        print(f"Comparando {len(items)} localizados contra {len(pacientes_norm)} pacientes locales...")

        rows = []
        duplicados = 0
        for idx, item in enumerate(items, 1):
            slug = safe_str(item.get("slug"))
            nombre_completo = safe_str(item.get("nombreCompleto"))
            direccion = safe_str(item.get("direccion"))
            observaciones = safe_str(item.get("observaciones"))
            condicion = safe_str(item.get("condicion"))
            lugar_slug = safe_str(item.get("lugarSlug"))
            lugar_nombre = safe_str(item.get("lugarNombre"))
            lugar_tipo = safe_str(item.get("lugarTipo"))

            fuente = item.get("fuente") or {}
            fuente_tipo = safe_str(fuente.get("tipo"))
            fuente_nombre = safe_str(fuente.get("nombre"))
            fuente_notas = safe_str(fuente.get("notas"))
            fuente_url = safe_str(fuente.get("url"))
            fuente_fecha = safe_str(fuente.get("fecha"))
            publicado_en = safe_str(item.get("publicadoEn"))

            paciente_id, score = find_best_duplicate(item, pacientes_norm, name_index)
            posible_duplicado = 1 if paciente_id else 0
            if posible_duplicado:
                duplicados += 1

            texto_busqueda = " ".join(
                [
                    normalize_text(nombre_completo),
                    normalize_text(direccion),
                    normalize_text(observaciones),
                    normalize_text(condicion),
                    normalize_text(lugar_nombre),
                    normalize_text(lugar_tipo),
                    normalize_text(fuente_nombre),
                    normalize_text(fuente_notas),
                    normalize_text(fuente_fecha),
                    normalize_text(publicado_en),
                ]
            ).strip()

            rows.append(
                (
                    slug,
                    nombre_completo,
                    direccion,
                    observaciones,
                    condicion,
                    lugar_slug,
                    lugar_nombre,
                    lugar_tipo,
                    fuente_tipo,
                    fuente_nombre,
                    fuente_notas,
                    fuente_url,
                    fuente_fecha,
                    publicado_en,
                    posible_duplicado,
                    paciente_id,
                    score,
                    texto_busqueda,
                )
            )

            if idx % 500 == 0:
                print(f"  Procesados {idx}/{len(items)} ({duplicados} duplicados encontrados)")

        conn.executemany(
            """
            INSERT INTO localizados
            (slug, nombre_completo, direccion, observaciones, condicion,
             lugar_slug, lugar_nombre, lugar_tipo, fuente_tipo, fuente_nombre,
             fuente_notas, fuente_url, fuente_fecha, publicado_en,
             posible_duplicado, paciente_id, score_duplicado, texto_busqueda)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    print(
        f"Sincronización completa: {len(rows)} localizados guardados "
        f"({duplicados} posibles duplicados con pacientes locales)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sincroniza datos de Localizados Venezuela con la base de datos local."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza la resincronización aunque la tabla ya exista.",
    )
    args = parser.parse_args()
    sync(force=args.force)
