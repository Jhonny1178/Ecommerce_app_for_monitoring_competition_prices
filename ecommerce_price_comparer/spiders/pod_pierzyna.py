import scrapy
import re
from ecommerce_price_comparer.items import ProductData
from ecommerce_price_comparer.utilities.brands import brand_matches, parse_brands
from ecommerce_price_comparer.utilities.idosell import idosell_sizes, size_prices
from ecommerce_price_comparer.utilities.jsonld import (
    breadcrumb_name,
    find_jsonld_node,
    jsonld_first_url,
    jsonld_offers,
    jsonld_text,
)
from datetime import datetime

PRODUCERS_URL = "https://podpierzyna.com/producers.php"
_COUNTER_PATTERN = re.compile(r"([?&]counter=)(\d+)")


def next_page_url(url):
    """Następna strona listy: zmienia tylko parametr counter."""
    match = _COUNTER_PATTERN.search(url)
    if match:
        page = int(match.group(2)) + 1
        return f"{url[:match.start()]}{match.group(1)}{page}{url[match.end():]}"
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}counter=1"


class PodPierzynaSpider(scrapy.Spider):
    name = "pod_pierzyna"
    allowed_domains = ["podpierzyna.com"]
    start_urls = [
                    "https://podpierzyna.com/do-sypialni/",
                ]
    urls_for_later_use = [
                    "https://podpierzyna.com/do-sypialni/",
                    "https://podpierzyna.com/do-lazienki/",
                    "https://podpierzyna.com/do-domu/",
                    "https://podpierzyna.com/do-kuchni-jadalni/",
                    "https://podpierzyna.com/premium/"]

    def __init__(self, *args, brands=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.brands = parse_brands(brands)

    def initial_requests(self):
        if self.brands:
            return [scrapy.Request(PRODUCERS_URL, callback=self.parse_producers, dont_filter=True)]
        return [scrapy.Request(url, dont_filter=True) for url in self.start_urls]

    async def start(self):
        for request in self.initial_requests():
            yield request

    def parse_producers(self, response):
        matched = []
        for link in response.css("a.producers__icon"):
            title = (link.attrib.get("title") or " ".join(link.css("::text").getall())).strip()
            href = link.attrib.get("href")
            if href and title and brand_matches(title, self.brands) and href not in matched:
                matched.append(href)
                self.logger.info("Marka klienta w sklepie: %s", title)

        if not matched:
            self.logger.warning(
                "Na liście marek %s nie ma marek klienta; pobieram dział sypialni", response.url
            )
            for url in self.start_urls:
                yield response.follow(url, callback=self.parse, dont_filter=True)
            return

        self.logger.info("Pobieram tylko marki klienta: %d z %d", len(matched), len(self.brands))
        for href in matched:
            yield response.follow(href, callback=self.get_product_links)

    def parse(self, response):
        category_links = response.css('div.core-navigation_items  a::attr(href)').getall()
        for link in category_links:
            yield response.follow(link, callback=self.get_product_links)

    def get_product_links(self, response):
        page_links = response.css('section.search.products div.col-6 a.product__icon::attr(href)').getall()
        if page_links:
            yield from response.follow_all(page_links, callback=self.get_data_from_links)
            yield response.follow(next_page_url(response.url), callback=self.get_product_links)

    def get_data_from_links(self, response):
        category = None
        raw_json = response.css('script[type="application/ld+json"]::text').getall()
        color = response.xpath('//text()[contains(., "Dominujący kolor")]/following::text()[1]').get()
        sizes_and_info = idosell_sizes(
            response.css('script.ajaxLoad::text').getall(), self.logger, response.url
        )

        final_json = find_jsonld_node(raw_json, "Product")
        if not final_json:
            self.logger.warning("Brak danych produktu (JSON-LD Product) na stronie %s", response.url)
            return

        breadcrumb = find_jsonld_node(raw_json, "BreadcrumbList") or {}
        for element in breadcrumb.get("itemListElement") or []:
            if isinstance(element, dict) and element.get("position") == 2:
                category = breadcrumb_name(element)

        offers = [offer for offer in jsonld_offers(final_json) if offer.get('@type') == "Offer"]
        availability = offers[0].get('availability') if offers else None
        url = (offers[0].get('url') if offers else None) or response.url
        name = jsonld_text(final_json.get("name"))
        description = final_json.get('description')
        brand = jsonld_text(final_json.get("brand"))
        image = jsonld_first_url(final_json.get("image"))

        for key, prices in sizes_and_info.items():
            size = prices.get('description') or None
            price_old, price_special = size_prices(prices)

            sku = prices.get('code_extern')

            product_data = ProductData()
            product_data['color'] = color
            product_data['size'] = size
            product_data['name'] = name
            product_data['description'] = description
            product_data['sku'] = sku
            product_data['price_normal'] = price_old
            product_data['price_special'] = price_special
            product_data['category'] = category
            product_data['manufacturer'] = brand
            product_data['url'] = url
            product_data['store'] = 'podpierzyna'
            product_data['date_of_download'] = datetime.now().isoformat()
            product_data['image'] = image
            product_data['availability'] = availability

            yield product_data


