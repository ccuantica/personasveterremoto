#!/usr/bin/env python3
"""
Aplicación web para consultar el directorio de personas del sismo en Venezuela.

Combina:
- Pacientes internados (Hoja 1 del Excel local)
- Personas faltantes (hoja "Faltantes" del Excel local)
- Personas localizadas (sincronizadas desde la API pública de Localizados Venezuela)
- Personas reportadas en Venezuela Reporta (buscando / a salvo / encontrado)
- Pacientes registrados en Localiza Pacientes (sistema oficial)

Evita mostrar registros duplicados entre la lista local de pacientes y las
fuentes externas, salvo que el usuario active la opción.
"""
import re
import sqlite3
import unicodedata

from flask import Flask, jsonify, render_template, request

from import_data import DB_PATH, import_all

app = Flask(__name__)

PAGE_SIZE = 20

# Configuración de las fuentes de datos disponibles para la búsqueda unificada.
SOURCES = {
    "paciente": {
        "select": """
            SELECT
                'paciente' AS tipo,
                CAST(p.id AS TEXT) AS id,
                p.nombre_completo,
                p.cedula,
                p.edad,
                p.hospital AS ubicacion,
                p.estado AS estado_o_seccion,
                p.observaciones,
                p.fecha_registro AS fecha,
                0 AS posible_duplicado,
                NULL AS paciente_id
            FROM pacientes p
        """,
        "search_col": "p.texto_busqueda",
    },
    "faltante": {
        "select": """
            SELECT
                'faltante' AS tipo,
                CAST(f.id AS TEXT) AS id,
                f.nombre_completo,
                f.cedula,
                f.edad,
                COALESCE(NULLIF(f.procedencia, ''), f.seccion, '') AS ubicacion,
                f.seccion AS estado_o_seccion,
                f.observaciones,
                f.fecha,
                0 AS posible_duplicado,
                NULL AS paciente_id
            FROM faltantes f
        """,
        "search_col": "f.texto_busqueda",
    },
    "localizado": {
        "select": """
            SELECT
                'localizado' AS tipo,
                CAST(l.id AS TEXT) AS id,
                l.nombre_completo,
                '' AS cedula,
                '' AS edad,
                COALESCE(NULLIF(l.lugar_nombre, ''), l.direccion, '') AS ubicacion,
                l.condicion AS estado_o_seccion,
                l.observaciones,
                l.publicado_en AS fecha,
                l.posible_duplicado,
                l.paciente_id
            FROM localizados l
        """,
        "search_col": "l.texto_busqueda",
        "duplicate_col": "l.posible_duplicado",
    },
    "reporta_buscando": {
        "select": """
            SELECT
                'reporta_buscando' AS tipo,
                r.id,
                r.nombre AS nombre_completo,
                r.cedula,
                CAST(r.edad AS TEXT) AS edad,
                COALESCE(NULLIF(r.zona, ''), r.ciudad, '') AS ubicacion,
                'buscando' AS estado_o_seccion,
                COALESCE(NULLIF(r.ultima_vez, ''), r.descripcion, '') AS observaciones,
                r.created_at AS fecha,
                r.posible_duplicado,
                r.paciente_id
            FROM reporta_personas r
            WHERE r.status = 'buscando'
        """,
        "search_col": "r.texto_busqueda",
        "duplicate_col": "r.posible_duplicado",
    },
    "reporta_localizado": {
        "select": """
            SELECT
                'reporta_localizado' AS tipo,
                r.id,
                r.nombre AS nombre_completo,
                r.cedula,
                CAST(r.edad AS TEXT) AS edad,
                COALESCE(NULLIF(r.zona, ''), r.ciudad, '') AS ubicacion,
                r.status AS estado_o_seccion,
                COALESCE(NULLIF(r.ultima_vez, ''), r.descripcion, '') AS observaciones,
                r.created_at AS fecha,
                r.posible_duplicado,
                r.paciente_id
            FROM reporta_personas r
            WHERE r.status IN ('a_salvo', 'encontrado')
        """,
        "search_col": "r.texto_busqueda",
        "duplicate_col": "r.posible_duplicado",
    },
    "lp_paciente": {
        "select": """
            SELECT
                'lp_paciente' AS tipo,
                lp.id,
                lp.nombre_completo,
                '' AS cedula,
                CAST(lp.edad AS TEXT) AS edad,
                lp.hospital AS ubicacion,
                lp.condicion AS estado_o_seccion,
                COALESCE(NULLIF(lp.direccion, ''), lp.ciudad, '') AS observaciones,
                lp.fecha_ingreso AS fecha,
                lp.posible_duplicado,
                lp.paciente_id
            FROM lp_pacientes lp
        """,
        "search_col": "lp.texto_busqueda",
        "duplicate_col": "lp.posible_duplicado",
    },
    "warroom_found": {
        "select": """
            SELECT
                'warroom_found' AS tipo,
                w.id,
                w.nombre_completo,
                w.cedula,
                CAST(w.edad AS TEXT) AS edad,
                COALESCE(NULLIF(w.ubicacion_nombre, ''), w.ubicacion_direccion, '') AS ubicacion,
                w.status AS estado_o_seccion,
                COALESCE(NULLIF(w.relevant_info, ''), w.ubicacion_direccion, '') AS observaciones,
                w.created_at AS fecha,
                w.posible_duplicado,
                w.paciente_id
            FROM warroom_found w
        """,
        "search_col": "w.texto_busqueda",
        "duplicate_col": "w.posible_duplicado",
    },
}

