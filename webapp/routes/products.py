from flask import Blueprint, jsonify, session, request, Response
import math
import psycopg2
import psycopg2.extras
from psycopg2 import sql
from urllib.parse import quote, unquote, urljoin, urlparse
import requests
import ipaddress
import socket

from utils import get_db, login_required
from extensions import limiter, user_rate_key

products_bp = Blueprint("products", __name__)

IMAGE_PROXY_MAX_BYTES = 10 * 1024 * 1024
IMAGE_PROXY_MAX_REDIRECTS = 3
IMAGE_PROXY_ALLOWED_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/webp", "image/avif", "image/bmp", "image/x-icon",
}


def _get_store_prefix():
    prefix = session.get("store_prefix")
    if prefix:
        return prefix

    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT c.store_prefix
            FROM users u
            JOIN clients c ON c.id = u.client_id
            WHERE u.id = %s
            LIMIT 1
        """, (user_id,))

        row = cur.fetchone()

        if not row:
            return None

        prefix = row["store_prefix"]
        session["store_prefix"] = prefix

        return prefix

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS table_ref", (f"public.{table_name}",))
    row = cur.fetchone()

    if not row:
        return False

    if isinstance(row, dict):
        return row.get("table_ref") is not None

    return row[0] is not None


def _get_columns(cur, table_name: str) -> set[str]:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
    """, (table_name,))

    rows = cur.fetchall()

    columns = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(row["column_name"])
        else:
            columns.add(row[0])

    return columns


def _to_float(value):
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None

