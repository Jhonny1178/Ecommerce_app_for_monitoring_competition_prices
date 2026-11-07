"""Jednorazowe pobranie cen rozmiarów ze strony sklepu klienta (IdoSell).

Uruchamiany ręcznie (processors/client_size_prices.py), zapisuje tylko do pliku.
"""
import json
from urllib.parse import urlsplit

import scrapy

from ecommerce_price_comparer.utilities.idosell import idosell_sizes, size_prices


class ClientSizePricesSpider(scrapy.Spider):
    name = "client_size_prices"
    custom_settings = {
        "ITEM_PIPELINES": {},
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
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
                errback=self.page_failed,
                cb_kwargs={"product_id": str(target["product_id"])},
            )

    def parse_product(self, response, product_id):
        sizes = idosell_sizes(response.css("script.ajaxLoad::text").getall(), self.logger, response.url)
        if not sizes:
            self.logger.warning("Brak cen rozmiarów na stronie %s", response.url)
            return

        for code, info in sizes.items():
            price_normal, price_special = size_prices(info)
            yield {
                "sku": f"{product_id}-{code}",
                "product_id": product_id,
                "size": info.get("description"),
                "price_normal": price_normal,
                "price_special": price_special,
                "url": response.url,
            }

    def page_failed(self, failure):
        self.logger.warning("Nie udało się pobrać strony %s: %r", failure.request.url, failure.value)
