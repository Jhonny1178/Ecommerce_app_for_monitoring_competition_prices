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

def _store_key(name) -> str:
    """Nazwa sklepu tylko z liter i cyfr (podpierzyna = pod_pierzyna)."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


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


def _store_key(name) -> str:
    """Nazwa sklepu tylko z liter i cyfr (podpierzyna = pod_pierzyna)."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _competitor_price_columns(match_columns: set[str], store: str | None = None) -> list[tuple[str, str | None]]:
    """Kolumny (cena, cena promocyjna) sklepów konkurencji w matches, opcjonalnie jednego sklepu."""
    result = []

    for price_column in _flat_competitor_price_columns(match_columns):
        store_name = price_column[:-len("_price")]

        if store and _store_key(store_name) != _store_key(store):
            continue

        special_column = f"{store_name}_price_special"
        result.append((price_column, special_column if special_column in match_columns else None))

    return result


_CLIENT_PRICE_SQL = sql.SQL("(CASE WHEN p.price_special > 0 THEN p.price_special ELSE p.price_normal END)")

PRICE_DIFF_DIRECTIONS = {"higher", "lower"}
PRICE_DIFF_UNITS = {"pln", "percent"}


def _competitor_price_sql(price_column: str, special_column: str | None):
    price = sql.SQL("CAST(m.{} AS double precision)").format(sql.Identifier(price_column))

    if not special_column:
        return price

    special = sql.SQL("CAST(m.{} AS double precision)").format(sql.Identifier(special_column))
    return sql.SQL("(CASE WHEN {special} > 0 THEN {special} ELSE {price} END)").format(
        special=special,
        price=price,
    )


def _price_difference_lateral(match_columns: set[str], matches_table: str, store: str | None,
                              direction: str, diff_min: float | None, diff_max: float | None, unit: str):
    """Złączenie "d" z różnicami cen produktu p (cena konkurencji - cena klienta).

    Liczy tylko ceny spełniające filtr: qualifying, max_abs_diff, max_abs_pct,
    min_diff_pct, max_diff_pct. Zwraca (sql, parametry) albo (None, []).
    """
    columns = _competitor_price_columns(match_columns, store)

    if "client_sku" not in match_columns or not columns:
        return None, []

    prices = sql.SQL(", ").join(
        sql.SQL("({})").format(_competitor_price_sql(price_column, special_column))
        for price_column, special_column in columns
    )
    difference = sql.SQL("(c.price - {})").format(_CLIENT_PRICE_SQL)
    percent = sql.SQL("({} * 100.0 / NULLIF({}, 0))").format(difference, _CLIENT_PRICE_SQL)
    amount = sql.SQL("ABS({})").format(percent if unit == "percent" else difference)

    conditions = [sql.SQL("{} IS NOT NULL").format(difference)]
    params = []

    if direction == "higher":
        conditions.append(sql.SQL("{} > 0").format(difference))
    elif direction == "lower":
        conditions.append(sql.SQL("{} < 0").format(difference))

    if diff_min is not None:
        conditions.append(sql.SQL("{} >= %s").format(amount))
        params.append(diff_min)

    if diff_max is not None:
        conditions.append(sql.SQL("{} <= %s").format(amount))
        params.append(diff_max)

    lateral = sql.SQL("""
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS qualifying,
                MAX(ABS({difference})) AS max_abs_diff,
                MAX(ABS({percent})) AS max_abs_pct,
                MIN({percent}) AS min_diff_pct,
                MAX({percent}) AS max_diff_pct
            FROM {matches} m
            CROSS JOIN LATERAL (VALUES {prices}) AS c(price)
            WHERE m.client_sku = p.sku
              AND {conditions}
        ) d ON TRUE
    """).format(
        difference=difference,
        percent=percent,
        matches=sql.Identifier(matches_table),
        prices=prices,
        conditions=sql.SQL(" AND ").join(conditions),
    )

    return lateral, params


_SORT_EXPRESSIONS = {
    "name": sql.SQL("p.name"),
    "price": _CLIENT_PRICE_SQL,
    "diff_pln": sql.SQL("d.max_abs_diff"),
    "diff_percent": sql.SQL("d.max_abs_pct"),
}
_GROUP_SORT_EXPRESSIONS = {
    "name": sql.SQL("name"),
    "price": sql.SQL("min_price"),
    "diff_pln": sql.SQL("max_abs_diff"),
    "diff_percent": sql.SQL("max_abs_pct"),
}
_DIFF_SORTS = {"diff_pln", "diff_percent"}

