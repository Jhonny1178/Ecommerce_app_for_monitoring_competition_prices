# Statystyki wszystkich dopasowań klienta dla Dashboardu: mediany zamiast średnich, bez podejrzanych dopasowań.
import traceback

import psycopg2.extras
from flask import Blueprint, jsonify
from psycopg2 import sql

from routes.products import (
    HIGH_RISK_DIFF_PCT,
    _CLIENT_PRICE_SQL,
    _competitor_price_columns,
    _competitor_price_sql,
    _get_columns,
    _get_store_prefix,
    _table_exists,
)
from utils import get_db, login_required

dashboard_bp = Blueprint("dashboard", __name__)

TOP_ROWS = 10
TOP_GAPS = 5
# Tolerancja przy porównywaniu cen zapisanych jako liczby zmiennoprzecinkowe.
SAME_PRICE_EPS = 0.005


def _empty_dashboard(total_products: int = 0) -> dict:
    return {
        "ok": True,
        "risk_threshold_pct": HIGH_RISK_DIFF_PCT,
        "summary": {
            "total_products": total_products,
            "matched_products": 0,
            "stores": 0,
            "offers": 0,
            "median_diff": None,
            "risky_offers": 0,
            "risky_products": 0,
        },
        "position": {"you_cheapest": 0, "competitor_cheaper": 0, "same_price": 0},
        "stores": [],
        "categories": [],
        "brands": [],
        "biggest_gaps": {"competitor_cheaper": [], "you_cheaper": []},
    }


def _store_label(price_column: str) -> str:
    return price_column[:-len("_price")].replace("_", " ").title()


def _pairs_cte(prefix: str, match_columns: set[str], products_table: str, matches_table: str):
    """CTE z parami (wariant klienta, oferta sklepu); różnica liczona tak samo jak w filtrze listy produktów."""
    # Kolumna własnego sklepu klienta (np. nasz_klient_price) to nie konkurencja.
    columns = [
        (price_column, special_column)
        for price_column, special_column in _competitor_price_columns(match_columns)
        if price_column[:-len("_price")] != prefix
    ]
    if "client_sku" not in match_columns or not columns:
        return None

    offers = sql.SQL(", ").join(
        sql.SQL("({}, {})").format(sql.Literal(_store_label(price_column)), _competitor_price_sql(price_column, special_column))
        for price_column, special_column in columns
    )

    return sql.SQL("""
        WITH pairs AS (
            SELECT p.id, p.name, p.sku, p.category, p.manufacturer,
                   {client} AS ours,
                   c.store,
                   c.price AS theirs,
                   c.price - {client} AS diff,
                   (c.price - {client}) * 100.0 / NULLIF({client}, 0) AS diff_pct
            FROM {products} p
            JOIN {matches} m ON m.client_sku = p.sku
            CROSS JOIN LATERAL (VALUES {offers}) AS c(store, price)
            WHERE c.price IS NOT NULL AND {client} > 0
        ),
        clean AS (
            SELECT * FROM pairs WHERE ABS(diff_pct) <= %(risk)s
        )
    """).format(
        client=_CLIENT_PRICE_SQL,
        products=sql.Identifier(products_table),
        matches=sql.Identifier(matches_table),
        offers=offers,
    )


def _money(value):
    return round(float(value), 2) if value is not None else None


def _grouped_rows(cur, cte, column: str, empty_label: str, params: dict) -> list[dict]:
    cur.execute(cte + sql.SQL("""
        SELECT COALESCE(NULLIF(BTRIM({column}), ''), %(empty)s) AS name,
               COUNT(DISTINCT id) AS products,
               COUNT(DISTINCT id) FILTER (WHERE diff < -%(eps)s) AS competitor_cheaper,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY diff) AS median_diff
        FROM clean
        GROUP BY 1
        ORDER BY competitor_cheaper DESC, products DESC, name
        LIMIT %(top)s
    """).format(column=sql.Identifier(column)), {**params, "empty": empty_label})
    return [
        {
            "name": row["name"],
            "products": row["products"],
            "competitor_cheaper": row["competitor_cheaper"],
            "median_diff": _money(row["median_diff"]),
        }
        for row in cur.fetchall()
    ]


