"""Sprawdza na stronie sklepu klienta, czy produkty spoza feedu nadal istnieją.

Status SKU: present, size_gone, product_gone albo unknown. Uruchamiany ręcznie
(processors/stale_products.py), zapisuje tylko do pliku.
"""
import json
import re
from urllib.parse import urlsplit

import scrapy

from ecommerce_price_comparer.utilities.idosell import idosell_sizes

_PRODUCT_PATH = re.compile(r"/product-[a-z]+-(\d+)-")


def page_state(response, product_id):
    """Stan strony produktu: "product_gone", "sizes" albo "unknown"."""
    if response.status in (404, 410):
        return "product_gone"
    if "noproduct" in response.url:
        return "product_gone"
    match = _PRODUCT_PATH.search(urlsplit(response.url).path)
    if not match or match.group(1) != str(product_id):
        return "product_gone"
    return "sizes"


class ClientProductCheckSpider(scrapy.Spider):
    name = "client_product_check"
    custom_settings = {
        "ITEM_PIPELINES": {},
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "HTTPERROR_ALLOWED_CODES": [404, 410],
    }

    def __init__(self, *args, targets=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not targets:
            raise ValueError("Podaj plik z listą produktów: -a targets=plik.json")
        with open(targets, encoding="utf-8") as file:
            self.targets = json.load(file)
        self.allowed_domains = sorted({
            urlsplit(target["url"]).hostname
            for target in self.targets
            if urlsplit(target["url"]).hostname
        })

    async def start(self):
        for target in self.targets:
            yield scrapy.Request(
                target["url"],
                callback=self.parse_product,
                cb_kwargs={"product_id": str(target["product_id"]), "skus": target["skus"]},
                dont_filter=True,
            )

    def parse_product(self, response, product_id, skus):
        state = page_state(response, product_id)
        sizes = {}
        if state == "sizes":
            sizes = idosell_sizes(response.css("script.ajaxLoad::text").getall(), self.logger, response.url)

        for sku in skus:
            size_code = sku.partition("-")[2]
            if state == "product_gone":
                status = "product_gone"
            elif not sizes:
                status = "unknown"
            elif size_code in sizes:
                status = "present"
            else:
                status = "size_gone"
            yield {"sku": sku, "product_id": product_id, "status": status, "final_url": response.url}
