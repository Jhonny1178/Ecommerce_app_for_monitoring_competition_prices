import scrapy
import re
from datetime import datetime
from scrapy.spidermiddlewares.httperror import HttpError
from ecommerce_price_comparer.items import ProductData
from ecommerce_price_comparer.utilities.brands import (
    GENERIC_BRAND_WORDS,
    brand_variants,
    parse_brands,
)
from ecommerce_price_comparer.utilities.jsonld import (
    find_jsonld_node,
    jsonld_first_url,
    jsonld_offers,
    jsonld_text,
)

BRAND_URL = "https://www.calvado.com/kolekcja/{slug}.html"
SIZE_PATTERN = re.compile(r'\d+(?:\s*[/-]\s*\d+)?\s*x\s*\d+(?:\s*[/-]\s*\d+)?')


class CalavadoSpider(scrapy.Spider):
    name = "Calavado"
    allowed_domains = ["calvado.com"]
    start_urls = [
        "https://www.calvado.com/sypialnia/posciel.html",
        "https://www.calvado.com/sypialnia.html",
        "https://www.calvado.com/lazienka.html",
        "https://www.calvado.com/kuchnia.html",
        "https://www.calvado.com/jadalnia.html",
        "https://www.calvado.com/salon-dekoracje.html",
        "https://www.calvado.com/ogrod-i-taras.html",
        "https://www.calvado.com/concept-design.html"
    ]

    def __init__(self, *args, brands=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.brands = parse_brands(brands)

    def brand_start_urls(self):
        """Adresy stron marek klienta w wariantach nazwy ("möve" -> move, moeve); brak marki daje 404."""
        urls = []
        for brand in self.brands:
            for words in brand_variants(brand):
                specific = tuple(word for word in words if word not in GENERIC_BRAND_WORDS)
                for candidate in (words, specific):
                    url = BRAND_URL.format(slug="_".join(candidate))
                    if candidate and url not in urls:
                        urls.append(url)
        return urls

    def initial_requests(self):
        if not self.brands:
            return [scrapy.Request(url, dont_filter=True) for url in self.start_urls]
        urls = self.brand_start_urls()
        self.logger.info("Pobieram tylko marki klienta: %d marek, %d adresów", len(self.brands), len(urls))
        return [
            scrapy.Request(url, callback=self.parse, errback=self.brand_page_failed, dont_filter=True)
            for url in urls
        ]

    async def start(self):
        for request in self.initial_requests():
            yield request

    def brand_page_failed(self, failure):
        if failure.check(HttpError) and failure.value.response.status == 404:
            self.logger.info("Sklep nie ma strony marki %s", failure.request.url)
        else:
            self.logger.warning("Nie udało się pobrać strony marki %s: %r", failure.request.url, failure.value)

    def parse(self, response):
        all_products_links = response.css('li.item.last a.product-image.lasto::attr(href)')
        for link in all_products_links:
            yield response.follow(link,callback=self.parse)
        product_link = response.css('h2.product-name a::attr(href)')
        yield from response.follow_all(product_link, callback=self.parse_json)
        next_page = response.css('a.next::attr(href)').get()
        if next_page is not None:
            yield response.follow(next_page, callback=self.parse)
    def parse_json(self, response):
        raw_blocks = response.css('script[type="application/ld+json"]::text').getall()
        product_json = find_jsonld_node(raw_blocks, "Product")
        if not product_json:
            self.logger.warning("Brak danych produktu (JSON-LD Product) na stronie %s", response.url)
            return

        offers = jsonld_offers(product_json)
        availability = offers[0].get('availability') if offers else None
        category = product_json.get('category') or (offers[0].get('category') if offers else None)
        manufacturer = jsonld_text(product_json.get('manufacturer')) or jsonld_text(product_json.get('brand'))
        description = product_json.get('description')
        image = jsonld_first_url(product_json.get('image'))
        base_sku = product_json.get('sku')
        if base_sku:
            base_sku = base_sku.replace('grouped', "").replace("-", "").strip()
        color = response.xpath('//text()[contains(., "Kolor")]/following::text()[1]').get()
        product_url = product_json.get('url') or response.url

        variants = []
        for row in response.css('#super-product-table tr'):
            name = row.css('td a::text').get()
            if not name:
                continue
            price_old, price_special = self.prices_from(row)
            url = row.css('td a::attr(href)').get() or product_url
            variants.append((name, price_old, price_special, url))

        if not variants:
            name = jsonld_text(product_json.get('name')) or response.css('.product-name h1::text').get()
            price_box = response.css('.product-shop .price-box') or response.css('.product-essential .price-box')
            price_old, price_special = self.prices_from(price_box[0]) if price_box else (None, None)
            if price_old is None and offers:
                price_old = offers[0].get('price')
            if name and price_old is not None:
                variants.append((name.strip(), price_old, price_special, product_url))
            else:
                self.logger.warning("Brak nazwy lub ceny produktu na stronie %s", response.url)

        for name, price_old, price_special, url in variants:
            size = SIZE_PATTERN.search(name)
            size = size.group(0) if size else None

            sku = None
            if base_sku:
                sku = f"{base_sku}-{size}" if size else base_sku
                sku = sku.replace(" ", "-").strip()

            product_data = ProductData()
            product_data['color'] = color
            product_data['size'] = size
            product_data['name'] = name
            product_data['description'] = description
            product_data['sku'] = sku
            product_data['price_normal'] = price_old
            product_data['price_special'] = price_special
            product_data['category'] = category
            product_data['manufacturer'] = manufacturer
            product_data['url'] = url
            product_data['store']= 'calavado'
            product_data['date_of_download'] = datetime.now().isoformat()
            product_data['image'] = image
            product_data['availability'] = availability

            yield product_data

    @staticmethod
    def prices_from(selector):
        """Cena regularna i promocyjna z wiersza wariantu albo ramki ceny."""
        price_old = selector.css('p.old-price span.price::text').get()
        if price_old is None:
            price_old = selector.css('span.regular-price span.price::text').get()
        price_special = selector.css('p.special-price span.price::text').get()
        return price_old, price_special


