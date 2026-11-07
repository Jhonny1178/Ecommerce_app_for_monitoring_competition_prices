import os
import requests
import xml.etree.ElementTree as ET
import psycopg2
import psycopg2.extras
import pandas as pd
import io
from dotenv import load_dotenv
from datetime import datetime
from ecommerce_price_comparer.utilities.data_utility import DataCleaner
from ecommerce_price_comparer.items import ProductData
from ecommerce_price_comparer.utilities.url_safety import safe_get
from processors.client_size_prices import apply_size_price, load_size_prices
load_dotenv()

class DataExtractor:
    def __init__(self, config: dict):
        self.config = config
        self.store_name = config.get("store_name")
        self.source_type = config.get("source_type")  # 'url' lub 'local'
        self.source_path = config.get("source_path")
        self.file_format = config.get("file_format")  # 'xml', 'csv', 'xlsx'
        self.mapping = config.get("field_mapping", {})

    def extract(self):
        print(f"Rozpoczynam pobieranie danych dla: {self.store_name} z {self.source_type.upper()}")

        file_object = None
        try:
            if self.source_type == 'url':
                headers = {'User-Agent': 'Mozilla/5.0'}
                if self.file_format in ['xlsx', 'excel']:
                    response = safe_get(self.source_path, headers=headers, timeout=60)
                    response.raise_for_status()
                    file_object = io.BytesIO(response.content)
                else:
                    # XML i CSV szybszym strumieniem z mniejszym ramem excel potrzbuej calego pliku
                    response = safe_get(self.source_path, headers=headers, stream=True,timeout=60)
                    response.raise_for_status()
                    response.raw.decode_content = True
                    file_object = response.raw
            elif self.source_type == 'local':
                file_object = open(self.source_path, 'rb')
            else:
                raise ValueError(f"Nieznany typ źródła: {self.source_type}")

            if self.file_format == 'xml':
                yield from self.parse_xml(file_object)
            elif self.file_format == 'csv':
                yield from self.parse_csv(file_object)
            elif self.file_format in ['xlsx', 'excel']:
                yield from self.parse_excel(file_object)
            else:
                raise NotImplementedError(f"Format {self.file_format} nie jest obsługiwany.")

        finally:
            if self.source_type == 'local' and file_object:
                file_object.close()
            print(f"Zakończono czytanie pliku dla {self.store_name}.")

    def parse_xml(self, file_object):
        events = ET.iterparse(file_object, events=("end",))
        for event, elem in events:
            if elem.tag.split('}')[-1] == "item":
                raw_data = self.create_empty_record()
                for child in elem:
                    child_tag = child.tag.split('}')[-1]
                    if child_tag in self.mapping:
                        internal_key = self.mapping[child_tag]
                        if internal_key == "category" and raw_data.get("category") is not None:
                            continue
                        raw_data[internal_key] = child.text

                yield raw_data
                elem.clear()

    def parse_csv(self, file_object):
        chunk_iterator = pd.read_csv(file_object, chunksize=1000)
        for chunk in chunk_iterator:
            for row in chunk.to_dict(orient='records'):
                yield self.map_pandas_row(row)

    def parse_excel(self, file_object):
        df = pd.read_excel(file_object)
        for row in df.to_dict(orient='records'):
            yield self.map_pandas_row(row)

    def map_pandas_row(self, row):
        raw_data = self.create_empty_record()
        for client_column_name, cell_value in row.items():
            if client_column_name in self.mapping and pd.notna(cell_value):
                raw_data[self.mapping[client_column_name]] = str(cell_value)
        return raw_data

    def create_empty_record(self):
        product_data = ProductData()
        product_data['store'] = self.store_name
        for internal_key in self.mapping.values():
            product_data[internal_key] = None
        return product_data