_GROUP_NAME_SQL = sql.SQL("LOWER(BTRIM(p.name))")
_GROUP_MAKER_SQL = sql.SQL("COALESCE(p.manufacturer, '')")


def _add_price_comparison(cur, matches_table: str, products: list[dict], store: str | None = None) -> list[dict]:
    """Dodaje do produktów ceny konkurencji z różnicą (competitor_prices)."""
    if not products or not _table_exists(cur, matches_table):
        return products

    match_columns = _get_columns(cur, matches_table)
    columns = _competitor_price_columns(match_columns, store)

    if "client_sku" not in match_columns or not columns:
        return products

    selected = ["client_sku"] + [
        column
        for price_column, special_column in columns
        for column in (price_column, special_column)
        if column
    ]
    query = sql.SQL("SELECT {} FROM {} WHERE client_sku = ANY(%s)").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in selected),
        sql.Identifier(matches_table),
    )
    cur.execute(query, ([p["sku"] for p in products if p.get("sku")],))
    rows = {row["client_sku"]: dict(row) for row in cur.fetchall()}

    result = []

    for product in products:
        item = dict(product)
        row = rows.get(item.get("sku"))
        client_price = _effective_price(item.get("price_normal"), item.get("price_special"))
        prices = []

        for price_column, special_column in columns if row else []:
            competitor_price = _effective_price(
                row.get(price_column),
                row.get(special_column) if special_column else None,
            )

            if competitor_price is None:
                continue

            difference = competitor_price - client_price if client_price is not None else None
            prices.append({
                "store": price_column[:-len("_price")],
                "price": round(competitor_price, 2),
                "difference": round(difference, 2) if difference is not None else None,
                "difference_percent": (
                    round(difference * 100.0 / client_price, 1)
                    if difference is not None and client_price
                    else None
                ),
            })

        item["competitor_prices"] = prices
        result.append(item)

    return result


