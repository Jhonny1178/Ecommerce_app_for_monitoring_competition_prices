"""Ceny rozmiarów klienta pobrane jednorazowo ze strony sklepu.

Feed podaje jedną cenę (albo cenę promocyjną) dla wszystkich rozmiarów. Pobrane ceny są w
{prefix}_size_prices i ingest je stosuje, dopóki cena w feedzie się nie zmieni.

Kroki (airflow-worker, /opt/airflow/dags):
  1. python -m processors.client_size_prices plan nasz_klient /tmp/size_targets.json
  2. cd ecommerce_price_comparer && scrapy crawl client_size_prices -a targets=/tmp/size_targets.json -O /tmp/size_prices.jsonl
  3. python -m processors.client_size_prices apply nasz_klient /tmp/size_targets.json /tmp/size_prices.jsonl
"""
import json
import re
import sys
from collections import defaultdict
from contextlib import closing

PRICE_TOLERANCE = 0.005
_FEED_LINK_SUFFIX = re.compile(r"\.feed\d+\.html$")


def size_prices_table_for(products_table):
    """nasz_klient_products -> nasz_klient_size_prices."""
    base = products_table[: -len("_products")] if products_table.endswith("_products") else products_table
    return f"{base}_size_prices"


def _price(value):
    try:
        price = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def same_price(first, second):
    if first is None or second is None:
        return first is None and second is None
    return abs(float(first) - float(second)) < PRICE_TOLERANCE


def product_page_url(url):
    """Adres strony produktu bez dopisku feedu (".feed10000.html")."""
    return _FEED_LINK_SUFFIX.sub(".html", url or "")


def uniform_price_groups(rows, already_fetched=()):
    """Produkty z kilkoma rozmiarami i jedną ceną albo jedną ceną promocyjną; pomija już pobrane."""
    already_fetched = set(already_fetched)
    groups = defaultdict(list)
    for row in rows:
        product_id, separator, _ = str(row["sku"]).partition("-")
        if separator and product_id.isdigit() and row.get("url"):
            groups[product_id].append(row)

    targets = []
    for product_id, items in sorted(groups.items(), key=lambda pair: int(pair[0])):
        if any(item["sku"] in already_fetched for item in items):
            continue
        sizes = {item["size"] for item in items}
        normal_prices = {item["price_normal"] for item in items}
        special_prices = {item["price_special"] for item in items}
        same_special = None not in special_prices and len(special_prices) == 1
        if len(sizes) < 2 or not (len(normal_prices) == 1 or same_special):
            continue
        targets.append({
            "product_id": product_id,
            "url": product_page_url(items[0]["url"]),
            "skus": {
                item["sku"]: {
                    "size": item["size"],
                    "price_normal": item["price_normal"],
                    "price_special": item["price_special"],
                }
                for item in items
            },
        })
    return targets


def size_price_rows(targets, scraped):
    """Wiersze do zapisania (tylko rozmiary z feedu) i raport."""
    by_product = {target["product_id"]: target for target in targets}
    rows = {}
    report = defaultdict(int)

    for item in scraped:
        target = by_product.get(str(item.get("product_id")))
        feed = target["skus"].get(item.get("sku")) if target else None
        if feed is None:
            report["rozmiary_spoza_feedu"] += 1
            continue
        price_normal = _price(item.get("price_normal"))
        if price_normal is None:
            report["bez_ceny"] += 1
            continue
        rows[item["sku"]] = {
            "sku": item["sku"],
            "size": item.get("size"),
            "price_normal": price_normal,
            "price_special": _price(item.get("price_special")),
            "feed_price_normal": feed["price_normal"],
            "feed_price_special": feed["price_special"],
            "url": item.get("url"),
        }

    fetched = {row["sku"].partition("-")[0] for row in rows.values()}
    report["produkty"] = len(targets)
    report["produkty_z_cenami"] = len(fetched)
    report["produkty_bez_cen"] = len(targets) - len(fetched)
    report["rozmiary_z_cena"] = len(rows)
    report["rozmiary_feedu_bez_ceny_ze_strony"] = sum(
        1 for target in targets for sku in target["skus"]
        if target["product_id"] in fetched and sku not in rows
    )
    report["rozmiary_z_inna_cena_niz_feed"] = sum(
        1 for row in rows.values() if not same_price(row["price_normal"], row["feed_price_normal"])
    )
    report["rozmiary_z_inna_cena_promocyjna_niz_feed"] = sum(
        1 for row in rows.values() if not same_price(row["price_special"], row["feed_price_special"])
    )
    report["produkty_gdzie_cena_feedu_jest_cena_rozmiaru"] = sum(
        1 for product_id in fetched
        if any(
            same_price(row["price_normal"], row["feed_price_normal"])
            for row in rows.values() if row["sku"].partition("-")[0] == product_id
        )
    )
    return list(rows.values()), dict(report)


def apply_size_price(item, size_prices):
    """Wstawia cenę rozmiaru ze strony; zwraca "applied", "stale" (feed się zmienił) albo None."""
    saved = size_prices.get(item.get("sku"))
    if not saved:
        return None
    if not (
        same_price(item.get("price_normal"), saved["feed_price_normal"])
        and same_price(item.get("price_special"), saved["feed_price_special"])
    ):
        return "stale"
    item["price_normal"] = saved["price_normal"]
    item["price_special"] = saved["price_special"]
    return "applied"


