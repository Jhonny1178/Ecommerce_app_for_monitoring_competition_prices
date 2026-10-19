from flask import Flask, request, session, jsonify
import psycopg2.extras
from dotenv import load_dotenv
from flask_cors import CORS
import os
import hashlib
import unicodedata
import re
import csv
import io
import xml.etree.ElementTree as ET
from utils import get_db, hash_password, generate_store_prefix, login_required, admin_required
from routes.products import products_bp
from routes.auth import auth_bp
from datetime import timedelta

load_dotenv()

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "super-tajny-klucz"
)

app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_NAME="eroch_session",
    SESSION_COOKIE_PATH="/",
)

CORS(
    app,
    supports_credentials=True,
)



ALLOWED_SORTS = {
    "id", "sku", "name", "size", "color", "manufacturer",
    "category", "price_normal", "price_special",
    "store", "availability", "date_of_download"
}


@app.route("/api/stats")
@login_required
def api_stats():
    prefix = session.get("store_prefix", "default")

    products_table = f"{prefix}_products"
    competitors_table = f"{prefix}_competitors"
    matches_table = f"{prefix}_matches"

    def ident(name):
        return '"' + str(name).replace('"', '""') + '"'

    def table_exists(cur, table_name):
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = %s
            ) AS exists
        """, (table_name,))
        return bool(cur.fetchone()["exists"])

    def get_columns(cur, table_name):
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
        """, (table_name,))
        return {row["column_name"] for row in cur.fetchall()}

    def to_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        product_summary = {
            "total": 0,
            "avg_client_price": None,
            "total_categories": 0,
            "total_manufacturers": 0,
        }

        competitor_summary = {
            "total_competitor_products": 0,
            "avg_competitor_price": None,
            "total_stores": 0,
        }

        match_summary = {
            "matched_products": 0,
            "total_competitor_matches": 0,
            "our_price_lower_count": 0,
            "our_price_higher_count": 0,
            "same_price_count": 0,
            "avg_difference_competitor_minus_ours": None,
            "max_saving_when_ours_cheaper": None,
            "max_loss_when_ours_expensive": None,
        }

        by_category = []
        by_store = []
        by_availability = []

        if table_exists(cur, products_table):
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(price_normal)::numeric, 2) AS avg_client_price,
                    COUNT(DISTINCT category) AS total_categories,
                    COUNT(DISTINCT manufacturer) AS total_manufacturers
                FROM {ident(products_table)}
            """)
            product_summary = dict(cur.fetchone() or product_summary)

            cur.execute(f"""
                SELECT
                    category,
                    COUNT(*) AS count,
                    ROUND(AVG(price_normal)::numeric, 2) AS avg_price
                FROM {ident(products_table)}
                GROUP BY category
                ORDER BY count DESC
                LIMIT 8
            """)
            by_category = cur.fetchall()

            cur.execute(f"""
                SELECT
                    availability,
                    COUNT(*) AS count
                FROM {ident(products_table)}
                GROUP BY availability
                ORDER BY count DESC
            """)
            by_availability = cur.fetchall()

        if table_exists(cur, competitors_table):
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total_competitor_products,
                    ROUND(AVG(price_normal)::numeric, 2) AS avg_competitor_price,
                    COUNT(DISTINCT store) AS total_stores
                FROM {ident(competitors_table)}
            """)
            competitor_summary = dict(cur.fetchone() or competitor_summary)

            cur.execute(f"""
                SELECT
                    store,
                    COUNT(*) AS count
                FROM {ident(competitors_table)}
                GROUP BY store
                ORDER BY count DESC
            """)
            by_store = cur.fetchall()

        if table_exists(cur, matches_table):
            match_columns = get_columns(cur, matches_table)

            competitor_price_columns = [
                col for col in match_columns
                if col.endswith("_price")
                and not col.startswith("client_")
                and not col.startswith("product_")
                and not col.startswith("our_")
            ]

            client_price_col = None
            if "client_price" in match_columns:
                client_price_col = "client_price"
            elif "product_price" in match_columns:
                client_price_col = "product_price"
            elif "our_price" in match_columns:
                client_price_col = "our_price"

            if client_price_col and competitor_price_columns:
                selected_cols = [client_price_col] + competitor_price_columns

                cur.execute(f"""
                    SELECT {", ".join(ident(col) for col in selected_cols)}
                    FROM {ident(matches_table)}
                """)

                rows = cur.fetchall()

                matched_products = 0
                total_competitor_matches = 0
                our_price_lower_count = 0
                our_price_higher_count = 0
                same_price_count = 0
                diffs = []
                savings = []
                losses = []

                for row in rows:
                    row = dict(row)
                    client_price = to_float(row.get(client_price_col))
                    row_has_match = False

                    for col in competitor_price_columns:
                        competitor_price = to_float(row.get(col))

                        if client_price is None or competitor_price is None:
                            continue

                        row_has_match = True
                        total_competitor_matches += 1

                        diff = competitor_price - client_price
                        diffs.append(diff)

                        if diff > 0:
                            # konkurencja drożej, czyli my jesteśmy tańsi
                            our_price_lower_count += 1
                            savings.append(diff)
                        elif diff < 0:
                            # konkurencja taniej, czyli my jesteśmy drożsi
                            our_price_higher_count += 1
                            losses.append(abs(diff))
                        else:
                            same_price_count += 1

                    if row_has_match:
                        matched_products += 1

                match_summary = {
                    "matched_products": matched_products,
                    "total_competitor_matches": total_competitor_matches,
                    "our_price_lower_count": our_price_lower_count,
                    "our_price_higher_count": our_price_higher_count,
                    "same_price_count": same_price_count,
                    "avg_difference_competitor_minus_ours": round(sum(diffs) / len(diffs), 2) if diffs else None,
                    "max_saving_when_ours_cheaper": round(max(savings), 2) if savings else None,
                    "max_loss_when_ours_expensive": round(max(losses), 2) if losses else None,
                }

        summary = {
            "total": match_summary["matched_products"],
            "avg_price_normal": competitor_summary.get("avg_competitor_price"),
            "avg_price_special": None,

            "matched_products": match_summary["matched_products"],
            "total_competitor_matches": match_summary["total_competitor_matches"],
            "our_price_lower_count": match_summary["our_price_lower_count"],
            "our_price_higher_count": match_summary["our_price_higher_count"],
            "same_price_count": match_summary["same_price_count"],
            "avg_difference_competitor_minus_ours": match_summary["avg_difference_competitor_minus_ours"],
            "max_saving_when_ours_cheaper": match_summary["max_saving_when_ours_cheaper"],
            "max_loss_when_ours_expensive": match_summary["max_loss_when_ours_expensive"],

            "total_client_products": product_summary.get("total") or 0,
            "avg_client_price": product_summary.get("avg_client_price"),
            "total_categories": product_summary.get("total_categories") or 0,
            "total_manufacturers": product_summary.get("total_manufacturers") or 0,

            "total_competitor_products": competitor_summary.get("total_competitor_products") or 0,
            "avg_competitor_price": competitor_summary.get("avg_competitor_price"),
            "total_stores": competitor_summary.get("total_stores") or 0,
        }

        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "summary": summary,
            "by_category": [dict(r) for r in by_category],
            "by_store": [dict(r) for r in by_store],
            "by_availability": [dict(r) for r in by_availability],
            "report_rows": match_summary["matched_products"],
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/categories")
@login_required
def api_categories():
    try:
        conn = get_db(); cur = conn.cursor()
        table_products = f"{session.get('store_prefix', 'default')}_products"
        cur.execute(f"SELECT DISTINCT category FROM {table_products} WHERE category IS NOT NULL ORDER BY category")
        cats = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"ok": True, "data": cats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stores")
