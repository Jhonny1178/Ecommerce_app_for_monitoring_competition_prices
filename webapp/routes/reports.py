# Zgłoszenia błędów przez klienta z widoku produktu (porównanie, rekomendacja, dane produktu) zapisywane w error_logs dla admina.
import traceback

import psycopg2.extras
from flask import Blueprint, jsonify, request, session
from psycopg2 import sql

from extensions import limiter, user_rate_key
from routes.products import _get_store_prefix, _table_exists
from utils import get_db, login_required

reports_bp = Blueprint("reports", __name__)

MAX_COMMENT_LENGTH = 1000
MAX_FIELD_LENGTH = 300

# Tematy i gotowe powody; frontend wysyła klucze, tutaj są teksty widoczne dla admina.
REPORT_TOPICS = {
    "comparison": {
        "label": "Porównanie z konkurencją",
        "reasons": {
            "wrong_match": "To inny produkt (złe dopasowanie)",
            "wrong_variant": "Inny rozmiar lub wariant",
            "wrong_competitor_price": "Nieprawidłowa cena konkurencji",
            "offer_unavailable": "Oferta nieaktualna lub niedostępna",
            "wrong_difference": "Źle policzona różnica ceny",
        },
    },
    "recommendation": {
        "label": "Rekomendacja ceny",
        "reasons": {
            "price_nonsense": "Sugerowana cena nie ma sensu",
            "reason_mismatch": "Uzasadnienie nie zgadza się z ceną",
            "reason_unclear": "Uzasadnienie jest niezrozumiałe",
            "not_generated": "Rekomendacja się nie generuje",
        },
    },
    "product": {
        "label": "Dane mojego produktu",
        "reasons": {
            "wrong_own_price": "Zła cena mojego produktu",
            "wrong_name": "Zła nazwa lub opis",
            "wrong_category": "Zła kategoria lub marka",
            "wrong_image": "Złe lub brakujące zdjęcie",
        },
    },
    "other": {"label": "Inne", "reasons": {}},
}


def _short(value, limit=MAX_FIELD_LENGTH):
    text = str(value or "").strip()
    return text[:limit]


def _price(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _offer_line(offer: dict) -> str | None:
    store = _short(offer.get("store"))
    if not store:
        return None

    details = []
    if offer.get("name"):
        details.append(_short(offer["name"]))
    price = _price(offer.get("price"))
    if price is not None:
        details.append(f"{price:.2f} zł")
    if offer.get("sku"):
        details.append(f"SKU: {_short(offer['sku'])}")
    if offer.get("url"):
        details.append(_short(offer["url"], 500))
    return f"{store} — {', '.join(details)}" if details else store


def build_report_message(topic: str, product: dict, reasons: list[str], comment: str,
                         offer: dict | None = None, recommendation: dict | None = None) -> str:
    """Czytelna treść zgłoszenia dla admina (okno logu w panelu)."""
    info = REPORT_TOPICS[topic]
    lines = [
        f"Zgłoszenie klienta: {info['label']}",
        f"Produkt: {product.get('name') or '-'} (SKU: {product.get('sku') or '-'}, id: {product.get('id')})",
    ]

    if offer:
        offer_text = _offer_line(offer)
        if offer_text:
            lines.append(f"Oferta: {offer_text}")

    if recommendation:
        price = _price(recommendation.get("price"))
        if price is not None:
            lines.append(f"Sugerowana cena: {price:.2f} zł")
        if recommendation.get("reason"):
            lines.append(f"Uzasadnienie rekomendacji: {_short(recommendation['reason'], MAX_COMMENT_LENGTH)}")

    if reasons:
        lines.append("Powody: " + "; ".join(info["reasons"][key] for key in reasons))
    if comment:
        lines.append(f"Opis klienta: {comment}")

    return "\n".join(lines)


@reports_bp.route("/api/products/<int:product_id>/report", methods=["POST"])
@login_required
@limiter.limit("20 per hour", key_func=user_rate_key)
def api_product_report(product_id: int):
    data = request.get_json(silent=True) or {}

    topic = data.get("topic")
    if topic not in REPORT_TOPICS:
        return jsonify({"ok": False, "error": "Wybierz, czego dotyczy zgłoszenie."}), 400

    raw_reasons = data.get("reasons") or []
    if not isinstance(raw_reasons, list):
        return jsonify({"ok": False, "error": "Nieprawidłowa lista powodów."}), 400
    allowed = REPORT_TOPICS[topic]["reasons"]
    reasons = list(dict.fromkeys(str(key) for key in raw_reasons))
    if any(key not in allowed for key in reasons):
        return jsonify({"ok": False, "error": "Nieznany powód zgłoszenia."}), 400

    comment = str(data.get("comment") or "").strip()
    if len(comment) > MAX_COMMENT_LENGTH:
        return jsonify({"ok": False, "error": f"Opis może mieć maksymalnie {MAX_COMMENT_LENGTH} znaków."}), 400
    if not reasons and not comment:
        return jsonify({"ok": False, "error": "Wybierz powód albo opisz problem."}), 400

    offer = data.get("offer") if topic == "comparison" and isinstance(data.get("offer"), dict) else None
    recommendation = (
        data.get("recommendation")
        if topic == "recommendation" and isinstance(data.get("recommendation"), dict)
        else None
    )

    prefix = _get_store_prefix()
    if not prefix:
        return jsonify({"ok": False, "error": "Brak zdefiniowanego sklepu w sesji"}), 401

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        products_table = f"{prefix}_products"

        # Nazwę i SKU bierzemy z bazy, nie od klienta.
        product = None
        if _table_exists(cur, products_table):
            cur.execute(
                sql.SQL("SELECT id, name, sku FROM {} WHERE id = %s").format(sql.Identifier(products_table)),
                (product_id,),
            )
            product = cur.fetchone()
        if not product:
            return jsonify({"ok": False, "error": "Produkt nie został znaleziony"}), 404

        message = build_report_message(topic, dict(product), reasons, comment, offer, recommendation)

        cur.execute("""
            INSERT INTO error_logs (user_id, client_id, category, error_code, message, error_type, is_reviewed)
            VALUES (%s, %s, 'Klient', %s, %s, 'Zgłoszony', FALSE)
        """, (session.get("user_id"), session.get("client_id"), REPORT_TOPICS[topic]["label"], message))

        return jsonify({"ok": True})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
