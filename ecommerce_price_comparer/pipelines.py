from datetime import datetime
from itemadapter import ItemAdapter
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv
from utilities.data_utility import DataCleaner
load_dotenv()


def offer_price(row):
    """Cena promocyjna albo regularna; oferta bez ceny przegrywa."""
    price_normal, price_special = row[6], row[7]
    if price_special is not None and price_special > 0:
        return price_special
    return price_normal if price_normal is not None else float("inf")


class EcommercePriceComparerPipeline:
    def __init__(self):
        self.to_lowercase_list = ['color','category','availability','manufacturer','name','size']
        self.to_normalize_prize =['price_normal','price_special']
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        #striping all field besides description
        for category in adapter.field_names():
            if category != 'description':
                value = adapter.get(category)
                value = DataCleaner.to_strip(value,spider)
                adapter[category] = value
        for category in self.to_lowercase_list:
            value = adapter.get(category)
            value = DataCleaner.to_lowercase(value,spider)
            adapter[category] = value
        for price in self.to_normalize_prize:
            value = adapter.get(price)
            value = DataCleaner.clean_price(value)
            adapter[price] = value

        adapter['availability'] = DataCleaner.standardize_availability_link(adapter.get('availability'))
        adapter['name'] = DataCleaner.standardize_name(adapter.get('name'),adapter.get('color'),adapter.get('size'),adapter.get('manufacturer'))
        adapter['size'] = DataCleaner.standardize_size(adapter.get('size'))
        adapter['color'] = DataCleaner.standardize_color(adapter.get('color'))
        adapter['sku'] = DataCleaner.sku_normalize(adapter.get('name'),adapter.get('color'),adapter.get('size'),adapter.get('manufacturer'))
        adapter['description'] = DataCleaner.clean_description(adapter.get('description'))

        if adapter.get('category'): adapter['category'] = adapter['category'][:100]
        if adapter.get('manufacturer'): adapter['manufacturer'] = adapter['manufacturer'][:50]
        if adapter.get('size'): adapter['size'] = adapter['size'][:50]
        if adapter.get('color'): adapter['color'] = adapter['color'][:50]
        if adapter.get('store'): adapter['store'] = adapter['store'][:50]
        if adapter.get('availability'): adapter['availability'] = adapter['availability'][:50]

        return item
