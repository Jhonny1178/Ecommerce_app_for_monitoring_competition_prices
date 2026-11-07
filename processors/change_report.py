"""Raport zmian po aktualizacji: zmiany cen oraz nowe i wycofane oferty konkurencji w dopasowaniach."""
from datetime import datetime

import psycopg2.extras
from psycopg2 import sql

PRICE_TOLERANCE = 0.005


def store_key(name):
    """Nazwa sklepu tylko z liter i cyfr (podpierzyna = pod_pierzyna)."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN z pandas


def effective_price(price_normal, price_special):
    """Cena promocyjna, jeśli jest, inaczej regularna (jak w webapp)."""
    special = to_float(price_special)
    if special is not None and special > 0:
        return special
    return to_float(price_normal)


def price_changed(old, new):
    if old is None or new is None:
        return (old is None) != (new is None)
    return abs(old - new) > PRICE_TOLERANCE


def difference(competitor_price, client_price):
    """Cena konkurencji minus cena klienta (jak w aplikacji)."""
    if competitor_price is None or client_price is None:
        return None
    return round(competitor_price - client_price, 2)


def change_item(change_type, before, after):
    base = after or before
    item = {
        "type": change_type,
        "client_sku": base["client_sku"],
        "client_name": base.get("client_name"),
        "size": base.get("size"),
        "store": base["store"],
        "competitor_sku": base["competitor_sku"],
        "competitor_name": base.get("competitor_name"),
        "competitor_url": base.get("competitor_url"),
        "old_client_price": before["client_price"] if before else None,
        "new_client_price": after["client_price"] if after else None,
        "old_competitor_price": before["competitor_price"] if before else None,
        "new_competitor_price": after["competitor_price"] if after else None,
    }
    item["old_difference"] = difference(item["old_competitor_price"], item["old_client_price"])
    item["new_difference"] = difference(item["new_competitor_price"], item["new_client_price"])

    if change_type == "price":
        competitor = price_changed(item["old_competitor_price"], item["new_competitor_price"])
        client = price_changed(item["old_client_price"], item["new_client_price"])
        item["changed_by"] = "both" if competitor and client else "competitor" if competitor else "client"

    return item


def compare_states(previous, current, store_offers, new_offers):
    """Zmiany między stanami; nowa i wycofana tylko, gdy oferta pojawiła się lub zniknęła ze sklepu."""
    items = []

    for key in sorted(set(previous) | set(current)):
        before = previous.get(key)
        after = current.get(key)

        if before and after and before["competitor_sku"] == after["competitor_sku"]:
            if (
                price_changed(before["competitor_price"], after["competitor_price"])
                or price_changed(before["client_price"], after["client_price"])
            ):
                items.append(change_item("price", before, after))
            continue

        if before and (store_key(before["store"]), before["competitor_sku"]) not in store_offers:
            items.append(change_item("removed", before, None))

        if after and (store_key(after["store"]), after["competitor_sku"]) in new_offers:
            items.append(change_item("new", None, after))

    return items


def table_exists(cur, table_name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (table_name,))
    return cur.fetchone()["exists"]


def table_columns(cur, table_name):
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table_name,),
    )
    return {row["column_name"] for row in cur.fetchall()}


def load_current_state(cur, prefix, competitor_stores):
    """Dopasowane pary; cena klienta z tabeli produktów (z promocją)."""
    matches_table = f"{prefix}_matches"
    products_table = f"{prefix}_products"

    if not table_exists(cur, matches_table):
        return {}

    columns = table_columns(cur, matches_table)
    stores = [store for store in competitor_stores if f"{store}_sku" in columns]
    if not stores:
        return {}

    cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(matches_table)))
    rows = cur.fetchall()

    client_prices = {}
    if table_exists(cur, products_table):
        cur.execute(
            sql.SQL("SELECT sku, price_normal, price_special FROM {} WHERE sku = ANY(%s)").format(
                sql.Identifier(products_table)
            ),
            ([row["client_sku"] for row in rows],),
        )
        for product in cur.fetchall():
            client_prices.setdefault(
                product["sku"], effective_price(product["price_normal"], product["price_special"])
            )

    state = {}
    for row in rows:
        for store in stores:
            competitor_sku = row.get(f"{store}_sku")
            if not competitor_sku:
                continue
            state[(row["client_sku"], store)] = {
                "client_sku": row["client_sku"],
                "client_name": row.get("client_name"),
                "size": row.get("size"),
                "store": store,
                "competitor_sku": competitor_sku,
                "competitor_name": row.get(f"{store}_name"),
                "competitor_url": row.get(f"{store}_url"),
                "client_price": client_prices.get(row["client_sku"], to_float(row.get("client_price"))),
                "competitor_price": effective_price(
                    row.get(f"{store}_price"), row.get(f"{store}_price_special")
                ),
            }
    return state


def load_offers(cur, prefix, since):
    """Oferty w sklepach konkurencji oraz te, które pojawiły się po `since`."""
    competitors_table = f"{prefix}_competitors"
    if not table_exists(cur, competitors_table):
        return set(), set()

    has_first_seen = "first_seen" in table_columns(cur, competitors_table)
    cur.execute(
        sql.SQL("SELECT store, sku, {} AS first_seen FROM {}").format(
            sql.Identifier("first_seen") if has_first_seen else sql.SQL("NULL::timestamp"),
            sql.Identifier(competitors_table),
        )
    )

    store_offers, new_offers = set(), set()
    for row in cur.fetchall():
        key = (store_key(row["store"]), row["sku"])
        store_offers.add(key)
        if row["first_seen"] is not None and row["first_seen"] > since:
            new_offers.add(key)
    return store_offers, new_offers


STATE_COLUMNS = (
    "client_sku", "store", "competitor_sku", "client_name", "size",
    "competitor_name", "competitor_url", "client_price", "competitor_price",
)


def ensure_tables(cur, prefix):
    cur.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {reports} (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            checked_at TIMESTAMP NOT NULL,
            pipeline_run_id INTEGER,
            baseline BOOLEAN NOT NULL DEFAULT FALSE,
            unchecked_stores JSONB NOT NULL DEFAULT '[]'::jsonb,
            items JSONB NOT NULL DEFAULT '[]'::jsonb
        );
        CREATE TABLE IF NOT EXISTS {state} (
            client_sku TEXT NOT NULL,
            store TEXT NOT NULL,
            competitor_sku TEXT,
            client_name TEXT,
            size TEXT,
            competitor_name TEXT,
            competitor_url TEXT,
            client_price DOUBLE PRECISION,
            competitor_price DOUBLE PRECISION,
            PRIMARY KEY (client_sku, store)
        );
    """).format(
        reports=sql.Identifier(f"{prefix}_change_reports"),
        state=sql.Identifier(f"{prefix}_match_state"),
    ))