def _optional_amount(name: str):
    """Liczba nieujemna z parametru zapytania albo None, gdy pusty."""
    raw = request.args.get(name, "").strip().replace(",", ".")

    if not raw:
        return None

    value = float(raw)

    if value < 0 or value != value or value in (float("inf"), float("-inf")):
        raise ValueError(name)

    return value


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
        # Bez kolumny {sklep}_sku (starsze dopasowania) szukamy oferty po adresie i cenie.
        competitor_sku = match_row.get(f"{store_name}_sku")

        competitor_price_normal = match_price
        special_column = f"{store_name}_price_special"
        has_special_column = special_column in match_row
        competitor_price_special = match_row.get(special_column) if has_special_column else None

        if competitors_exist:
            try:
                extra = None

                if competitor_sku:
                    extra_query = sql.SQL("""
                        SELECT
                            sku,
                            name,
                            image,
                            availability,
                            price_normal,
                            price_special,
                            url,
                            store
                        FROM {}
                        WHERE sku = %s
                    """).format(sql.Identifier(competitors_table))

                    cur.execute(extra_query, (competitor_sku,))
                    extra = next(
                        (
                            row for row in cur.fetchall()
                            if _store_key(row["store"]) == _store_key(store_name)
                        ),
                        None,
                    )

                if not extra and url:
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
                        ORDER BY (price_normal IS NOT DISTINCT FROM %s) DESC
                        LIMIT 1
                    """).format(sql.Identifier(competitors_table))

                    cur.execute(extra_query, (url, _to_float(match_price)))
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
                        ORDER BY (price_normal IS NOT DISTINCT FROM %s) DESC
                        LIMIT 1
                    """).format(sql.Identifier(competitors_table))

                    cur.execute(extra_query, (store_name, name, _to_float(match_price)))
                    extra = cur.fetchone()

                if extra:
                    extra = dict(extra)

                    competitor_sku = extra.get("sku")
                    image = extra.get("image")
                    availability = extra.get("availability")
                    url = url or extra.get("url")

                    if not has_special_column:
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

    price_diff = request.args.get("price_diff", "").strip().lower()
    diff_unit = request.args.get("diff_unit", "pln").strip().lower() or "pln"

    try:
        diff_min = _optional_amount("diff_min")
        diff_max = _optional_amount("diff_max")
    except ValueError:
        return jsonify({"ok": False, "error": "Różnica ceny musi być liczbą nieujemną"}), 400

    if (price_diff and price_diff not in PRICE_DIFF_DIRECTIONS) or diff_unit not in PRICE_DIFF_UNITS:
        return jsonify({"ok": False, "error": "Nieprawidłowy filtr różnicy ceny"}), 400

    price_diff_filter_active = bool(price_diff) or diff_min is not None or diff_max is not None

    group_mode = request.args.get("group", "false").lower() == "true"
    group_name = request.args.get("group_name")
    group_maker = request.args.get("group_maker", "")

    allowed_sort_columns = ["id", "sku", "name", "price_normal", "price_special", "category", "store", "availability"]
    if group_mode:
        if sort_by not in _GROUP_SORT_EXPRESSIONS: sort_by = "name"
    elif sort_by not in _SORT_EXPRESSIONS and sort_by not in allowed_sort_columns:
        sort_by = "id"
    if order not in ["ASC", "DESC"]: order = "ASC"

    page = max(page or 1, 1)
    per_page = min(max(per_page or 20, 1), 100)
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

    if group_name is not None:
        where_parts.append(sql.SQL("{} = LOWER(BTRIM(%s)) AND {} = %s").format(_GROUP_NAME_SQL, _GROUP_MAKER_SQL))
        params.extend([group_name, group_maker or ""])

    conn = None
    cursor = None

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not _table_exists(cursor, products_table):
            return jsonify({"ok": True, "data": [], "pagination": {"total_items": 0, "current_page": page, "per_page": per_page, "total_pages": 1}})

        matched_filter = _get_matched_products_filter(cursor, matches_table) if matched_only else None
        if matched_filter is not None:
            where_parts.append(matched_filter)

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
                store_columns = _competitor_price_columns(match_columns, store)
                store_col = store_columns[0][0] if store_columns else None
                if store_col:
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

        lateral = None
        lateral_params = []
        if (price_diff_filter_active or sort_by in _DIFF_SORTS or group_mode) and _table_exists(cursor, matches_table):
            lateral, lateral_params = _price_difference_lateral(
                _get_columns(cursor, matches_table),
                matches_table,
                store or None,
                price_diff,
                diff_min,
                diff_max,
                diff_unit,
            )

        if price_diff_filter_active:
            where_parts.append(sql.SQL("COALESCE(d.qualifying, 0) > 0") if lateral is not None else sql.SQL("FALSE"))

        if sort_by in _DIFF_SORTS and lateral is None:
            sort_by = "name"

        where_sql = sql.SQL("")
        if where_parts:
            where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_parts)

        from_sql = sql.SQL("FROM {} p {} {}").format(
            sql.Identifier(products_table),
            lateral if lateral is not None else sql.SQL(""),
            where_sql,
        )
        base_params = lateral_params + params

        if group_mode:
            return _grouped_products_response(
                cursor, products_table, from_sql, base_params, lateral is not None,
                matched_filter, sort_by, order, page, per_page,
            )

        count_query = sql.SQL("SELECT COUNT(p.id) AS total {}").format(from_sql)
        cursor.execute(count_query, tuple(base_params))
        total_items = cursor.fetchone()["total"]

        if sort_by in _SORT_EXPRESSIONS:
            order_sql = sql.SQL("{} {} NULLS LAST, p.id").format(_SORT_EXPRESSIONS[sort_by], sql.SQL(order))
        else:
            order_sql = sql.SQL("{} {}").format(sql.Identifier(sort_by), sql.SQL(order))

        query = sql.SQL("""
            SELECT p.id, p.sku, p.name, p.size, p.color, p.manufacturer, p.category, p.price_normal, p.price_special, p.store, p.availability, p.url, p.image, p.description
            {} ORDER BY {} LIMIT %s OFFSET %s
        """).format(from_sql, order_sql)

        cursor.execute(query, tuple(base_params + [per_page, offset]))
        products = [dict(row) for row in cursor.fetchall()]

        products = _add_match_counts(cursor, matches_table, products)
        products = _add_price_comparison(cursor, matches_table, products, store or None)
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


def _rounded(value, digits=2):
    return round(float(value), digits) if value is not None else None