@login_required
def api_stores():
    try:
        conn = get_db(); cur = conn.cursor()
        table_products = f"{session.get('store_prefix', 'default')}_products"
        cur.execute(f"SELECT DISTINCT store FROM {table_products} WHERE store IS NOT NULL ORDER BY store")
        data = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/manufacturers")
@login_required
def api_manufacturers():
    try:
        conn = get_db(); cur = conn.cursor()
        table_products = f"{session.get('store_prefix', 'default')}_products"
        cur.execute(f"SELECT DISTINCT manufacturer FROM {table_products} WHERE manufacturer IS NOT NULL ORDER BY manufacturer")
        data = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/comparison")
@login_required
def api_comparison():
    shop     = request.args.get("shop", "")
    category = request.args.get("category", "")
    diff     = request.args.get("diff", "")

    prefix = session.get('store_prefix', 'default')
    competitor_table  = f"{prefix}_competitors"
    COMPETITOR_TABLES = {competitor_table: "Rynek (Konkurencja)"}

    if shop and shop not in COMPETITOR_TABLES:
        return jsonify({"ok": False, "error": "Unknown shop"}), 400

    rows = []
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        table_products = f"{prefix}_products"
        table_mappings = f"{prefix}_product_mappings"

        cur.execute("SELECT to_regclass(%s) AS table_ref", (f"public.{table_mappings}",))
        if not cur.fetchone()['table_ref']:
            cur.close(); conn.close()
            return jsonify({"ok": True, "data": [], "shops": COMPETITOR_TABLES})

        for table, label in COMPETITOR_TABLES.items():
            if shop and table != shop:
                continue
            cat_filter = "AND p.category = %s" if category else ""
            params = [table]
            if category:
                params.append(category)
            cur.execute(f"""
                SELECT p.id AS our_id, p.sku, p.name AS our_name, p.category,
                       p.price_normal AS our_price_normal, p.price_special AS our_price_special,
                       p.availability AS our_availability,
                       c.id AS comp_id, c.name AS comp_name,
                       c.price_normal AS comp_price_normal, c.price_special AS comp_price_special,
                       c.availability AS comp_availability, c.url AS comp_url,
                       '{label}' AS shop_label, '{table}' AS shop_table,
                       COALESCE(p.price_special, p.price_normal) AS our_effective,
                       COALESCE(c.price_special, c.price_normal) AS comp_effective,
                       ROUND((COALESCE(c.price_special,c.price_normal)
                              - COALESCE(p.price_special,p.price_normal))::numeric, 2) AS diff_abs,
                       ROUND(((COALESCE(c.price_special,c.price_normal)
                               - COALESCE(p.price_special,p.price_normal))
                              / NULLIF(COALESCE(c.price_special,c.price_normal),0)*100)::numeric, 1) AS diff_pct
                FROM {table_mappings} m
                JOIN {table_products} p ON p.id = m.our_product_id
                JOIN {table}          c ON c.id = m.competitor_id
                WHERE m.competitor_table = %s {cat_filter}
                ORDER BY diff_abs DESC
            """, params)
            rows += [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    if diff == "cheaper":
        rows = [r for r in rows if r["diff_abs"] and float(r["diff_abs"]) > 0]
    elif diff == "expensive":
        rows = [r for r in rows if r["diff_abs"] and float(r["diff_abs"]) < 0]
    elif diff == "equal":
        rows = [r for r in rows if not r["diff_abs"] or float(r["diff_abs"]) == 0]

    return jsonify({"ok": True, "data": rows, "shops": COMPETITOR_TABLES})


@app.route("/api/report")
@login_required
def api_report():
    """
    Zwraca raport wygenerowany przez pipeline dla zalogowanego klienta.
    Tabela raportu to: {store_prefix}_report
    Widoczna TYLKO dla właściciela (izolacja przez store_prefix z sesji).
    """
    prefix     = session.get("store_prefix", "default")
    table_name = f"{prefix}_report"

    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            );
        """, (table_name,))
        table_exists = cur.fetchone()['exists']

        if not table_exists:
            cur.close(); conn.close()
            return jsonify({
                "ok": False,
                "error": "Raport jest w trakcie generowania lub jeszcze nie powstał. Wróć za chwilę."
            }), 404

        cur.execute(f"SELECT * FROM {table_name}")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/comparison/summary")
@login_required
def api_comparison_summary():
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        prefix           = session.get('store_prefix', 'default')
        table_products   = f"{prefix}_products"
        table_mappings   = f"{prefix}_product_mappings"
        competitor_table = f"{prefix}_competitors"
        COMPETITOR_TABLES = {competitor_table: "Rynek (Konkurencja)"}

        cur.execute("SELECT to_regclass(%s) AS table_ref", (f"public.{table_mappings}",))
        if not cur.fetchone()['table_ref']:
            cur.close(); conn.close()
            return jsonify({"ok": True, "summary": []})

        summary = []
        for table, label in COMPETITOR_TABLES.items():
            cur.execute(f"""
                SELECT '{label}' AS shop_label,
                       COUNT(*) AS total_mapped,
                       SUM(CASE WHEN COALESCE(c.price_special,c.price_normal) > COALESCE(p.price_special,p.price_normal) THEN 1 ELSE 0 END) AS we_cheaper,
                       SUM(CASE WHEN COALESCE(c.price_special,c.price_normal) < COALESCE(p.price_special,p.price_normal) THEN 1 ELSE 0 END) AS we_expensive,
                       SUM(CASE WHEN COALESCE(c.price_special,c.price_normal) = COALESCE(p.price_special,p.price_normal) THEN 1 ELSE 0 END) AS equal,
                       ROUND(AVG(COALESCE(c.price_special,c.price_normal)
                                 - COALESCE(p.price_special,p.price_normal))::numeric, 2) AS avg_diff_abs
                FROM {table_mappings} m
                JOIN {table_products} p ON p.id = m.our_product_id
                JOIN {table}          c ON c.id = m.competitor_id
                WHERE m.competitor_table = %s
            """, (table,))
            row = cur.fetchone()
            if row:
                row = dict(row)
                cur.execute(f"""
                    SELECT p.category, '{label}' AS shop_label, COUNT(*) AS count,
                           ROUND(AVG(COALESCE(c.price_special,c.price_normal)
                                     - COALESCE(p.price_special,p.price_normal))::numeric, 2) AS avg_diff
                    FROM {table_mappings} m
                    JOIN {table_products} p ON p.id = m.our_product_id
                    JOIN {table}          c ON c.id = m.competitor_id
                    WHERE m.competitor_table = %s
                    GROUP BY p.category ORDER BY avg_diff DESC
                """, (table,))
                row["by_category"] = [dict(r) for r in cur.fetchall()]
                summary.append(row)

        cur.close(); conn.close()
        return jsonify({"ok": True, "data": summary})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ==========================================
# ADMIN — UPLOAD I ZARZĄDZANIE PRODUKTAMI
# ==========================================

PRODUCT_FIELDS = [
    "sku", "name", "size", "color", "manufacturer",
    "category", "price_normal", "price_special",
    "store", "availability", "url", "image", "description"
]


@app.route("/api/admin/product", methods=["POST"])
@admin_required
def admin_add_product():
    data = request.get_json()
    cols, vals = [], []
    for f in PRODUCT_FIELDS:
        if f in data and data[f] not in (None, ""):
            cols.append(f)
            vals.append(float(data[f]) if f in ("price_normal", "price_special") else data[f])
    if not cols:
        return jsonify({"ok": False, "error": "Brak danych"}), 400

    table_products = f"{session.get('store_prefix', 'default')}_products"
    sql = (f"INSERT INTO {table_products} ({', '.join(cols)}) "
           f"VALUES ({', '.join(['%s']*len(cols))}) RETURNING id")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(sql, vals)
        new_id = cur.fetchone()[0]
        cur.close(); conn.close()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/admin/product/<int:product_id>", methods=["PUT"])
@admin_required
def admin_update_product(product_id):
    data = request.get_json()
    fields, params = [], []
    for f in PRODUCT_FIELDS:
        if f in data:
            fields.append(f"{f} = %s")
            if f in ("price_normal", "price_special"):
                params.append(float(data[f]) if data[f] not in (None, "") else None)
            else:
                params.append(data[f] if data[f] != "" else None)
    if not fields:
        return jsonify({"ok": False, "error": "Brak pól do aktualizacji"}), 400
    params.append(product_id)

    table_products = f"{session.get('store_prefix', 'default')}_products"
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(f"UPDATE {table_products} SET {', '.join(fields)} WHERE id = %s", params)
        cur.close(); conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/admin/product/<int:product_id>", methods=["DELETE"])
@admin_required
def admin_delete_product(product_id):
    table_products = f"{session.get('store_prefix', 'default')}_products"
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(f"DELETE FROM {table_products} WHERE id = %s", (product_id,))
        cur.close(); conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _coerce_row(raw: dict) -> dict:
    out = {}
    for f in PRODUCT_FIELDS:
        v = raw.get(f, "")
        if v in (None, ""):
            continue
        if f in ("price_normal", "price_special"):
            try:
                out[f] = float(str(v).replace(",", "."))
            except ValueError:
                pass
        else:
            out[f] = str(v).strip()
    return out


def _parse_csv(file_bytes: bytes) -> list[dict]:
    text   = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [_coerce_row(row) for row in reader]


def _parse_xml(file_bytes: bytes) -> list[dict]:
    root  = ET.fromstring(file_bytes)
    rows  = []
    items = root.findall("product") or list(root)
    for item in items:
        raw = {child.tag: child.text for child in item}
        raw.update(item.attrib)
        rows.append(_coerce_row(raw))
    return rows


@app.route("/api/admin/upload/preview", methods=["POST"])
@admin_required
def admin_upload_preview():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Brak pliku"}), 400
    f    = request.files["file"]
    data = f.read()
    fname = f.filename.lower()
    try:
        if fname.endswith(".csv"):
            rows = _parse_csv(data)
        elif fname.endswith(".xml"):
            rows = _parse_xml(data)
        else:
            return jsonify({"ok": False, "error": "Obsługiwane formaty: .csv, .xml"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Błąd parsowania: {e}"}), 400

    return jsonify({"ok": True, "rows": rows, "count": len(rows)})


@app.route("/api/admin/upload/confirm", methods=["POST"])
@admin_required
def admin_upload_confirm():
    payload = request.get_json()
    rows    = payload.get("rows", [])
    mode    = payload.get("mode", "insert")

    if not rows:
        return jsonify({"ok": False, "error": "Brak wierszy do wstawienia"}), 400

    inserted = updated = skipped = 0
    errors = []
    table_products = f"{session.get('store_prefix', 'default')}_products"

    try:
        conn = get_db()
        cur  = conn.cursor()

        for i, row in enumerate(rows):
            if not row: continue
            cols = list(row.keys())
            vals = list(row.values())
            if not cols: continue

            try:
                if mode == "upsert" and "sku" in row and row["sku"]:
                    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "sku")
                    sql = (
                        f"INSERT INTO {table_products} ({', '.join(cols)}) "
                        f"VALUES ({', '.join(['%s']*len(cols))}) "
                        f"ON CONFLICT (sku) DO UPDATE SET {set_clause} "
                        f"RETURNING (xmax = 0) AS inserted"
                    )
                    cur.execute(sql, vals)
                    was_inserted = cur.fetchone()[0]
                    if was_inserted: inserted += 1
                    else:            updated  += 1
                else:
                    sql = (
                        f"INSERT INTO {table_products} ({', '.join(cols)}) "
                        f"VALUES ({', '.join(['%s']*len(cols))}) "
                        f"ON CONFLICT DO NOTHING RETURNING id"
                    )
                    cur.execute(sql, vals)
                    if cur.fetchone(): inserted += 1
                    else:              skipped  += 1
            except Exception as e:
                errors.append({"row": i + 1, "error": str(e)})

        cur.close(); conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    })

app.register_blueprint(products_bp)
app.register_blueprint(auth_bp)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6767, debug=False)