def _get_current_user_subscription_plan(cur) -> str:
    user_id = session.get("user_id")

    if not user_id:
        return "Basic"

    cur.execute("""
        SELECT subscription_plan
        FROM users
        WHERE id = %s
        LIMIT 1
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        return "Basic"

    if isinstance(row, dict):
        return row.get("subscription_plan") or "Basic"

    return row[0] or "Basic"


def _has_price_history_access(subscription_plan: str | None) -> bool:
    plan = (subscription_plan or "").strip().lower()

    return plan in {
        "pro",
        "enterprise",
        # zostawiam też premium, bo w Twoim kodzie AI wcześniej sprawdzało Premium
        "premium",
    }

def _effective_price(price_normal, price_special):
    special = _to_float(price_special)
    normal = _to_float(price_normal)

    if special is not None and special > 0:
        return special

    return normal

def _normalize_image_url(image_url):
    if not image_url:
        return None

    image_url = str(image_url).strip()

    if not image_url:
        return None

    if image_url.startswith("/api/image-proxy"):
        return image_url

    if image_url.startswith("//"):
        image_url = f"https:{image_url}"

    if image_url.startswith("http://"):
        image_url = "https://" + image_url[len("http://"):]

    if not image_url.startswith("https://") and not image_url.startswith("http://"):
        return None

    return image_url

def _is_safe_proxy_target(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False

    if not hostname or any(ord(char) <= 32 or ord(char) == 127 for char in hostname):
        return False

    try:
        addrs = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, ValueError):
        return False

    for family, _, _, _, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False

    return True

def _proxied_image_url(image_url):
    normalized = _normalize_image_url(image_url)

    if not normalized:
        return None

    if normalized.startswith("/api/image-proxy"):
        return normalized

    return f"/api/image-proxy?url={quote(normalized, safe='')}"


def _prepare_product_for_response(product: dict) -> dict:
    item = dict(product)

    original_image = item.get("image")
    item["image_original"] = original_image
    item["image"] = _proxied_image_url(original_image)

    item["display_price"] = _effective_price(
        item.get("price_normal"),
        item.get("price_special"),
    )

    return item


def _prepare_competitor_for_response(competitor: dict) -> dict:
    item = dict(competitor)

    original_image = item.get("image")
    item["image_original"] = original_image
    item["image"] = _proxied_image_url(original_image)

    item["display_price"] = _effective_price(
        item.get("price_normal"),
        item.get("price_special"),
    )

    return item


def _flat_competitor_columns(match_columns: set[str]) -> list[str]:
    ignored_prefixes = ("client_", "product_", "our_")
    result = []

    for col in match_columns:
        if not (
            col.endswith("_price")
            or col.endswith("_url")
            or col.endswith("_name")
        ):
            continue

        if any(col.startswith(prefix) for prefix in ignored_prefixes):
            continue

        result.append(col)

    return sorted(result)


def _flat_competitor_price_columns(match_columns: set[str]) -> list[str]:
    return [
        col
        for col in _flat_competitor_columns(match_columns)
        if col.endswith("_price")
    ]


def _flat_has_competitor_sql(match_columns: set[str]):
    price_cols = _flat_competitor_price_columns(match_columns)

    if not price_cols:
        return None

    return sql.SQL(" OR ").join(
        sql.SQL("{} IS NOT NULL").format(sql.Identifier(col))
        for col in price_cols
    )


def _fetch_match_counts(cur, matches_table: str, products: list[dict]) -> dict:
    if not products or not _table_exists(cur, matches_table):
        return {}

    match_columns = _get_columns(cur, matches_table)
    has_competitor_sql = _flat_has_competitor_sql(match_columns)

    if "product_id" in match_columns:
        product_ids = [p["id"] for p in products]

        if "competitor_product_id" in match_columns:
            query = sql.SQL("""
                SELECT product_id AS key, COUNT(*) AS count
                FROM {}
                WHERE product_id = ANY(%s)
                  AND competitor_product_id IS NOT NULL
                GROUP BY product_id
            """).format(sql.Identifier(matches_table))
        else:
            query = sql.SQL("""
                SELECT product_id AS key, COUNT(*) AS count
                FROM {}
                WHERE product_id = ANY(%s)
                GROUP BY product_id
            """).format(sql.Identifier(matches_table))

        cur.execute(query, (product_ids,))
        return {row["key"]: row["count"] for row in cur.fetchall()}

    if has_competitor_sql is None:
        return {}

    if "client_sku" in match_columns:
        skus = [p["sku"] for p in products if p.get("sku")]

        query = sql.SQL("""
            SELECT client_sku AS key, COUNT(*) AS count
            FROM {}
            WHERE client_sku = ANY(%s)
              AND ({})
            GROUP BY client_sku
        """).format(
            sql.Identifier(matches_table),
            has_competitor_sql,
        )

        cur.execute(query, (skus,))
        return {row["key"]: row["count"] for row in cur.fetchall()}

    if "product_sku" in match_columns:
        skus = [p["sku"] for p in products if p.get("sku")]

        query = sql.SQL("""
            SELECT product_sku AS key, COUNT(*) AS count
            FROM {}
            WHERE product_sku = ANY(%s)
              AND ({})
            GROUP BY product_sku
        """).format(
            sql.Identifier(matches_table),
            has_competitor_sql,
        )

        cur.execute(query, (skus,))
        return {row["key"]: row["count"] for row in cur.fetchall()}

    if "product_name" in match_columns:
        names = [p["name"] for p in products if p.get("name")]

        query = sql.SQL("""
            SELECT product_name AS key, COUNT(*) AS count
            FROM {}
            WHERE product_name = ANY(%s)
              AND ({})
            GROUP BY product_name
        """).format(
            sql.Identifier(matches_table),
            has_competitor_sql,
        )

        cur.execute(query, (names,))
        return {row["key"]: row["count"] for row in cur.fetchall()}

    if "client_name" in match_columns:
        names = [p["name"] for p in products if p.get("name")]

        query = sql.SQL("""
            SELECT client_name AS key, COUNT(*) AS count
            FROM {}
            WHERE client_name = ANY(%s)
              AND ({})
            GROUP BY client_name
        """).format(
            sql.Identifier(matches_table),
            has_competitor_sql,
        )

        cur.execute(query, (names,))
        return {row["key"]: row["count"] for row in cur.fetchall()}

    return {}


def _add_match_counts(cur, matches_table: str, products: list[dict]) -> list[dict]:
    if not products:
        return products

    match_columns = _get_columns(cur, matches_table) if _table_exists(cur, matches_table) else set()
    counts = _fetch_match_counts(cur, matches_table, products)

    result = []

    for product in products:
        item = dict(product)

        if "product_id" in match_columns:
            key = item.get("id")
        elif "client_sku" in match_columns or "product_sku" in match_columns:
            key = item.get("sku")
        elif "product_name" in match_columns or "client_name" in match_columns:
            key = item.get("name")
        else:
            key = None

        item["competitors_count"] = counts.get(key, 0) if key is not None else 0
        result.append(item)

    return result


def _fetch_competitors_for_product(cur, product: dict, matches_table: str, competitors_table: str) -> list[dict]:
    if not product or not _table_exists(cur, matches_table):
        return []

    match_columns = _get_columns(cur, matches_table)
    competitors_exist = _table_exists(cur, competitors_table)

    if (
        competitors_exist
        and "product_id" in match_columns
        and "competitor_product_id" in match_columns
    ):
        query = sql.SQL("""
            SELECT
                c.id,
                c.sku,
                c.name,
                c.price_normal,
                c.price_special,
                c.store,
                c.availability,
                c.url,
                c.image,
                m.similarity_score,
                m.match_status
            FROM {} m
            LEFT JOIN {} c ON c.id = m.competitor_product_id
            WHERE m.product_id = %s
              AND m.competitor_product_id IS NOT NULL
            ORDER BY m.similarity_score DESC NULLS LAST
        """).format(
            sql.Identifier(matches_table),
            sql.Identifier(competitors_table),
        )

        cur.execute(query, (product["id"],))
        rows = []

        product_display_price = _effective_price(
            product.get("price_normal"),
            product.get("price_special"),
        )

        for row in cur.fetchall():
            item = dict(row)

            competitor_display_price = _effective_price(
                item.get("price_normal"),
                item.get("price_special"),
            )

            item["display_price"] = competitor_display_price
            item["price_difference"] = (
                float(competitor_display_price) - float(product_display_price)
                if competitor_display_price is not None and product_display_price is not None
                else None
            )

            rows.append(_prepare_competitor_for_response(item))

        return rows

    # Wariant aktualny: matches jest płaską tabelą z files_connector,
    # np. jmbdesing_price, jmbdesing_url, jmbdesing_name.
    match_row = None

    if "client_sku" in match_columns and product.get("sku"):
        query = sql.SQL("""
            SELECT *
            FROM {}
            WHERE client_sku = %s
            LIMIT 1
        """).format(sql.Identifier(matches_table))

        cur.execute(query, (product["sku"],))
        match_row = cur.fetchone()

    elif "product_sku" in match_columns and product.get("sku"):
        query = sql.SQL("""
            SELECT *
            FROM {}
            WHERE product_sku = %s
            LIMIT 1
        """).format(sql.Identifier(matches_table))

        cur.execute(query, (product["sku"],))
        match_row = cur.fetchone()

    elif "product_name" in match_columns and product.get("name"):
        query = sql.SQL("""
            SELECT *
            FROM {}
            WHERE product_name = %s
            LIMIT 1
        """).format(sql.Identifier(matches_table))

        cur.execute(query, (product["name"],))
        match_row = cur.fetchone()

    elif "client_name" in match_columns and product.get("name"):
        query = sql.SQL("""
            SELECT *
            FROM {}
            WHERE client_name = %s
            LIMIT 1
        """).format(sql.Identifier(matches_table))

        cur.execute(query, (product["name"],))
        match_row = cur.fetchone()

    if not match_row:
        return []

    match_row = dict(match_row)
    competitors = []

    product_display_price = _effective_price(
        product.get("price_normal"),
        product.get("price_special"),
    )

    # Szukamy kolumn typu:
    # calavado_price
    # calavado_url
    # calavado_name
    #
    # Pomijamy:
    # client_price
    # product_price
    # our_price
    for column_name, value in match_row.items():
        if not column_name.endswith("_price"):
            continue

        if column_name in ("client_price", "product_price", "our_price"):
            continue

        if (
            column_name.startswith("client_")
            or column_name.startswith("product_")
            or column_name.startswith("our_")
        ):
            continue

        store_name = column_name[:-6]

        match_price = match_row.get(f"{store_name}_price")
        url = match_row.get(f"{store_name}_url")
        name = match_row.get(f"{store_name}_name")

        if match_price is None and name is None and url is None:
            continue

        image = None
        availability = None
        competitor_sku = None

        # Domyślnie bierzemy cenę z matches, ale później nadpisujemy ją pełnymi danymi z competitors_table.
        competitor_price_normal = match_price
        competitor_price_special = None

        if competitors_exist:
            try:
                extra = None

                if url:
                    extra_query = sql.SQL("""
                        SELECT
                            sku,
                            name,
                            image,
                            availability,
                            price_normal,
                            price_special,
                            url
                        FROM {}
                        WHERE TRIM(TRAILING '/' FROM url) =
                              TRIM(TRAILING '/' FROM %s)
                        LIMIT 1
                    """).format(sql.Identifier(competitors_table))

                    cur.execute(extra_query, (url,))
                    extra = cur.fetchone()

                # Awaryjne wyszukanie po nazwie i sklepie,
                # gdy w tabeli matches nie ma URL-u.
                if not extra and name:
                    extra_query = sql.SQL("""
                        SELECT
                            sku,
                            name,
                            image,
                            availability,
                            price_normal,
                            price_special,
                            url
                        FROM {}
                        WHERE LOWER(store) = LOWER(%s)
                          AND LOWER(name) = LOWER(%s)
                        LIMIT 1
                    """).format(sql.Identifier(competitors_table))

                    cur.execute(extra_query, (store_name, name))
                    extra = cur.fetchone()

                if extra:
                    extra = dict(extra)

                    competitor_sku = extra.get("sku")
                    image = extra.get("image")
                    availability = extra.get("availability")
                    url = url or extra.get("url")

                    if extra.get("price_normal") is not None:
                        competitor_price_normal = extra.get("price_normal")

                    if extra.get("price_special") is not None:
                        competitor_price_special = extra.get("price_special")

            except Exception as e:
                print(
                    f"[PRODUCTS] Nie udało się pobrać szczegółów "
                    f"konkurenta {store_name}: {e}"
                )

        competitor_display_price = _effective_price(
            competitor_price_normal,
            competitor_price_special,
        )

        competitor = {
            "id": None,
            "sku": competitor_sku,
            "name": name or store_name,
            "price_normal": competitor_price_normal,
            "price_special": competitor_price_special,
            "display_price": competitor_display_price,
            "store": store_name,
            "shop_label": store_name,
            "availability": availability,
            "url": url,
            "image": image,
            "similarity_score": None,
            "price_difference": (
                float(competitor_display_price) - float(product_display_price)
                if competitor_display_price is not None and product_display_price is not None
                else None
            ),
            "match_status": "matched",
        }

        competitors.append(_prepare_competitor_for_response(competitor))

    return competitors


def _get_matched_products_filter(cur, matches_table: str):
    if not _table_exists(cur, matches_table):
        return sql.SQL("FALSE")

    match_columns = _get_columns(cur, matches_table)
    has_competitor_sql = _flat_has_competitor_sql(match_columns)

    if "product_id" in match_columns:
        if "competitor_product_id" in match_columns:
            return sql.SQL("""
                p.id IN (
                    SELECT m.product_id
                    FROM {} m
                    WHERE m.competitor_product_id IS NOT NULL
                )
            """).format(sql.Identifier(matches_table))

        return sql.SQL("""
            p.id IN (
                SELECT m.product_id
                FROM {} m
            )
        """).format(sql.Identifier(matches_table))

    if has_competitor_sql is not None and "client_sku" in match_columns:
        return sql.SQL("""
            p.sku IN (
                SELECT m.client_sku
                FROM {} m
                WHERE {}
            )
        """).format(sql.Identifier(matches_table), has_competitor_sql)

    if has_competitor_sql is not None and "product_sku" in match_columns:
        return sql.SQL("""
            p.sku IN (
                SELECT m.product_sku
                FROM {} m
                WHERE {}
            )
        """).format(sql.Identifier(matches_table), has_competitor_sql)

    if has_competitor_sql is not None and "product_name" in match_columns:
        return sql.SQL("""
            p.name IN (
                SELECT m.product_name
                FROM {} m
                WHERE {}
            )
        """).format(sql.Identifier(matches_table), has_competitor_sql)

    if has_competitor_sql is not None and "client_name" in match_columns:
        return sql.SQL("""
            p.name IN (
                SELECT m.client_name
                FROM {} m
                WHERE {}
            )
        """).format(sql.Identifier(matches_table), has_competitor_sql)

    return sql.SQL("FALSE")


def _get_match_analysis_summary(cur, matches_table: str) -> dict:
    summary = {
        "matched_products": 0,
        "total_competitor_matches": 0,
        "our_price_lower_count": 0,
        "our_price_higher_count": 0,
        "same_price_count": 0,
        "avg_difference_competitor_minus_ours": None,
        "max_saving_when_ours_cheaper": None,
        "max_loss_when_ours_expensive": None,
    }

    if not _table_exists(cur, matches_table):
        return summary

    match_columns = _get_columns(cur, matches_table)
    competitor_price_columns = _flat_competitor_price_columns(match_columns)

    if not competitor_price_columns:
        return summary

    client_price_col = None

    if "client_price" in match_columns:
        client_price_col = "client_price"
    elif "product_price" in match_columns:
        client_price_col = "product_price"
    elif "our_price" in match_columns:
        client_price_col = "our_price"

    if not client_price_col:
        return summary

    select_columns = [client_price_col] + competitor_price_columns

    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(sql.Identifier(col) for col in select_columns),
        sql.Identifier(matches_table),
    )

    cur.execute(query)
    rows = cur.fetchall()

    matched_product_count = 0
    total_matches = 0
    diffs = []
    savings = []
    losses = []
    our_lower = 0
    our_higher = 0
    same_price = 0

    for row in rows:
        row = dict(row)
        client_price = _to_float(row.get(client_price_col))

        row_has_match = False

        for col in competitor_price_columns:
            competitor_price = _to_float(row.get(col))

            if competitor_price is None or client_price is None:
                continue

            row_has_match = True
            total_matches += 1

            diff = competitor_price - client_price
            diffs.append(diff)

            if diff > 0:
                our_lower += 1
                savings.append(diff)
            elif diff < 0:
                our_higher += 1
                losses.append(abs(diff))
            else:
                same_price += 1

        if row_has_match:
            matched_product_count += 1

    summary["matched_products"] = matched_product_count
    summary["total_competitor_matches"] = total_matches
    summary["our_price_lower_count"] = our_lower
    summary["our_price_higher_count"] = our_higher
    summary["same_price_count"] = same_price
    summary["avg_difference_competitor_minus_ours"] = round(sum(diffs) / len(diffs), 2) if diffs else None
    summary["max_saving_when_ours_cheaper"] = round(max(savings), 2) if savings else None
    summary["max_loss_when_ours_expensive"] = round(max(losses), 2) if losses else None

    return summary


@products_bp.route("/api/image-proxy")
@login_required
@limiter.limit("300 per minute", key_func=user_rate_key)
def api_image_proxy():
    raw_url = request.args.get("url", "").strip()

    if not raw_url:
        return jsonify({
            "ok": False,
            "error": "Brak parametru url"
        }), 400

    image_url = _normalize_image_url(unquote(raw_url))

    if not image_url:
        return jsonify({
            "ok": False,
            "error": "Nieprawidłowy URL obrazka"
        }), 400

    if not _is_safe_proxy_target(image_url):
        return jsonify({
            "ok": False,
            "error": "Niedozwolony host docelowy"
        }), 400

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*;q=0.8",
    }

    try:
        for _ in range(IMAGE_PROXY_MAX_REDIRECTS + 1):
            response = requests.get(
                image_url,
                timeout=12,
                headers=headers,
                stream=True,
                allow_redirects=False,
            )

            if response.is_redirect:
                image_url = urljoin(image_url, response.headers.get("Location", ""))
                response.close()
                if not _is_safe_proxy_target(image_url):
                    return jsonify({
                        "ok": False,
                        "error": "Niedozwolony host docelowy"
                    }), 400
                continue
            break
        else:
            return jsonify({"ok": False, "error": "Zbyt wiele przekierowań"}), 400

        if response.status_code >= 400:
            response.close()
            return jsonify({
                "ok": False,
                "error": f"Nie udało się pobrać obrazka. Status={response.status_code}"
            }), 502

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in IMAGE_PROXY_ALLOWED_TYPES:
            response.close()
            return jsonify({"ok": False, "error": "Adres nie wskazuje na obrazek"}), 400

        content = b""
        for chunk in response.iter_content(64 * 1024):
            content += chunk
            if len(content) > IMAGE_PROXY_MAX_BYTES:
                response.close()
                return jsonify({"ok": False, "error": "Obrazek jest za duży"}), 413

        return Response(
            content,
            mimetype=content_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    except (requests.RequestException, ValueError):
        return jsonify({"ok": False, "error": "Nie udało się pobrać obrazka"}), 502


@products_bp.route("/api/products")
@login_required
def api_products():
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({"ok": False, "error": "Brak zdefiniowanego sklepu w sesji"}), 401

    products_table = f"{prefix}_products"
    matches_table = f"{prefix}_matches"
    competitors_table = f"{prefix}_competitors"

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort", "id")
    order = request.args.get("order", "asc").upper()
    search_query = request.args.get("search", "").strip()
    matched_only = request.args.get("matched_only", "false").lower() == "true"
    
    category = request.args.get("category", "").strip()
    brand = request.args.get("brand", "").strip()
    store = request.args.get("store", "").strip()
    price_min = request.args.get("price_min", "").strip()
    price_max = request.args.get("price_max", "").strip()
    min_matches = request.args.get("min_matches", type=int)

    allowed_sort_columns = ["id", "sku", "name", "price_normal", "price_special", "category", "store", "availability"]
    if sort_by not in allowed_sort_columns: sort_by = "id"
    if order not in ["ASC", "DESC"]: order = "ASC"

    offset = (page - 1) * per_page
    where_parts = []
    params = []

    if search_query:
        where_parts.append(sql.SQL("""
            (p.name ILIKE %s OR p.sku ILIKE %s OR p.category ILIKE %s OR p.manufacturer ILIKE %s)
        """))
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])

    if category:
        where_parts.append(sql.SQL("p.category = %s"))
        params.append(category)
        
    if brand:
        where_parts.append(sql.SQL("p.manufacturer = %s"))
        params.append(brand)
        
    if price_min:
        where_parts.append(sql.SQL("(COALESCE(p.price_special, p.price_normal) >= %s)"))
        params.append(float(price_min))
        
    if price_max:
        where_parts.append(sql.SQL("(COALESCE(p.price_special, p.price_normal) <= %s)"))
        params.append(float(price_max))

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not _table_exists(cursor, products_table):
            return jsonify({"ok": True, "data": [], "pagination": {"total_items": 0, "current_page": page, "per_page": per_page, "total_pages": 1}})

        if matched_only:
            where_parts.append(_get_matched_products_filter(cursor, matches_table))

        if store and _table_exists(cursor, matches_table):
            match_columns = _get_columns(cursor, matches_table)
            
            if "competitor_product_id" in match_columns and _table_exists(cursor, competitors_table):
                where_parts.append(sql.SQL("""
                    p.id IN (
                        SELECT m.product_id 
                        FROM {} m
                        JOIN {} c ON c.id = m.competitor_product_id
                        WHERE c.store = %s
                    )
                """).format(sql.Identifier(matches_table), sql.Identifier(competitors_table)))
                params.append(store)
            else:
                store_safe = store.lower().replace(" ", "_")
                store_col = f"{store_safe}_price"
                if store_col in match_columns:
                    if "product_id" in match_columns:
                        where_parts.append(sql.SQL("p.id IN (SELECT m.product_id FROM {} m WHERE m.{} IS NOT NULL)").format(sql.Identifier(matches_table), sql.Identifier(store_col)))
                    elif "client_sku" in match_columns:
                        where_parts.append(sql.SQL("p.sku IN (SELECT m.client_sku FROM {} m WHERE m.{} IS NOT NULL)").format(sql.Identifier(matches_table), sql.Identifier(store_col)))
                    else:
                        where_parts.append(sql.SQL("FALSE"))
                else:
                    where_parts.append(sql.SQL("FALSE"))

        if min_matches and min_matches > 0 and _table_exists(cursor, matches_table):
            match_columns = _get_columns(cursor, matches_table)
            has_competitor_sql = _flat_has_competitor_sql(match_columns)

            if "product_id" in match_columns:
                if "competitor_product_id" in match_columns:
                    where_parts.append(sql.SQL("p.id IN (SELECT product_id FROM {} WHERE competitor_product_id IS NOT NULL GROUP BY product_id HAVING COUNT(*) >= %s)").format(sql.Identifier(matches_table)))
                else:
                    where_parts.append(sql.SQL("p.id IN (SELECT product_id FROM {} GROUP BY product_id HAVING COUNT(*) >= %s)").format(sql.Identifier(matches_table)))
                params.append(min_matches)
            elif has_competitor_sql is not None and "client_sku" in match_columns:
                where_parts.append(sql.SQL("p.sku IN (SELECT client_sku FROM {} WHERE {} GROUP BY client_sku HAVING COUNT(*) >= %s)").format(sql.Identifier(matches_table), has_competitor_sql))
                params.append(min_matches)
            elif has_competitor_sql is not None and "product_sku" in match_columns:
                where_parts.append(sql.SQL("p.sku IN (SELECT product_sku FROM {} WHERE {} GROUP BY product_sku HAVING COUNT(*) >= %s)").format(sql.Identifier(matches_table), has_competitor_sql))
                params.append(min_matches)
            else:
                where_parts.append(sql.SQL("FALSE"))

        where_sql = sql.SQL("")
        if where_parts:
            where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_parts)

        count_query = sql.SQL("SELECT COUNT(p.id) AS total FROM {} p {}").format(sql.Identifier(products_table), where_sql)
        cursor.execute(count_query, tuple(params))
        total_items = cursor.fetchone()["total"]

        query = sql.SQL("""
            SELECT p.id, p.sku, p.name, p.size, p.color, p.manufacturer, p.category, p.price_normal, p.price_special, p.store, p.availability, p.url, p.image, p.description
            FROM {} p {} ORDER BY {} {} LIMIT %s OFFSET %s
        """).format(sql.Identifier(products_table), where_sql, sql.Identifier(sort_by), sql.SQL(order))

        cursor.execute(query, tuple(params + [per_page, offset]))
        products = [dict(row) for row in cursor.fetchall()]

        products = _add_match_counts(cursor, matches_table, products)
        products = [_prepare_product_for_response(product) for product in products]

        return jsonify({
            "ok": True,
            "data": products,
            "pagination": {"total_items": total_items, "current_page": page, "per_page": per_page, "total_pages": math.ceil(total_items / per_page) if total_items > 0 else 1}
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@products_bp.route("/api/products/<int:product_id>")
@login_required
def api_product_detail(product_id):
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({
            "ok": False,
            "error": "Brak zdefiniowanego sklepu w sesji użytkownika"
        }), 401

    products_table = f"{prefix}_products"
    competitors_table = f"{prefix}_competitors"
    matches_table = f"{prefix}_matches"

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not _table_exists(cursor, products_table):
            return jsonify({
                "ok": False,
                "error": "Tabela produktów klienta nie istnieje"
            }), 404

        query = sql.SQL("""
            SELECT
                id,
                sku,
                name,
                size,
                color,
                manufacturer,
                category,
                price_normal,
                price_special,
                store,
                availability,
                url,
                image,
                description
            FROM {}
            WHERE id = %s
        """).format(sql.Identifier(products_table))

        cursor.execute(query, (product_id,))
        product = cursor.fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "Produkt nie został znaleziony"
            }), 404

        product = _prepare_product_for_response(dict(product))

        competitors = _fetch_competitors_for_product(
            cursor,
            product,
            matches_table,
            competitors_table,
        )

        return jsonify({
            "ok": True,
            "data": product,
            "competitors": competitors,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@products_bp.route("/api/products/<int:product_id>/price-history")
@login_required
def api_product_price_history(product_id: int):
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({
            "ok": False,
            "error": "Brak zdefiniowanego sklepu w sesji użytkownika"
        }), 401

    products_table = f"{prefix}_products"
    matches_table = f"{prefix}_matches"
    competitors_table = f"{prefix}_competitors"
    history_table = f"{prefix}_competitors_history"

    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        subscription_plan = _get_current_user_subscription_plan(cur)

        if not _has_price_history_access(subscription_plan):
            return jsonify({
                "ok": False,
                "locked": True,
                "subscription_plan": subscription_plan,
                "required_plans": ["Pro", "Enterprise"],
                "error": "Historia zmian cen jest dostępna od pakietu Pro."
            }), 403

        if not _table_exists(cur, products_table):
            return jsonify({
                "ok": False,
                "error": f"Tabela produktów {products_table} nie istnieje"
            }), 404

        if not _table_exists(cur, history_table):
            return jsonify({
                "ok": True,
                "data": [],
                "message": "Tabela historii cen jeszcze nie istnieje"
            })

        cur.execute(
            sql.SQL("""
                SELECT *
                FROM {}
                WHERE id = %s
                LIMIT 1
            """).format(sql.Identifier(products_table)),
            (product_id,)
        )

        product = cur.fetchone()

        if not product:
            return jsonify({
                "ok": False,
                "error": "Nie znaleziono produktu"
            }), 404

        product = dict(product)

        competitors = _fetch_competitors_for_product(
            cur,
            product,
            matches_table,
            competitors_table,
        )

        if not competitors:
            return jsonify({
                "ok": True,
                "data": [],
                "message": "Produkt nie ma dopasowanych ofert konkurencji"
            })

        history_rows = []
        used_pairs = set()

        for competitor in competitors:
            competitor_sku = competitor.get("sku")
            competitor_store = competitor.get("store")

            if not competitor_sku or not competitor_store:
                continue

            pair_key = (competitor_sku, competitor_store)

            if pair_key in used_pairs:
                continue

            used_pairs.add(pair_key)

            cur.execute(
                sql.SQL("""
                    SELECT
                        sku,
                        store,
                        price_normal_old,
                        price_normal_new,
                        price_special_old,
                        price_special_new,
                        valid_from,
                        valid_to,
                        is_current
                    FROM {}
                    WHERE sku = %s
                      AND store = %s
                    ORDER BY valid_from DESC
                """).format(sql.Identifier(history_table)),
                (competitor_sku, competitor_store)
            )

            rows = cur.fetchall()

            for row in rows:
                row = dict(row)

                old_display_price = _effective_price(
                    row.get("price_normal_old"),
                    row.get("price_special_old"),
                )

                new_display_price = _effective_price(
                    row.get("price_normal_new"),
                    row.get("price_special_new"),
                )

                difference = (
                    round(float(new_display_price) - float(old_display_price), 2)
                    if old_display_price is not None and new_display_price is not None
                    else None
                )

                history_rows.append({
                    "sku": row.get("sku"),
                    "store": row.get("store"),
                    "name": competitor.get("name"),
                    "url": competitor.get("url"),
                    "image": competitor.get("image"),

                    "price_normal_old": row.get("price_normal_old"),
                    "price_normal_new": row.get("price_normal_new"),
                    "price_special_old": row.get("price_special_old"),
                    "price_special_new": row.get("price_special_new"),

                    "old_display_price": old_display_price,
                    "new_display_price": new_display_price,
                    "difference": difference,

                    "valid_from": row.get("valid_from").isoformat() if row.get("valid_from") else None,
                    "valid_to": row.get("valid_to").isoformat() if row.get("valid_to") else None,
                    "is_current": row.get("is_current"),
                })

        return jsonify({
            "ok": True,
            "subscription_plan": subscription_plan,
            "data": history_rows,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@products_bp.route("/api/products/<int:product_id>/recommend", methods=["POST"])
@login_required
@limiter.limit("30 per hour", key_func=user_rate_key)
def api_product_recommend(product_id):
    prefix = _get_store_prefix()
    if not prefix:
        return jsonify({"ok": False, "error": "Brak zdefiniowanego sklepu w sesji"}), 401

    products_table = f"{prefix}_products"
    competitors_table = f"{prefix}_competitors"
    matches_table = f"{prefix}_matches"

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not _table_exists(cursor, products_table):
            return jsonify({"ok": False, "error": "Tabela produktów klienta nie istnieje"}), 404

        query = sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(products_table))
        cursor.execute(query, (product_id,))
        product = cursor.fetchone()

        if not product:
            return jsonify({"ok": False, "error": "Produkt nie został znaleziony"}), 404

        product_dict = dict(product)
        competitors = _fetch_competitors_for_product(cursor, product_dict, matches_table, competitors_table)
        
        prices = []
        for c in competitors:
            price = c.get("price_special") or c.get("price_normal")
            if price is not None:
                prices.append(float(price))

        if not prices:
            return jsonify({
                "ok": False, 
                "error": "Brak danych rynkowych. Model AI potrzebuje ofert konkurencji, aby przeanalizować cenę."
            }), 400

        our_price = float(product_dict.get("price_special") or product_dict.get("price_normal") or 0.0)

        payload = {
            "nazwa_produktu": product_dict.get("name") or "Nieznany produkt",
            "lista_rynkowa": prices,
            "nasza_cena": our_price
        }

        import requests
        response = requests.post("http://recommendation_service:8000/api/oblicz-cene", json=payload, timeout=30)
        
        if response.status_code != 200:
            return jsonify({
                "ok": False, 
                "error": f"AI Serwis odrzucił żądanie (Kod {response.status_code}): {response.text}"
            }), 502
            
        data = response.json()
        return jsonify({
            "ok": True,
            "recommendation": data.get("cena_ostateczna"),
            "reason": data.get("uzasadnienie")
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@products_bp.route("/api/stats")
@login_required
def api_stats():
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({
            "ok": False,
            "error": "Brak zdefiniowanego sklepu w sesji"
        }), 401

    products_table = f"{prefix}_products"
    competitors_table = f"{prefix}_competitors"
    matches_table = f"{prefix}_matches"

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        product_summary = {
            "total": 0,
            "avg_price_normal": None,
            "avg_price_special": None,
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

        if _table_exists(cursor, products_table):
            product_summary_query = sql.SQL("""
                SELECT
                    COUNT(*) AS total,
                    AVG(price_normal) AS avg_price_normal,
                    AVG(price_special) AS avg_price_special,
                    COUNT(DISTINCT category) AS total_categories,
                    COUNT(DISTINCT manufacturer) AS total_manufacturers
                FROM {}
            """).format(sql.Identifier(products_table))

            cursor.execute(product_summary_query)
            product_summary = dict(cursor.fetchone())

        if _table_exists(cursor, competitors_table):
            competitor_summary_query = sql.SQL("""
                SELECT
                    COUNT(*) AS total_competitor_products,
                    AVG(price_normal) AS avg_competitor_price,
                    COUNT(DISTINCT store) AS total_stores
                FROM {}
            """).format(sql.Identifier(competitors_table))

            cursor.execute(competitor_summary_query)
            competitor_summary = dict(cursor.fetchone())

        if _table_exists(cursor, matches_table):
            match_summary = _get_match_analysis_summary(cursor, matches_table)

        summary = {
            "total": match_summary.get("matched_products") or 0,

            "avg_price_normal": competitor_summary.get("avg_competitor_price"),

            "avg_price_special": None,
            "total_categories": product_summary.get("total_categories") or 0,
            "total_stores": competitor_summary.get("total_stores") or 0,
            "total_manufacturers": product_summary.get("total_manufacturers") or 0,

            "total_client_products": product_summary.get("total") or 0,
            "avg_client_price": product_summary.get("avg_price_normal"),
            "total_competitor_products": competitor_summary.get("total_competitor_products") or 0,
            "avg_competitor_price": competitor_summary.get("avg_competitor_price"),

            "matched_products": match_summary.get("matched_products") or 0,
            "total_competitor_matches": match_summary.get("total_competitor_matches") or 0,
            "our_price_lower_count": match_summary.get("our_price_lower_count") or 0,
            "our_price_higher_count": match_summary.get("our_price_higher_count") or 0,
            "same_price_count": match_summary.get("same_price_count") or 0,
            "avg_difference_competitor_minus_ours": match_summary.get("avg_difference_competitor_minus_ours"),
            "max_saving_when_ours_cheaper": match_summary.get("max_saving_when_ours_cheaper"),
            "max_loss_when_ours_expensive": match_summary.get("max_loss_when_ours_expensive"),
        }

        by_category = []
        by_store = []
        by_availability = []

        if _table_exists(cursor, products_table):
            by_category_query = sql.SQL("""
                SELECT
                    category,
                    COUNT(*) AS count,
                    AVG(price_normal) AS avg_price
                FROM {}
                GROUP BY category
                ORDER BY count DESC
            """).format(sql.Identifier(products_table))

            cursor.execute(by_category_query)
            by_category = cursor.fetchall()

            by_availability_query = sql.SQL("""
                SELECT
                    availability,
                    COUNT(*) AS count
                FROM {}
                GROUP BY availability
                ORDER BY count DESC
            """).format(sql.Identifier(products_table))

            cursor.execute(by_availability_query)
            by_availability = cursor.fetchall()

        if _table_exists(cursor, competitors_table):
            by_store_query = sql.SQL("""
                SELECT
                    store,
                    COUNT(*) AS count
                FROM {}
                GROUP BY store
                ORDER BY count DESC
            """).format(sql.Identifier(competitors_table))

            cursor.execute(by_store_query)
            by_store = cursor.fetchall()

        return jsonify({
            "ok": True,
            "summary": summary,
            "by_category": by_category,
            "by_store": by_store,
            "by_availability": by_availability,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@products_bp.route("/api/categories")
@login_required
def api_categories():
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({
            "ok": False,
            "error": "Brak zdefiniowanego sklepu w sesji"
        }), 401

    products_table = f"{prefix}_products"

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()

        if not _table_exists(cursor, products_table):
            return jsonify({
                "ok": True,
                "data": []
            })

        query = sql.SQL("""
            SELECT DISTINCT category
            FROM {}
            WHERE category IS NOT NULL
            ORDER BY category
        """).format(sql.Identifier(products_table))

        cursor.execute(query)

        return jsonify({
            "ok": True,
            "data": [r[0] for r in cursor.fetchall()]
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@products_bp.route("/api/stores")
@login_required
def api_stores():
    prefix = _get_store_prefix()

    if not prefix:
        return jsonify({
            "ok": False,
            "error": "Brak zdefiniowanego sklepu w sesji"
        }), 401

    competitors_table = f"{prefix}_competitors"

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor()

        if not _table_exists(cursor, competitors_table):
            return jsonify({
                "ok": True,
                "data": []
            })

        query = sql.SQL("""
            SELECT DISTINCT store
            FROM {}
            WHERE store IS NOT NULL
            ORDER BY store
        """).format(sql.Identifier(competitors_table))

        cursor.execute(query)

        return jsonify({
            "ok": True,
            "data": [r[0] for r in cursor.fetchall()]
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@products_bp.route("/api/filters", methods=["GET"])
@login_required
def get_filters():
    prefix = _get_store_prefix()
    if not prefix:
        return jsonify({"error": "Brak zdefiniowanego sklepu"}), 400
        
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        products_table = f"{prefix}_products"
        competitors_table = f"{prefix}_competitors"
        
        categories = []
        brands = []
        stores = []
        
        if _table_exists(cur, products_table):
            cur.execute(sql.SQL("SELECT DISTINCT category FROM {} WHERE category IS NOT NULL AND category != ''").format(sql.Identifier(products_table)))
            categories = [row['category'] for row in cur.fetchall()]

            cur.execute(sql.SQL("SELECT DISTINCT manufacturer FROM {} WHERE manufacturer IS NOT NULL AND manufacturer != ''").format(sql.Identifier(products_table)))
            brands = [row['manufacturer'] for row in cur.fetchall()]

        if _table_exists(cur, competitors_table):
            cur.execute(sql.SQL("SELECT DISTINCT store FROM {} WHERE store IS NOT NULL AND store != ''").format(sql.Identifier(competitors_table)))
            stores = [row['store'] for row in cur.fetchall()]

        return jsonify({
            "ok": True,
            "categories": sorted(categories),
            "brands": sorted(brands),
            "stores": sorted(stores),
            "max_stores": len(stores) if len(stores) > 0 else 5
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()