def _grouped_products_response(cursor, products_table, from_sql, params, has_differences,
                               matched_filter, sort_by, order, page, per_page):
    """Kafelki: jeden wiersz na produkt z liczbą wariantów i zakresem cen oraz różnic."""
    differences = (
        sql.SQL("d.max_abs_diff, d.max_abs_pct, d.min_diff_pct, d.max_diff_pct")
        if has_differences
        else sql.SQL("NULL::float AS max_abs_diff, NULL::float AS max_abs_pct, "
                     "NULL::float AS min_diff_pct, NULL::float AS max_diff_pct")
    )
    variants = sql.SQL("""
        SELECT p.id, p.name, p.manufacturer, p.category, p.image,
               {client_price} AS display_price, {differences},
               {group_name} AS group_name, {group_maker} AS group_maker
        {from_sql}
    """).format(
        client_price=_CLIENT_PRICE_SQL,
        differences=differences,
        group_name=_GROUP_NAME_SQL,
        group_maker=_GROUP_MAKER_SQL,
        from_sql=from_sql,
    )

    cursor.execute(
        sql.SQL("SELECT COUNT(*) AS total FROM (SELECT 1 FROM ({}) v GROUP BY group_name, group_maker) g").format(variants),
        tuple(params),
    )
    total_groups = cursor.fetchone()["total"]

    cursor.execute(sql.SQL("""
        WITH v AS ({variants})
        SELECT group_name, group_maker,
               MIN(name) AS name, MIN(manufacturer) AS manufacturer, MIN(category) AS category,
               (ARRAY_AGG(image ORDER BY id) FILTER (WHERE image IS NOT NULL AND image <> ''))[1] AS image,
               MIN(id) AS first_id,
               COUNT(*) AS variants_matching,
               MIN(display_price) AS min_price, MAX(display_price) AS max_price,
               MAX(max_abs_diff) AS max_abs_diff, MAX(max_abs_pct) AS max_abs_pct,
               MIN(min_diff_pct) AS min_diff_pct, MAX(max_diff_pct) AS max_diff_pct
        FROM v
        GROUP BY group_name, group_maker
        ORDER BY {sort} {order} NULLS LAST, group_name, group_maker
        LIMIT %s OFFSET %s
    """).format(
        variants=variants,
        sort=_GROUP_SORT_EXPRESSIONS[sort_by],
        order=sql.SQL(order),
    ), tuple(params + [per_page, (page - 1) * per_page]))
    groups = [dict(row) for row in cursor.fetchall()]

    totals = {}
    if groups:
        conditions = [sql.SQL("{} = ANY(%s)").format(_GROUP_NAME_SQL)]
        if matched_filter is not None:
            conditions.append(matched_filter)
        cursor.execute(sql.SQL("""
            SELECT {group_name} AS group_name, {group_maker} AS group_maker, COUNT(*) AS total
            FROM {table} p
            WHERE {conditions}
            GROUP BY 1, 2
        """).format(
            group_name=_GROUP_NAME_SQL,
            group_maker=_GROUP_MAKER_SQL,
            table=sql.Identifier(products_table),
            conditions=sql.SQL(" AND ").join(conditions),
        ), ([group["group_name"] for group in groups],))
        totals = {(row["group_name"], row["group_maker"]): row["total"] for row in cursor.fetchall()}

    data = []
    for group in groups:
        data.append({
            "group_name": group["group_name"],
            "group_maker": group["group_maker"],
            "name": group["name"],
            "manufacturer": group["manufacturer"],
            "category": group["category"],
            "image": _proxied_image_url(group["image"]),
            "first_id": group["first_id"],
            "variants_matching": group["variants_matching"],
            "variants_total": totals.get((group["group_name"], group["group_maker"]), group["variants_matching"]),
            "min_price": _rounded(group["min_price"]),
            "max_price": _rounded(group["max_price"]),
            "max_abs_diff": _rounded(group["max_abs_diff"]),
            "max_abs_pct": _rounded(group["max_abs_pct"], 1),
            "min_diff_pct": _rounded(group["min_diff_pct"], 1),
            "max_diff_pct": _rounded(group["max_diff_pct"], 1),
        })

    return jsonify({
        "ok": True,
        "mode": "group",
        "data": data,
        "pagination": {
            "total_items": total_groups,
            "current_page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total_groups / per_page) if total_groups > 0 else 1,
        },
    })


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

            # Historia ma 'podpierzyna', dopasowania 'pod_pierzyna'.
            pair_key = (competitor_sku, _store_key(competitor_store))

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
                    ORDER BY valid_from DESC
                """).format(sql.Identifier(history_table)),
                (competitor_sku,)
            )

            rows = [
                row for row in cur.fetchall()
                if _store_key(row["store"]) == pair_key[1]
            ]

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
                    "store": competitor_store,
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

        # Od najnowszej zmiany.
        history_rows.sort(key=lambda item: item["valid_from"] or "", reverse=True)

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