TIPO_TO_KEY = {
    "todos": None,
    "pacientes": ["paciente", "lp_paciente"],
    "faltantes": ["faltante", "reporta_buscando"],
    "localizados": ["localizado", "reporta_localizado", "warroom_found"],
}


def get_db():
    """Devuelve una conexión SQLite configurada."""
    if not DB_PATH.exists():
        import_all()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_available_source_keys():
    """Devuelve las claves de fuentes cuyas tablas ya existen en la base de datos."""
    mapping = {
        "pacientes": "paciente",
        "faltantes": "faltante",
        "localizados": "localizado",
        "reporta_personas": "reporta_buscando",
        "lp_pacientes": "lp_paciente",
        "warroom_found": "warroom_found",
    }
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('pacientes', 'faltantes', 'localizados', 'reporta_personas', 'lp_pacientes', 'warroom_found')
            """
        )
        existing = {row[0] for row in cur.fetchall()}
    keys = []
    for table, key in mapping.items():
        if table in existing:
            # reporta_personas provee dos fuentes lógicas
            if key == "reporta_buscando":
                keys.extend(["reporta_buscando", "reporta_localizado"])
            else:
                keys.append(key)
    return keys


def normalize_query(text):
    """Limpia y tokeniza un texto de búsqueda."""
    if not text:
        return []
    text = str(text).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Extrae secuencias de letras/números
    tokens = re.findall(r"[a-z0-9]+", text)
    # Descarta tokens muy cortos para evitar coincidencias masivas
    return [t for t in tokens if len(t) >= 2]


def build_search_sql(tipo, tokens, include_duplicados=False):
    """Genera la consulta SQL y parámetros según el tipo, tokens y filtros."""
    available = get_available_source_keys()
    if tipo == "todos":
        keys = available
    else:
        requested = TIPO_TO_KEY.get(tipo, [])
        keys = [k for k in requested if k in available]

    # Si no hay fuentes disponibles, devolvemos una consulta vacía segura.
    if not keys:
        return (
            """
            SELECT 'none' AS tipo, '' AS id, '' AS nombre_completo,
                   '' AS cedula, '' AS edad, '' AS ubicacion,
                   '' AS estado_o_seccion, '' AS observaciones, '' AS fecha,
                   0 AS posible_duplicado, NULL AS paciente_id
            FROM sqlite_master WHERE 1=0
            """,
            [],
        )

    parts = []
    params = []
    for key in keys:
        source = SOURCES[key]
        sql = source["select"]
        where_parts = []
        if tokens:
            clauses = [f"({source['search_col']} LIKE ?)" for _ in tokens]
            where_parts.append(" AND ".join(clauses))
            params.extend(f"%{token}%" for token in tokens)
        # Por defecto oculta registros marcados como posibles duplicados
        if not include_duplicados and "duplicate_col" in source:
            where_parts.append(f"{source['duplicate_col']} = 0")
        if where_parts:
            if " WHERE " in sql.upper():
                sql += " AND " + " AND ".join(where_parts)
            else:
                sql += " WHERE " + " AND ".join(where_parts)
        parts.append(sql)

    union_sql = " UNION ALL ".join(parts)
    final_sql = f"SELECT * FROM ({union_sql}) ORDER BY nombre_completo COLLATE NOCASE"
    return final_sql, params


def count_results(tipo, tokens, include_duplicados=False):
    """Cuenta el total de resultados para una búsqueda."""
    sql, params = build_search_sql(tipo, tokens, include_duplicados)
    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    with get_db() as conn:
        return conn.execute(count_sql, params).fetchone()[0]


def search(tipo, tokens, page=1, include_duplicados=False):
    """Ejecuta la búsqueda paginada."""
    sql, params = build_search_sql(tipo, tokens, include_duplicados)
    offset = (page - 1) * PAGE_SIZE
    paginated_sql = f"{sql} LIMIT ? OFFSET ?"
    params_with_pagination = list(params) + [PAGE_SIZE, offset]
    with get_db() as conn:
        rows = conn.execute(paginated_sql, params_with_pagination).fetchall()
    total = count_results(tipo, tokens, include_duplicados)
    return rows, total


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    tipo = request.args.get("tipo", "todos").strip().lower()
    if tipo not in TIPO_TO_KEY:
        tipo = "todos"
    include_duplicados = request.args.get("duplicados", "0") == "1"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    tokens = normalize_query(q)
    rows, total = search(tipo, tokens, page, include_duplicados)

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1

    return render_template(
        "index.html",
        q=q,
        tipo=tipo,
        duplicados=include_duplicados,
        rows=rows,
        page=page,
        total=total,
        total_pages=total_pages,
        page_size=PAGE_SIZE,
    )


@app.route("/paciente/<int:persona_id>")
def paciente_detail(persona_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pacientes WHERE id = ?", (persona_id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    return render_template("detail.html", row=row, tipo="paciente")


@app.route("/faltante/<int:persona_id>")
def faltante_detail(persona_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM faltantes WHERE id = ?", (persona_id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    return render_template("detail.html", row=row, tipo="faltante")


@app.route("/localizado/<int:persona_id>")
def localizado_detail(persona_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM localizados WHERE id = ?", (persona_id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    return render_template("detail.html", row=row, tipo="localizado")


@app.route("/reporta/<id>")
def reporta_detail(id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM reporta_personas WHERE id = ?", (id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    tipo = "reporta_buscando" if row["status"] == "buscando" else "reporta_localizado"
    return render_template("detail.html", row=row, tipo=tipo)


@app.route("/lp_paciente/<id>")
def lp_paciente_detail(id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM lp_pacientes WHERE id = ?", (id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    return render_template("detail.html", row=row, tipo="lp_paciente")


@app.route("/warroom/<id>")
def warroom_detail(id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM warroom_found WHERE id = ?", (id,)
        ).fetchone()
    if not row:
        return render_template("404.html"), 404
    return render_template("detail.html", row=row, tipo="warroom_found")


@app.route("/api/buscar")
def api_buscar():
    q = request.args.get("q", "").strip()
    tipo = request.args.get("tipo", "todos").strip().lower()
    if tipo not in TIPO_TO_KEY:
        tipo = "todos"
    include_duplicados = request.args.get("duplicados", "0") == "1"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    tokens = normalize_query(q)
    rows, total = search(tipo, tokens, page, include_duplicados)
    return jsonify(
        {
            "q": q,
            "tipo": tipo,
            "duplicados": include_duplicados,
            "page": page,
            "total": total,
            "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1,
            "results": [dict(r) for r in rows],
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
