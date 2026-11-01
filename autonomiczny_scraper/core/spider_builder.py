import json
import re
import os

def generate_spider_file(json_map_path="selectors_map.json", output_file="autonomiczny_scraper/spiders/ready_scraper.py", store_name="store", links=None):
    try:
        with open(json_map_path, "r", encoding="utf-8") as f:
            css_map = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found {json_map_path}")
        return

    formatted_map = "{\n"
    for k, v in css_map.items():
        val = repr(str(v)) if v is not None else 'None'
        formatted_map += f'        {repr(str(k))}: {val},\n'
    formatted_map += "    }"

    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', store_name).lower()

    if links:
        links_path = os.path.join(os.path.dirname(output_file), f"{safe_name}_links.txt")
        with open(links_path, "w", encoding="utf-8") as f:
            if isinstance(links, (list, set, tuple)):
                for url in links:
                    f.write(f"{url}\n")
            elif isinstance(links, str):
                try:
                    import ast
                    parsed = ast.literal_eval(links)
                    if isinstance(parsed, (list, set, tuple)):
                        for url in parsed:
                            f.write(f"{url}\n")
                    else:
                        f.write(f"{links}\n")
                except:
                    f.write(f"{links}\n")

    spider_code = f"""import scrapy
import re
import os
from datetime import datetime
from ecommerce_price_comparer.items import ProductData
from ecommerce_price_comparer.utilities.scraper_engine import extract_data_using_map

class ReadyScraperSpider(scrapy.Spider):
    name = "{safe_name}_spider"

    css_map = {formatted_map}

    def start_requests(self):
        default_links_path = os.path.join(os.path.dirname(__file__), '{safe_name}_links.txt')
        links_file_path = getattr(self, 'links_file', default_links_path)

        if os.path.exists(links_file_path):
            with open(links_file_path, 'r', encoding='utf-8') as f:
                urls = f.read().splitlines()
                for url in urls:
                    if url.strip():
                        yield scrapy.Request(url=url.strip(), callback=self.parse)
        else:
            self.logger.error(f"Missing links file: {{links_file_path}}")

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
            self.logger.info(f"Rejected parent or grouped page (SKU: {{sku}})")
            return None 

        specs = raw_data.get('specifications', {{}})
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
"""

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(spider_code)
    print(f"Generated spider: {output_file}")