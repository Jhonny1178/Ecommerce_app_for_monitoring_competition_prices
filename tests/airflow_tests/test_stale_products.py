"""Usuwanie produktów klienta, których nie ma już w sklepie (processors/stale_products.py, spider client_product_check)."""
import json

from scrapy.http import HtmlResponse, Request

from ecommerce_price_comparer.spiders.klient.client_product_check import ClientProductCheckSpider, page_state
from processors.stale_products import skus_to_delete, stale_targets

PRODUCT = "https://lambfield.com/product-pol-4519-Dywanik-Rhomtuft-Prestige-45-001-White.html"


def page(url, body="", status=200):
    return HtmlResponse(url=url, status=status, body=body.encode("utf-8"), encoding="utf-8", request=Request(PRODUCT))


def sizes_script(codes):
    data = json.dumps({"cena_raty": "1", "sizes": {c: {"description": c, "price": {"value": "1"}} for c in codes}})
    return f'<script class="ajaxLoad">x = {data.replace(chr(34), chr(39))};</script>'


class TestPageState:
    def test_removed_product(self):
        assert page_state(page("https://lambfield.com/noproduct.php?reason=product&product=4519"), "4519") == "product_gone"
        assert page_state(page("https://lambfield.com/"), "4519") == "product_gone"
        assert page_state(page(PRODUCT, status=404), "4519") == "product_gone"
        assert page_state(page("https://lambfield.com/product-pol-9999-Inny.html"), "4519") == "product_gone"

    def test_existing_product(self):
        assert page_state(page(PRODUCT), "4519") == "sizes"


class TestSpider:
    def spider(self, tmp_path):
        targets = tmp_path / "t.json"
        targets.write_text(json.dumps([{"product_id": "4519", "url": PRODUCT, "skus": ["4519-58"]}]))
        return ClientProductCheckSpider(targets=str(targets))

    def test_status_per_size(self, tmp_path):
        spider = self.spider(tmp_path)
        response = page(PRODUCT, body=sizes_script(["58", "59"]))

        items = list(spider.parse_product(response, product_id="4519", skus=["4519-58", "4519-67"]))

        assert {i["sku"]: i["status"] for i in items} == {"4519-58": "present", "4519-67": "size_gone"}

    def test_redirects_to_same_page_are_not_skipped(self, tmp_path):
        import asyncio

        spider = self.spider(tmp_path)

        async def collect():
            return [request async for request in spider.start()]

        requests = asyncio.run(collect())

        assert all(request.dont_filter for request in requests)

    def test_removed_and_unknown(self, tmp_path):
        spider = self.spider(tmp_path)

        gone = list(spider.parse_product(page("https://lambfield.com/noproduct.php?product=4519"), "4519", ["4519-58"]))
        unknown = list(spider.parse_product(page(PRODUCT, body="<html></html>"), "4519", ["4519-58"]))

        assert gone[0]["status"] == "product_gone"
        assert unknown[0]["status"] == "unknown"


def test_targets_group_sizes_by_product_page():
    rows = [
        {"sku": "4519-58", "url": PRODUCT.replace(".html", ".feed10000.html")},
        {"sku": "4519-59", "url": PRODUCT.replace(".html", ".feed10000.html")},
        {"sku": "zly-sku", "url": PRODUCT},
    ]

    assert stale_targets(rows) == [{"product_id": "4519", "url": PRODUCT, "skus": ["4519-58", "4519-59"]}]


def test_only_checked_gone_skus_are_deleted():
    targets = [{"product_id": "1", "url": "u", "skus": ["1-A", "1-B", "1-C", "1-D", "1-E"]}]
    checked = [
        {"sku": "1-A", "status": "product_gone"},
        {"sku": "1-B", "status": "size_gone"},
        {"sku": "1-C", "status": "present"},
        {"sku": "1-D", "status": "unknown"},
        {"sku": "9-X", "status": "product_gone"},
    ]

    skus, report = skus_to_delete(targets, checked)

    assert skus == ["1-A", "1-B"]
    assert report["bez_wyniku"] == 1
    assert report["present"] == 1