def update_change_report(conn, prefix, competitor_stores, pipeline_run_id=None, unchecked_stores=(), now=None):
    """Zapisuje raport i nowy stan dopasowań; `now` z zegara pipeline'u, jak date_of_download."""
    now = now or datetime.now()
    reports_table = sql.Identifier(f"{prefix}_change_reports")
    state_table = sql.Identifier(f"{prefix}_match_state")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_tables(cur, prefix)

            cur.execute(sql.SQL("SELECT checked_at FROM {} ORDER BY id DESC LIMIT 1").format(reports_table))
            last_report = cur.fetchone()

            current = load_current_state(cur, prefix, competitor_stores)

            if last_report is None:
                items = []
                baseline = True
            else:
                cur.execute(sql.SQL("SELECT * FROM {}").format(state_table))
                previous = {(row["client_sku"], row["store"]): dict(row) for row in cur.fetchall()}
                store_offers, new_offers = load_offers(cur, prefix, last_report["checked_at"])
                items = compare_states(previous, current, store_offers, new_offers)
                baseline = False

            cur.execute(
                sql.SQL("""
                    INSERT INTO {} (checked_at, pipeline_run_id, baseline, unchecked_stores, items)
                    VALUES (%s, %s, %s, %s, %s)
                """).format(reports_table),
                (
                    now,
                    pipeline_run_id,
                    baseline,
                    psycopg2.extras.Json(sorted(unchecked_stores)),
                    psycopg2.extras.Json(items),
                ),
            )

            cur.execute(sql.SQL("DELETE FROM {}").format(state_table))
            psycopg2.extras.execute_values(
                cur,
                sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
                    state_table, sql.SQL(", ").join(map(sql.Identifier, STATE_COLUMNS))
                ).as_string(conn),
                [tuple(pair[column] for column in STATE_COLUMNS) for pair in current.values()],
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    summary = {"baseline": baseline, "pairs": len(current)}
    for change_type in ("price", "new", "removed"):
        summary[change_type] = sum(1 for item in items if item["type"] == change_type)
    return summary
