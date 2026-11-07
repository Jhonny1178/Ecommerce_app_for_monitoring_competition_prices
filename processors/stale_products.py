"""Usuwanie produktów klienta, których nie ma już w sklepie (sprawdzone na stronie).

Kroki (airflow-worker, /opt/airflow/dags):
  1. python -m processors.stale_products plan nasz_klient /tmp/stale_targets.json
  2. cd ecommerce_price_comparer && scrapy crawl client_product_check -a targets=/tmp/stale_targets.json -O /tmp/stale_check.jsonl
  3. python -m processors.stale_products apply nasz_klient /tmp/stale_targets.json /tmp/stale_check.jsonl /tmp/usuniete.csv
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from contextlib import closing

from processors.client_size_prices import _connect, product_page_url, size_prices_table_for

GONE_STATUSES = {"product_gone", "size_gone"}


def _latest_feed_condition():
    """Warunek SQL: wiersza nie było w ostatnim wczytaniu feedu."""
    return "date_of_download < (SELECT max(date_of_download)::date FROM {table})"


def stale_targets(rows):
    """Strony produktów do sprawdzenia: {product_id, url, skus} z wierszy spoza feedu."""
    groups = defaultdict(lambda: {"url": None, "skus": []})
    for row in rows:
        product_id, separator, _ = str(row["sku"]).partition("-")
        if not (separator and product_id.isdigit() and row.get("url")):
            continue
        group = groups[product_id]
        group["url"] = group["url"] or product_page_url(row["url"])
        group["skus"].append(row["sku"])
    return [
        {"product_id": product_id, "url": group["url"], "skus": sorted(group["skus"])}
        for product_id, group in sorted(groups.items(), key=lambda pair: int(pair[0]))
    ]


def skus_to_delete(targets, checked):
    """SKU sprawdzone jako usunięte i podsumowanie statusów."""
    known = {sku for target in targets for sku in target["skus"]}
    statuses = {item["sku"]: item["status"] for item in checked if item.get("sku") in known}
    report = Counter(statuses.values())
    report["bez_wyniku"] = len(known) - len(statuses)
    return sorted(sku for sku, status in statuses.items() if status in GONE_STATUSES), dict(report)


def plan(prefix, targets_path):
    import psycopg2.extras
    from psycopg2 import sql

    products_table = f"{prefix}_products"
    with closing(_connect()) as conn, conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql.SQL(
            "SELECT sku, url FROM {table} WHERE " + _latest_feed_condition()
        ).format(table=sql.Identifier(products_table)))
        rows = cur.fetchall()

    targets = stale_targets(rows)
    with open(targets_path, "w", encoding="utf-8") as file:
        json.dump(targets, file, ensure_ascii=False)
    print(f"Produkty spoza ostatniego feedu: {len(rows)} SKU, {len(targets)} stron -> {targets_path}")


def apply(prefix, targets_path, checked_path, backup_path):
    import psycopg2.extras
    from psycopg2 import sql

    with open(targets_path, encoding="utf-8") as file:
        targets = json.load(file)
    with open(checked_path, encoding="utf-8") as file:
        checked = [json.loads(line) for line in file if line.strip()]

    skus, report = skus_to_delete(targets, checked)
    for key, value in sorted(report.items()):
        print(f"{key}: {value}")
    if not skus:
        print("Nic do usunięcia.")
        return

    products_table = f"{prefix}_products"
    with closing(_connect()) as conn, conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        condition = sql.SQL("sku = ANY(%s) AND " + _latest_feed_condition()).format(
            table=sql.Identifier(products_table)
        )
        cur.execute(sql.SQL("SELECT * FROM {} WHERE {}").format(sql.Identifier(products_table), condition), (skus,))
        rows = cur.fetchall()
        if rows:
            with open(backup_path, "w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        cur.execute(sql.SQL("DELETE FROM {} WHERE {}").format(sql.Identifier(products_table), condition), (skus,))
        deleted = cur.rowcount

        size_table = size_prices_table_for(products_table)
        cur.execute("SELECT to_regclass(%s)", (f"public.{size_table}",))
        if cur.fetchone()["to_regclass"] is not None:
            cur.execute(sql.SQL("DELETE FROM {} WHERE sku = ANY(%s)").format(sql.Identifier(size_table)), (skus,))

    print(f"Usunięto z {products_table}: {deleted} SKU (kopia: {backup_path})")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if len(sys.argv) > 2 and not re.fullmatch(r"[a-z0-9_]+", sys.argv[2]):
        print("Prefix klienta może zawierać tylko małe litery, cyfry i _")
        sys.exit(1)
    if command == "plan" and len(sys.argv) == 4:
        plan(sys.argv[2], sys.argv[3])
    elif command == "apply" and len(sys.argv) == 6:
        apply(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__)
        sys.exit(1)
