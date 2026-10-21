import scrapy
import re
import os
from datetime import datetime
from ecommerce_price_comparer.items import ProductData
from ecommerce_price_comparer.utilities.scraper_engine import extract_data_using_map

class ReadyScraperSpider(scrapy.Spider):
    name = "oretyparapetybis_spider"

    css_map = {
        "product_name": None,
        "product_id": None,
        "brand": None,
        "color": None,
        "old_price": None,
        "special_price": None,
        "breadcrumbs_wrapper": None,
        "breadcrumb_item": None,
        "specifications_row": None,
        "spec_name": None,
        "spec_value": None,
        "variants_wrapper": None,
        "variant_option": None,
        "variant_price_attribute": None,
        "description": None,
    }

    def start_requests(self):
        default_links_path = os.path.join(os.path.dirname(__file__), 'oretyparapetybis_links.txt')
        links_file_path = getattr(self, 'links_file', default_links_path)

        if os.path.exists(links_file_path):
            with open(links_file_path, 'r', encoding='utf-8') as f:
                urls = f.read().splitlines()
                for url in urls:
                    if url.strip():
                        yield scrapy.Request(url=url.strip(), callback=self.parse)
        else:
            self.logger.error(f"Missing links file: {links_file_path}")

    def parse(self, response):
        if response.status != 200:
            return

        extracted_data = extract_data_using_map(response, self.css_map)
        if not extracted_data:
            return

        yield self.build_product_data(extracted_data)

    def build_product_data(self, raw_data):
        sku = raw_data.get('sku') or raw_data.get('product_id')
        if sku and ('grouped' in str(sku).lower() or 'parent' in str(sku).lower()):
            self.logger.info(f"Rejected parent or grouped page (SKU: {sku})")
            return None 

        specs = raw_data.get('specifications', {})
        def get_spec(specs_dict, keywords):
            for k, v in specs_dict.items():
                if any(keyword.lower() in str(k).lower() for keyword in keywords):
                    return v
            return None

        size_val = get_spec(specs, ['wymiar', 'rozmiar', 'size', 'dimensions']) or raw_data.get('size')
        color_val = get_spec(specs, ['kolor', 'barwa', 'color']) or raw_data.get('color')

        item = ProductData()
        item['sku'] = sku
        item['name'] = raw_data.get('product_name')
        item['size'] = size_val
        item['color'] = color_val
        item['manufacturer'] = raw_data.get('brand')
        item['price_normal'] = raw_data.get('old_price')
        item['price_special'] = raw_data.get('special_price')

        categories = raw_data.get('categories')
        if isinstance(categories, list):
            item['category'] = " > ".join(categories)
        else:
            item['category'] = categories

        item['description'] = raw_data.get('description')
        item['store'] = self.name
        item['date_of_download'] = datetime.now().isoformat()
        item['availability'] = raw_data.get('availability')
        item['image'] = raw_data.get('image_url')
        item['url'] = raw_data.get('url')

        return item