class SaveToPostgresSQLPipeline:
    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv("APP_DB_HOST"),
            port=os.getenv("APP_DB_PORT", "5432"),
            user=os.getenv("APP_DB_USER"),
            password=os.getenv("APP_DB_PASSWORD"),
            database=os.getenv("APP_DB_NAME"),
        )
        self.cur = self.conn.cursor()
        # Oferty zapisywane do bazy po zakończeniu pająka.
        self.offers = {}
        self.duplicate_skus = set()
        self.batch_size = 100

    def open_spider(self, spider):
        target_table = getattr(spider, 'target_table', None)
        prefix = getattr(spider, 'store_prefix', 'default')

        if target_table:
            self.table_name = target_table
        else:
            self.table_name = f"{prefix}_competitors"

        self.history_table_name = f"{self.table_name}_history"

        try:
            # Pakujemy tworzenie struktur w jeden try/except
            self.cur.execute(f"""
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
            """)

            self.cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.history_table_name} (
                    id SERIAL PRIMARY KEY,
                    sku TEXT,
                    store VARCHAR(50),
                    price_normal_old FLOAT,
                    price_normal_new FLOAT,
                    price_special_old FLOAT,
                    price_special_new FLOAT,
                    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_to TIMESTAMP,
                    is_current BOOLEAN DEFAULT TRUE
                );
            """)

            self.cur.execute("""
                CREATE OR REPLACE FUNCTION log_price_changes()
                RETURNS TRIGGER AS $$
                DECLARE
                    history_table TEXT := TG_ARGV[0];
                    query TEXT;
                BEGIN
                    IF (OLD.price_normal IS DISTINCT FROM NEW.price_normal) OR 
                       (OLD.price_special IS DISTINCT FROM NEW.price_special) THEN
                        query := format('UPDATE %I SET valid_to = CURRENT_TIMESTAMP, is_current = FALSE WHERE sku = $1 AND store = $2 AND is_current = TRUE', history_table);
                        EXECUTE query USING OLD.sku, OLD.store;
                        query := format('INSERT INTO %I (sku, store, price_normal_old, price_normal_new, price_special_old, price_special_new, valid_from, is_current) VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP, TRUE)', history_table);
                        EXECUTE query USING OLD.sku, OLD.store, OLD.price_normal, NEW.price_normal, OLD.price_special, NEW.price_special;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)

            self.cur.execute(f"DROP TRIGGER IF EXISTS trigger_price_history ON {self.table_name};")

            # Data pierwszego pojawienia się oferty (raport zmian).
            self.cur.execute(f"ALTER TABLE {self.table_name} ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP;")
            self.cur.execute(
                f"UPDATE {self.table_name} SET first_seen = date_of_download WHERE first_seen IS NULL;"
            )

            self.cur.execute(f"""
                CREATE TRIGGER trigger_price_history
                AFTER UPDATE ON {self.table_name}
                FOR EACH ROW
                EXECUTE FUNCTION log_price_changes('{self.history_table_name}');
            """)
            self.conn.commit()

        except psycopg2.errors.UniqueViolation:
            # Jeśli inny pająk właśnie założył tabelę mikrosekundę wcześniej, wycofujemy minitransakcję i lecimy dalej
            self.conn.rollback()
            spider.logger.info(f"[PIPELINE] Tabela {self.table_name} została już utworzona przez inny proces.")

    def process_item(self, item, spider):
        if not item.get('sku'):
            return item

        # Krotka musi idealnie pasować do kolejności kolumn w zapytaniu INSERT
        tuple_data = (
            item.get('sku'), item.get('name'), item.get('size'),
            item.get('color'), item.get('manufacturer'), item.get('category'),
            item.get('price_normal'), item.get('price_special'), item.get('store'),
            item.get('availability'),
            # Po niej DAG rozpoznaje oferty odświeżone w przebiegu.
            item.get('date_of_download') or datetime.now().isoformat(),
            item.get('url'), item.get('image'), item.get('description')
        )

        # Jeden zapis na SKU w przebiegu (zostaje tańsza oferta), inaczej trigger zapisze fałszywą zmianę ceny.
        key = (item.get('sku'), item.get('store'))
        saved = self.offers.get(key)

        if saved is None:
            self.offers[key] = tuple_data
        else:
            self.duplicate_skus.add(key)
            if offer_price(tuple_data) < offer_price(saved):
                self.offers[key] = tuple_data

        return item

    def _save_offers(self, spider):
        # Dopisany first_seen; nie ma go w ON CONFLICT DO UPDATE, więc się nie zmienia.
        rows = [row + (row[10],) for row in self.offers.values()]
        if not rows:
            return

        # Używamy UPSERT: Wstawiamy nowy produkt LUB aktualizujemy jeśli SKU już istnieje.
        # Właśnie ta aktualizacja (UPDATE) uruchomi naszego Triggera historycznego!
        query = f"""
            INSERT INTO {self.table_name} (
                sku, name, size, color, manufacturer, category,
                price_normal, price_special, store, availability,
                date_of_download, url, image, description, first_seen
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (sku, store) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, {self.table_name}.name),
                size = COALESCE(EXCLUDED.size, {self.table_name}.size),
                color = COALESCE(EXCLUDED.color, {self.table_name}.color),
                manufacturer = COALESCE(
                    EXCLUDED.manufacturer,
                    {self.table_name}.manufacturer
                ),
                category = COALESCE(
                    EXCLUDED.category,
                    {self.table_name}.category
                ),
                price_normal = EXCLUDED.price_normal,
                price_special = EXCLUDED.price_special,
                availability = COALESCE(
                    EXCLUDED.availability,
                    {self.table_name}.availability
                ),
                date_of_download = EXCLUDED.date_of_download,
                url = COALESCE(EXCLUDED.url, {self.table_name}.url),
                image = COALESCE(EXCLUDED.image, {self.table_name}.image),
                description = COALESCE(
                    EXCLUDED.description,
                    {self.table_name}.description
                );
        """
        saved_count = 0

        for start in range(0, len(rows), self.batch_size):
            batch = rows[start:start + self.batch_size]
            try:
                psycopg2.extras.execute_batch(self.cur, query, batch)
                self.conn.commit()
                saved_count += len(batch)
            except Exception as e:
                self.conn.rollback()
                spider.logger.error(f"[DATABASE ERROR] Błąd zapisu paczki: {e}")

        spider.logger.info(
            f"[DATABASE] Zapisano {saved_count} z {len(rows)} ofert w tabeli {self.table_name}."
        )

    def close_spider(self, spider):
        try:
            self._save_offers(spider)

            if self.duplicate_skus:
                examples = ", ".join(sorted(sku for sku, _ in self.duplicate_skus)[:5])
                spider.logger.warning(
                    f"[DATABASE] Ten sam SKU w kilku ofertach: {len(self.duplicate_skus)} "
                    f"(zapisano ofertę z najniższą ceną), np. {examples}"
                )
        finally:
            self.cur.close()
            self.conn.close()


