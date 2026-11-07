"""API raportów zmian po aktualizacjach danych (oglądanie od pakietu Pro)."""
from flask import Blueprint, jsonify, request
import psycopg2.extras
from psycopg2 import sql

from utils import get_db, login_required
from routes.products import (
    _get_current_user_subscription_plan,
    _get_store_prefix,
    _has_price_history_access,
    _table_exists,
)

changes_bp = Blueprint("changes", __name__)

CHANGE_TYPES = ("price", "new", "removed")


def _locked(subscription_plan):
    return jsonify({
        "ok": False,
        "locked": True,
        "subscription_plan": subscription_plan,
        "required_plans": ["Pro", "Premium"],
        "error": "Raporty zmian są dostępne od pakietu Pro.",
    }), 403


def _open(cur):
    """Prefiks sklepu albo odpowiedź z błędem lub blokadą pakietu."""
    prefix = _get_store_prefix()
    if not prefix:
        return None, (jsonify({"ok": False, "error": "Brak zdefiniowanego sklepu w sesji użytkownika"}), 401)

    subscription_plan = _get_current_user_subscription_plan(cur)
    if not _has_price_history_access(subscription_plan):
        return None, _locked(subscription_plan)

    return prefix, None


@changes_bp.route("/api/change-reports")
@login_required
def api_change_reports():
    limit = min(max(request.args.get("limit", 100, type=int) or 100, 1), 500)

    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        prefix, error = _open(cur)
        if error:
            return error

        reports_table = f"{prefix}_change_reports"
        if not _table_exists(cur, reports_table):
            return jsonify({"ok": True, "data": []})

        counts = sql.SQL(", ").join(
            sql.SQL(
                "(SELECT COUNT(*) FROM jsonb_array_elements(r.items) item "
                "WHERE item->>'type' = {}) AS {}"
            ).format(sql.Literal(change_type), sql.Identifier(f"{change_type}_count"))
            for change_type in CHANGE_TYPES
        )
        cur.execute(
            sql.SQL("""
                SELECT r.id, r.created_at, r.unchecked_stores, {counts}
                FROM {table} r
                WHERE NOT r.baseline
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT %s
            """).format(counts=counts, table=sql.Identifier(reports_table)),
            (limit,),
        )

        reports = [
            {
                "id": row["id"],
                "created_at": row["created_at"].isoformat(),
                "unchecked_stores": row["unchecked_stores"] or [],
                "price_changes": row["price_count"],
                "new_offers": row["new_count"],
                "removed_offers": row["removed_count"],
            }
            for row in cur.fetchall()
        ]
        return jsonify({"ok": True, "data": reports})
    finally:
        conn.close()


@changes_bp.route("/api/change-reports/<int:report_id>")
@login_required
def api_change_report(report_id: int):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        prefix, error = _open(cur)
        if error:
            return error

        reports_table = f"{prefix}_change_reports"
        products_table = f"{prefix}_products"

        report = None
        if _table_exists(cur, reports_table):
            cur.execute(
                sql.SQL("""
                    SELECT id, created_at, unchecked_stores, items
                    FROM {} WHERE id = %s AND NOT baseline
                """).format(sql.Identifier(reports_table)),
                (report_id,),
            )
            report = cur.fetchone()

        if not report:
            return jsonify({"ok": False, "error": "Nie znaleziono raportu"}), 404

        items = report["items"] or []

        # id produktu do linku (produkt mógł zniknąć z feedu).
        product_ids = {}
        skus = sorted({item.get("client_sku") for item in items if item.get("client_sku")})
        if skus and _table_exists(cur, products_table):
            cur.execute(
                sql.SQL("SELECT sku, MIN(id) AS id FROM {} WHERE sku = ANY(%s) GROUP BY sku").format(
                    sql.Identifier(products_table)
                ),
                (skus,),
            )
            product_ids = {row["sku"]: row["id"] for row in cur.fetchall()}

        for item in items:
            item["product_id"] = product_ids.get(item.get("client_sku"))

        items.sort(key=lambda item: (
            CHANGE_TYPES.index(item.get("type")) if item.get("type") in CHANGE_TYPES else len(CHANGE_TYPES),
            item.get("client_name") or "",
            item.get("size") or "",
            item.get("store") or "",
        ))

        return jsonify({
            "ok": True,
            "data": {
                "id": report["id"],
                "created_at": report["created_at"].isoformat(),
                "unchecked_stores": report["unchecked_stores"] or [],
                "items": items,
            },
        })
    finally:
        conn.close()
