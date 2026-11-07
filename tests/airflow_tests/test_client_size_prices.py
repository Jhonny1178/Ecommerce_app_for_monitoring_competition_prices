"""Ceny rozmiarów klienta ze strony sklepu (processors/client_size_prices.py, spider client_size_prices, utilities/idosell.py)."""
import json

from scrapy.http import HtmlResponse, Request

from ecommerce_price_comparer.spiders.klient.client_size_prices import ClientSizePricesSpider
from ecommerce_price_comparer.utilities.idosell import idosell_sizes, size_prices
from processors.client_size_prices import (
    apply_size_price,
    product_page_url,
    size_price_rows,
    size_prices_table_for,
    uniform_price_groups,
)

URL = "https://lambfield.com/product-pol-15133-Posciel-zakardowa-Elegante-Muriel-2406-4-pastel-green.html"


def size(description, value, normalprice=None):
    price = {"value": value, "price_formatted": f"{value} zł"}
    if normalprice:
        price["normalprice"] = normalprice
    return {"description": description, "price": price, "code_extern": "EL"}


SIZES = {
    "P": size("135x200 + 1*80x80", "662.00"),
    "R": size("155x200 + 1*80x80", "708.00"),
    "T": size("200x220 + 2*80x80", "1250.00", normalprice="1392.00"),
}


def product_page(sizes=SIZES):
    data = json.dumps({"cena_raty": "1", "sizes": sizes}).replace('"', "'")
    body = f"""<html><body>
      <script class="ajaxLoad">var other = {{'a': 1}};</script>
      <script class="ajaxLoad">app_shop.vars.product = {data};</script>
    </body></html>"""
    return HtmlResponse(url=URL, body=body.encode("utf-8"), encoding="utf-8", request=Request(URL))


class TestIdosellSizes:
    def test_reads_sizes_and_prices(self):
        sizes = idosell_sizes(product_page().css("script.ajaxLoad::text").getall())

        assert list(sizes) == ["P", "R", "T"]
        assert size_prices(sizes["P"]) == ("662.00", None)
        assert size_prices(sizes["T"]) == ("1392.00", "1250.00")

    def test_page_without_sizes(self):
        assert idosell_sizes(["var x = {'a': 1};"]) == {}


class TestSpider:
    def test_one_item_per_size_with_feed_sku(self, tmp_path):
        targets = tmp_path / "targets.json"
        targets.write_text(json.dumps([{"product_id": "15133", "url": URL, "skus": {}}]))
        spider = ClientSizePricesSpider(targets=str(targets))

        items = list(spider.parse_product(product_page(), product_id="15133"))

        assert spider.allowed_domains == ["lambfield.com"]
        assert [(i["sku"], i["price_normal"], i["price_special"]) for i in items] == [
            ("15133-P", "662.00", None),
            ("15133-R", "708.00", None),
            ("15133-T", "1392.00", "1250.00"),
        ]


def feed_row(sku, size_, price, special=None, url=URL.replace(".html", ".feed10000.html")):
    return {"sku": sku, "size": size_, "price_normal": price, "price_special": special, "url": url}


class TestUniformPriceGroups:
    def test_only_products_with_one_price_for_many_sizes(self):
        rows = [
            feed_row("15133-P", "135x200", 662.0), feed_row("15133-R", "155x200", 662.0),
            feed_row("200-P", "135x200", 500.0), feed_row("200-R", "155x200", 600.0),
            feed_row("300-U", "uniwersalny", 99.0),
        ]

        targets = uniform_price_groups(rows)

        assert [t["product_id"] for t in targets] == ["15133"]
        assert targets[0]["url"] == URL
        assert set(targets[0]["skus"]) == {"15133-P", "15133-R"}

    def test_same_sale_price_for_all_sizes(self):
        rows = [
            feed_row("3527-32", "135x200", 4345.0, special=3258.75),
            feed_row("3527-33", "155x200", 4635.0, special=3258.75),
            feed_row("3527-36", "200x220", 6649.0, special=3258.75),
            feed_row("400-A", "135x200", 100.0, special=80.0),
            feed_row("400-B", "155x200", 120.0, special=96.0),
            feed_row("500-A", "135x200", 100.0, special=80.0),
            feed_row("500-B", "155x200", 120.0),
        ]

        assert [t["product_id"] for t in uniform_price_groups(rows)] == ["3527"]

    def test_already_fetched_products_are_skipped(self):
        rows = [feed_row("15133-P", "135x200", 662.0), feed_row("15133-R", "155x200", 662.0)]

        assert uniform_price_groups(rows, already_fetched={"15133-R"}) == []

    def test_feed_link_suffix_is_removed(self):
        assert product_page_url("https://a.pl/product-pol-1-x.feed10000.html") == "https://a.pl/product-pol-1-x.html"
        assert product_page_url("https://a.pl/product-pol-1-x.html") == "https://a.pl/product-pol-1-x.html"

    def test_table_name(self):
        assert size_prices_table_for("nasz_klient_products") == "nasz_klient_size_prices"


class TestSizePriceRows:
    def test_rows_and_report(self):
        targets = uniform_price_groups([
            feed_row("15133-P", "135x200", 662.0), feed_row("15133-R", "155x200", 662.0),
            feed_row("15133-T", "200x220", 662.0),
            feed_row("999-P", "135x200", 100.0), feed_row("999-R", "155x200", 100.0),
        ])
        scraped = [
            {"sku": "15133-P", "product_id": "15133", "price_normal": "662.00", "price_special": None},
            {"sku": "15133-R", "product_id": "15133", "price_normal": "708.00", "price_special": None},
            {"sku": "15133-X", "product_id": "15133", "price_normal": "1.00", "price_special": None},
            {"sku": "15133-T", "product_id": "15133", "price_normal": "", "price_special": None},
        ]

        rows, report = size_price_rows(targets, scraped)

        assert {row["sku"]: row["price_normal"] for row in rows} == {"15133-P": 662.0, "15133-R": 708.0}
        assert rows[1]["feed_price_normal"] == 662.0
        assert report["produkty"] == 2
        assert report["produkty_bez_cen"] == 1
        assert report["rozmiary_spoza_feedu"] == 1
        assert report["bez_ceny"] == 1
        assert report["rozmiary_feedu_bez_ceny_ze_strony"] == 1
        assert report["rozmiary_z_inna_cena_niz_feed"] == 1
        assert report["produkty_gdzie_cena_feedu_jest_cena_rozmiaru"] == 1


class TestApplyOnIngest:
    SAVED = {"15133-R": {"price_normal": 708.0, "price_special": None,
                         "feed_price_normal": 662.0, "feed_price_special": None}}

    def test_size_price_replaces_feed_price(self):
        item = {"sku": "15133-R", "price_normal": 662.0, "price_special": None}

        assert apply_size_price(item, self.SAVED) == "applied"
        assert item["price_normal"] == 708.0

    def test_feed_price_changed_so_feed_wins(self):
        item = {"sku": "15133-R", "price_normal": 700.0, "price_special": None}

        assert apply_size_price(item, self.SAVED) == "stale"
        assert item["price_normal"] == 700.0

    def test_other_products_untouched(self):
        item = {"sku": "1-P", "price_normal": 10.0, "price_special": None}

        assert apply_size_price(item, self.SAVED) is None
        assert apply_size_price(item, {}) is None
        assert item["price_normal"] == 10.0