def _gaps(cur, cte, condition: str, order: str, params: dict) -> list[dict]:
    cur.execute(cte + sql.SQL("""
        SELECT id AS product_id, name, sku, store, ours, theirs, diff
        FROM clean
        WHERE {condition}
        ORDER BY diff {order}, id
        LIMIT %(gaps)s
    """).format(condition=sql.SQL(condition), order=sql.SQL(order)), params)
    return [
        {
            "product_id": row["product_id"],
            "name": row["name"],
            "sku": row["sku"],
            "store": row["store"],
            "ours": _money(row["ours"]),
            "theirs": _money(row["theirs"]),
            "diff": _money(row["diff"]),
        }
        for row in cur.fetchall()
    ]


@dashboard_bp.route("/api/dashboard")
@login_required
def api_dashboard():
    prefix = _get_store_prefix()
    if not prefix:
        return jsonify({"ok": False, "error": "Brak zdefiniowanego sklepu w sesji"}), 401

    products_table = f"{prefix}_products"
    matches_table = f"{prefix}_matches"

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not _table_exists(cur, products_table):
            return jsonify(_empty_dashboard())

        cur.execute(sql.SQL("SELECT COUNT(*) AS total FROM {}").format(sql.Identifier(products_table)))
        total_products = cur.fetchone()["total"]

        if not _table_exists(cur, matches_table):
            return jsonify(_empty_dashboard(total_products))

        cte = _pairs_cte(prefix, _get_columns(cur, matches_table), products_table, matches_table)
        if cte is None:
            return jsonify(_empty_dashboard(total_products))

        params = {"risk": HIGH_RISK_DIFF_PCT, "eps": SAME_PRICE_EPS, "top": TOP_ROWS, "gaps": TOP_GAPS}
        result = _empty_dashboard(total_products)

        cur.execute(cte + sql.SQL("""
            SELECT COUNT(DISTINCT id) AS matched_products,
                   COUNT(DISTINCT store) AS stores,
                   COUNT(*) FILTER (WHERE ABS(diff_pct) <= %(risk)s) AS offers,
                   COUNT(*) FILTER (WHERE ABS(diff_pct) > %(risk)s) AS risky_offers,
                   COUNT(DISTINCT id) FILTER (WHERE ABS(diff_pct) > %(risk)s) AS risky_products,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY diff) FILTER (WHERE ABS(diff_pct) <= %(risk)s) AS median_diff
            FROM pairs
        """), params)
        summary = cur.fetchone()
        result["summary"].update({
            "matched_products": summary["matched_products"],
            "stores": summary["stores"],
            "offers": summary["offers"],
            "risky_offers": summary["risky_offers"],
            "risky_products": summary["risky_products"],
            "median_diff": _money(summary["median_diff"]),
        })

        # Pozycja wariantu: cena klienta vs najtańsza (niepodejrzana) oferta konkurencji.
        cur.execute(cte + sql.SQL("""
            SELECT COUNT(*) FILTER (WHERE ours < cheapest - %(eps)s) AS you_cheapest,
                   COUNT(*) FILTER (WHERE ours > cheapest + %(eps)s) AS competitor_cheaper,
                   COUNT(*) FILTER (WHERE ABS(ours - cheapest) <= %(eps)s) AS same_price
            FROM (SELECT id, MIN(ours) AS ours, MIN(theirs) AS cheapest FROM clean GROUP BY id) s
        """), params)
        result["position"] = dict(cur.fetchone())

        cur.execute(cte + sql.SQL("""
            SELECT store,
                   COUNT(*) AS offers,
                   COUNT(*) FILTER (WHERE diff < -%(eps)s) AS cheaper,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY diff) AS median_diff
            FROM clean
            GROUP BY store
            ORDER BY offers DESC, store
        """), params)
        result["stores"] = [
            {"store": row["store"], "offers": row["offers"], "cheaper": row["cheaper"], "median_diff": _money(row["median_diff"])}
            for row in cur.fetchall()
        ]

        result["categories"] = _grouped_rows(cur, cte, "category", "Bez kategorii", params)
        result["brands"] = _grouped_rows(cur, cte, "manufacturer", "Bez marki", params)
        result["biggest_gaps"] = {
            "competitor_cheaper": _gaps(cur, cte, "diff < -%(eps)s", "ASC", params),
            "you_cheaper": _gaps(cur, cte, "diff > %(eps)s", "DESC", params),
        }

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