class DataLoader:
    def __init__(self, table_name: str):
        self.table_name = table_name
        self.conn = psycopg2.connect(
            host= os.getenv("APP_DB_HOST"),
            user= os.getenv("APP_DB_USER"),
            password = os.getenv("APP_DB_PASSWORD"),
            database = os.getenv("APP_DB_NAME"),
        )
        self.cur = self.conn.cursor()
        self._create_table()
        self.size_prices = load_size_prices(self.cur, self.table_name)

    def _create_table(self):
        query = f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
            id SERIAL PRIMARY KEY,
            sku TEXT,
            name TEXT,
            size VARCHAR(50),
            color VARCHAR(50),
            manufacturer VARCHAR(50),
            category VARCHAR(100),
            price_normal FLOAT,
            price_special FLOAT,
            store VARCHAR(50),
            availability VARCHAR(50),
            date_of_download TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            url TEXT,
            image TEXT,
            description TEXT,
            UNIQUE (sku, store)
            );
        """
        self.cur.execute(query)
        self.conn.commit()
        print(f"Baza danych gotowa. Tabela: {self.table_name}")

    def load(self, data_stream, batch_size=500):
        try:
            print(f"Rozpoczynam czyszczenie i zapis do tabeli...")
            batch = []
            total_processed = 0
            size_price_status = {"applied": 0, "stale": 0}

            for raw_item in data_stream:
                cleaned_item = self.apply_data_cleaner(raw_item)
                status = apply_size_price(cleaned_item, self.size_prices)
                if status:
                    size_price_status[status] += 1

                if cleaned_item.get('sku'):
                    tuple_data = (
                        cleaned_item.get('sku'), cleaned_item.get('name'), cleaned_item.get('size'),
                        cleaned_item.get('color'), cleaned_item.get('manufacturer'), cleaned_item.get('category'),
                        cleaned_item.get('price_normal'), cleaned_item.get('price_special'), cleaned_item.get('store'),
                        cleaned_item.get('availability'), cleaned_item.get('date_of_download'), cleaned_item.get('url'),
                        cleaned_item.get('image'), cleaned_item.get('description')
                    )
                    batch.append(tuple_data)
                    total_processed += 1
                if len(batch) >= batch_size:
                    self._save_batch(batch)
                    batch.clear()

            if batch:
                self._save_batch(batch)

            print(f"Zakończono! Zapisano łącznie {total_processed} produktów.")
            if self.size_prices:
                print(
                    f"[INGEST] Ceny rozmiarów ze strony sklepu: zastosowane {size_price_status['applied']}, "
                    f"pominięte {size_price_status['stale']} (cena w feedzie zmieniła się od pobrania)"
                )

        finally:
            # Zawsze zamykaj po zakończeniu pracy
            self.cur.close()
            self.conn.close()
            print("Połączenie z bazą zamknięte poprawnie.")

    def apply_data_cleaner(self, raw_item):
        to_lowercase_list = ['color', 'availability', 'manufacturer', 'name', 'size']
        to_normalize_prize = ['price_normal', 'price_special']

        clean_item = dict(raw_item)
        for category in clean_item.keys():
            if category != 'description':
                value = clean_item.get(category)
                if value is not None:
                    clean_item[category] = DataCleaner.to_strip(value)
        for category in to_lowercase_list:
            value = clean_item.get(category)
            if value is not None:
                clean_item[category] = DataCleaner.to_lowercase(value)
        for price in to_normalize_prize:
            value = clean_item.get(price)
            if value is not None:
                clean_item[price] = DataCleaner.clean_price(value)

        clean_item['availability'] = DataCleaner.standardize_availability_link(clean_item.get('availability'))
        clean_item['name'] = DataCleaner.standardize_name(clean_item.get('name'), clean_item.get('color'),
                                                          clean_item.get('size'), clean_item.get('manufacturer'))
        clean_item['size'] = DataCleaner.standardize_size(clean_item.get('size'))
        clean_item['color'] = DataCleaner.standardize_color(clean_item.get('color'))
        original_sku = clean_item.get('sku')

        if original_sku is not None and str(original_sku).strip():
            clean_item['sku'] = DataCleaner.to_strip(str(original_sku))
        else:
            clean_item['sku'] = DataCleaner.sku_normalize(
                clean_item.get('name'),
                clean_item.get('color'),
                clean_item.get('size'),
                clean_item.get('manufacturer')
            )
        clean_item['description'] = DataCleaner.clean_description(clean_item.get('description'))
        clean_item['category'] = DataCleaner.clean_category(clean_item.get('category'))
        clean_item['date_of_download'] = datetime.now().isoformat()

        return clean_item

    def _save_batch(self, batch_data):
        query = f"""
            INSERT INTO {self.table_name} (
                sku, name, size, color, manufacturer, category, 
                price_normal, price_special, store, availability, 
                date_of_download, url, image, description
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (sku,store) DO UPDATE SET
                price_normal = EXCLUDED.price_normal,
                price_special = EXCLUDED.price_special,
                availability = EXCLUDED.availability,
                date_of_download = EXCLUDED.date_of_download;
        """
        try:
            psycopg2.extras.execute_batch(self.cur, query, batch_data)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"Błąd zapisu paczki: {e}")