def load_size_prices(cur, products_table):
    """Zapisane ceny rozmiarów ({sku: wiersz}); pusty słownik bez tabeli."""
    from psycopg2 import sql

    table = size_prices_table_for(products_table)
    with cur.connection.cursor() as plain:
        plain.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if plain.fetchone()[0] is None:
            return {}
        plain.execute(sql.SQL(
            "SELECT sku, price_normal, price_special, feed_price_normal, feed_price_special FROM {}"
        ).format(sql.Identifier(table)))
        return {
            sku: {
                "price_normal": price_normal,
                "price_special": price_special,
                "feed_price_normal": feed_price_normal,
                "feed_price_special": feed_price_special,
            }
            for sku, price_normal, price_special, feed_price_normal, feed_price_special in plain.fetchall()
        }


def _connect():
    import os
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("APP_DB_HOST"),
        port=os.getenv("APP_DB_PORT", "5432"),
        user=os.getenv("APP_DB_USER"),
        password=os.getenv("APP_DB_PASSWORD"),
        database=os.getenv("APP_DB_NAME"),
    )


def plan(prefix, targets_path):
    """Lista stron do pobrania z ostatniego wczytania feedu."""
    import psycopg2.extras
    from psycopg2 import sql

    products_table = f"{prefix}_products"
    with closing(_connect()) as conn, conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql.SQL("""
            SELECT sku, size, price_normal, price_special, url
            FROM {table}
            WHERE date_of_download >= (SELECT max(date_of_download)::date FROM {table})
        """).format(table=sql.Identifier(products_table)))
        rows = cur.fetchall()
        fetched = set(load_size_prices(cur, products_table))

    targets = uniform_price_groups(rows, already_fetched=fetched)
    with open(targets_path, "w", encoding="utf-8") as file:
        json.dump(targets, file, ensure_ascii=False)
    print(f"Produkty z ostatniego feedu: {len(rows)} rozmiarów, już pobrane ze strony: {len(fetched)}")
    print(f"Do pobrania: {len(targets)} stron ({sum(len(t['skus']) for t in targets)} rozmiarów) -> {targets_path}")


def apply(prefix, targets_path, scraped_path):
    """Zapisuje ceny rozmiarów i od razu poprawia ceny w tabeli produktów."""
    import psycopg2.extras
    from psycopg2 import sql

    with open(targets_path, encoding="utf-8") as file:
        targets = json.load(file)
    with open(scraped_path, encoding="utf-8") as file:
        scraped = [json.loads(line) for line in file if line.strip()]

    rows, report = size_price_rows(targets, scraped)
    for key, value in report.items():
        print(f"{key}: {value}")
    if not rows:
        print("Brak cen do zapisania.")
        return

    products_table = f"{prefix}_products"
    table = size_prices_table_for(products_table)
    with closing(_connect()) as conn, conn, conn.cursor() as cur:
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                sku TEXT PRIMARY KEY,
                size TEXT,
                price_normal DOUBLE PRECISION NOT NULL,
                price_special DOUBLE PRECISION,
                feed_price_normal DOUBLE PRECISION,
                feed_price_special DOUBLE PRECISION,
                url TEXT,
                fetched_at TIMESTAMP NOT NULL DEFAULT now()
            )
        """).format(sql.Identifier(table)))
        psycopg2.extras.execute_batch(cur, sql.SQL("""
            INSERT INTO {} (sku, size, price_normal, price_special, feed_price_normal, feed_price_special, url)
            VALUES (%(sku)s, %(size)s, %(price_normal)s, %(price_special)s,
                    %(feed_price_normal)s, %(feed_price_special)s, %(url)s)
            ON CONFLICT (sku) DO UPDATE SET
                size = EXCLUDED.size,
                price_normal = EXCLUDED.price_normal,
                price_special = EXCLUDED.price_special,
                feed_price_normal = EXCLUDED.feed_price_normal,
                feed_price_special = EXCLUDED.feed_price_special,
                url = EXCLUDED.url,
                fetched_at = now()
        """).format(sql.Identifier(table)), rows)
        cur.execute(sql.SQL("""
            UPDATE {products} p
            SET price_normal = s.price_normal,
                price_special = s.price_special
            FROM {sizes} s
            WHERE p.sku = s.sku
              AND abs(p.price_normal - s.feed_price_normal) < %s
              AND p.price_special IS NOT DISTINCT FROM s.feed_price_special
        """).format(products=sql.Identifier(products_table), sizes=sql.Identifier(table)), (PRICE_TOLERANCE,))
        updated = cur.rowcount
    print(f"Zapisano w {table}: {len(rows)} rozmiarów, poprawiono ceny w {products_table}: {updated}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if len(sys.argv) > 2 and not re.fullmatch(r"[a-z0-9_]+", sys.argv[2]):
        print("Prefix klienta może zawierać tylko małe litery, cyfry i _")
        sys.exit(1)
    if command == "plan" and len(sys.argv) == 4:
        plan(sys.argv[2], sys.argv[3])
    elif command == "apply" and len(sys.argv) == 5:
        apply(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(__doc__)
        sys.exit(1)
