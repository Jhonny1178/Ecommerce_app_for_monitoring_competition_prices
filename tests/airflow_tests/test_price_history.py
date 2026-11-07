"""Historia cen konkurencji: zapis ofert (pipelines.py), trigger historii i usuwanie nieaktualnych ofert (Dag.py)."""
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

DAGS_DIR = (
    "/opt/airflow/dags"
    if os.path.exists("/opt/airflow/dags/Dag.py")
    else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "airflow", "dags"))
)
# pipelines.py importuje "utilities" z katalogu projektu scrapy.
SCRAPY_DIR = (
    os.path.join(DAGS_DIR, "ecommerce_price_comparer")
    if os.path.exists(os.path.join(DAGS_DIR, "ecommerce_price_comparer"))
    else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ecommerce_price_comparer"))
)
for path in (DAGS_DIR, SCRAPY_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import Dag
from ecommerce_price_comparer import pipelines
from ecommerce_price_comparer.pipelines import SaveToPostgresSQLPipeline, offer_price

YESTERDAY = (datetime.now() - timedelta(days=1)).isoformat()


def offer(sku, price, store="podpierzyna", special=None, date=None):
    return {
        "sku": sku, "name": sku, "size": "60x100", "color": None, "manufacturer": "graccioza",
        "category": None, "price_normal": price, "price_special": special, "store": store,
        "availability": None, "date_of_download": date, "url": f"https://{store}.test/{sku}",
        "image": None, "description": None,
    }


def fake_spider(table="test_competitors"):
    return SimpleNamespace(target_table=table, store_prefix="test", logger=MagicMock())


class TestOneOfferPerSku:
    """Każdy SKU trafia do bazy raz na przebieg, niezależnie od kolejności stron."""

    @pytest.fixture
    def pipeline(self):
        with patch.object(pipelines.psycopg2, "connect"):
            pipeline = SaveToPostgresSQLPipeline()
        pipeline.table_name = "test_competitors"
        return pipeline

    @staticmethod
    def saved_rows(pipeline, spider):
        with patch.object(pipelines.psycopg2.extras, "execute_batch") as execute_batch:
            pipeline.close_spider(spider)
        return [row for call in execute_batch.call_args_list for row in call.args[2]]

    @pytest.mark.parametrize("prices", [(2171.9, 2261.9), (2261.9, 2171.9)])
    def test_cheapest_of_duplicate_offers_is_saved(self, pipeline, prices):
        """Rozmiary „60x100” i „60x100 falowany” dają ten sam SKU po normalizacji rozmiaru."""
        spider = fake_spider()
        for price in prices:
            pipeline.process_item(offer("dywanik-60x100", price), spider)
        pipeline.process_item(offer("dywanik-70x120", 2823.9), spider)

        rows = self.saved_rows(pipeline, spider)

        assert [(row[0], row[6]) for row in rows] == [("dywanik-60x100", 2171.9), ("dywanik-70x120", 2823.9)]
        warning = spider.logger.warning.call_args[0][0]
        assert "Ten sam SKU w kilku ofertach: 1" in warning
        assert "dywanik-60x100" in warning

    def test_same_sku_in_other_store_is_a_separate_offer(self, pipeline):
        spider = fake_spider()
        pipeline.process_item(offer("koc", 100.0, store="calavado"), spider)
        pipeline.process_item(offer("koc", 120.0, store="podpierzyna"), spider)

        rows = self.saved_rows(pipeline, spider)

        assert sorted((row[8], row[6]) for row in rows) == [("calavado", 100.0), ("podpierzyna", 120.0)]
        spider.logger.warning.assert_not_called()

    def test_offers_are_saved_in_batches(self, pipeline):
        spider = fake_spider()
        for index in range(250):
            pipeline.process_item(offer(f"sku-{index}", 10.0), spider)

        with patch.object(pipelines.psycopg2.extras, "execute_batch") as execute_batch:
            pipeline.close_spider(spider)

        assert [len(call.args[2]) for call in execute_batch.call_args_list] == [100, 100, 50]
        assert pipeline.conn.commit.call_count == 3

    def test_missing_download_date_is_filled(self, pipeline):
        """Po dacie pobrania DAG rozpoznaje oferty odświeżone w przebiegu."""
        spider = fake_spider()
        pipeline.process_item(offer("koc", 100.0), spider)

        rows = self.saved_rows(pipeline, spider)

        assert datetime.fromisoformat(rows[0][10]) > datetime.now() - timedelta(minutes=1)

    def test_offer_price_prefers_special_and_puts_missing_last(self):
        def row(normal, special):
            data = offer("x", normal, special=special)
            return (None,) * 6 + (data["price_normal"], data["price_special"])

        assert offer_price(row(100.0, 80.0)) == 80.0
        assert offer_price(row(100.0, None)) == 100.0
        assert offer_price(row(100.0, 0)) == 100.0
        assert offer_price(row(None, None)) == float("inf")


class TestStaleOffersAfterScraper:
    """DAG usuwa nieodświeżone oferty tylko po pełnym przebiegu scrapera."""

    @pytest.fixture(autouse=True)
    def mock_db_helpers(self, monkeypatch):
        monkeypatch.setattr(Dag, "create_live_task_run", lambda **kwargs: 123)
        monkeypatch.setattr(Dag, "update_live_task_run", lambda **kwargs: None)
        monkeypatch.setattr(Dag, "save_task_run", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "mark_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "clear_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "count_rows", lambda table: 10)
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: [])
        self.error_log = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", self.error_log)
        self.remove = MagicMock(return_value=3)
        monkeypatch.setattr(Dag, "remove_stale_offers", self.remove)

    def run_spider(self, client, finish_reason, errors=0):
        stats = [
            "[scrapy.statscollectors] INFO: Dumping Scrapy stats:",
            f"{{'finish_reason': '{finish_reason}',",
            " 'item_scraped_count': 10,",
        ]
        if errors:
            stats.append(f" 'log_count/ERROR': {errors},")
        process = MagicMock()
        process.stdout = iter(stats + ["[scrapy.core.engine] INFO: Spider closed"])
        process.wait.return_value = 0
        with patch("subprocess.Popen", return_value=process):
            return Dag.run_spider_for_client(client=client, spider_name="pod_pierzyna", pipeline_run_id=1)

    def test_finished_scraper_removes_offers_it_did_not_refresh(self, sample_client):
        before = datetime.now()

        result = self.run_spider(sample_client, "finished")

        assert result["success"] is True
        table, spider_name, refreshed_since = self.remove.call_args.args
        assert (table, spider_name) == ("nasz_klient_competitors", "pod_pierzyna")
        assert before <= refreshed_since <= datetime.now()

    @pytest.mark.parametrize("finish_reason", ["closespider_timeout", "closespider_itemcount", "shutdown"])
    def test_partial_scrape_keeps_old_offers(self, sample_client, finish_reason):
        result = self.run_spider(sample_client, finish_reason)

        assert result["success"] is True
        self.remove.assert_not_called()

    def test_errors_in_log_keep_old_offers(self, sample_client):
        self.run_spider(sample_client, "finished", errors=2)

        self.remove.assert_not_called()

    def test_cleanup_failure_is_only_a_warning(self, sample_client):
        self.remove.side_effect = RuntimeError("baza niedostępna")

        result = self.run_spider(sample_client, "finished")

        assert result["success"] is True
        kwargs = self.error_log.call_args.kwargs
        assert kwargs["error_code"] == "stale_offers_cleanup_failed"
        assert kwargs["error_type"] == "warning"


class TestCompetitorsReset:
    """Przed scrapowaniem tabela ofert nie jest już czyszczona, więc trigger widzi zmiany cen."""

    @pytest.fixture(autouse=True)
    def mock_db(self, monkeypatch):
        monkeypatch.setattr(Dag, "save_task_run", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "save_error_log", lambda *args, **kwargs: None)
        self.cur = MagicMock()
        self.cur.fetchone.return_value = ("nasz_klient_competitors",)
        self.cur.rowcount = 4
        conn = MagicMock()
        conn.cursor.return_value = self.cur
        monkeypatch.setattr(Dag.psycopg2, "connect", lambda **kwargs: conn)

    def executed(self):
        return [repr(call.args[0]) for call in self.cur.execute.call_args_list]

    def test_truncate_is_off_by_default(self):
        assert Dag.DEMO_TRUNCATE_COMPETITORS is False

    def test_only_offers_older_than_limit_are_removed(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "DEMO_TRUNCATE_COMPETITORS", False)
        monkeypatch.setattr(Dag, "COMPETITOR_MAX_AGE_DAYS", 7)

        Dag.reset_competitors_table_for_demo(sample_client, pipeline_run_id=1)

        statements = self.executed()
        assert not any("TRUNCATE" in statement for statement in statements)
        delete_call = self.cur.execute.call_args_list[-1]
        assert "DELETE FROM" in repr(delete_call.args[0])
        cutoff = delete_call.args[1][0]
        assert abs((datetime.now() - cutoff) - timedelta(days=7)) < timedelta(minutes=1)

    def test_demo_mode_still_truncates(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "DEMO_TRUNCATE_COMPETITORS", True)

        Dag.reset_competitors_table_for_demo(sample_client, pipeline_run_id=1)

        assert any("TRUNCATE" in statement for statement in self.executed())


# Testy na bazie (tymczasowe tabele test_hist_*, usuwane po teście).

def database_available():
    try:
        psycopg2.connect(**Dag.DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not database_available(), reason="Brak połączenia z bazą aplikacji")
class TestPriceHistoryInDatabase:

    @pytest.fixture
    def table(self):
        name = f"test_hist_{uuid.uuid4().hex[:8]}_competitors"
        yield name
        conn = psycopg2.connect(**Dag.DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {name}, {name}_history")
        conn.close()

    @staticmethod
    def scrape(table, offers):
        """Jeden przebieg scrapera przez pipeline."""
        spider = SimpleNamespace(target_table=table, store_prefix="test", logger=logging.getLogger("test"))
        pipeline = SaveToPostgresSQLPipeline()
        pipeline.open_spider(spider)
        for item in offers:
            pipeline.process_item(item, spider)
        pipeline.close_spider(spider)

    @staticmethod
    def query(sql_text, params=()):
        conn = psycopg2.connect(**Dag.DB_CONFIG)
        with conn, conn.cursor() as cur:
            cur.execute(sql_text, params)
            rows = cur.fetchall()
        conn.close()
        return rows

    def history(self, table):
        return self.query(
            f"SELECT sku, price_normal_old, price_normal_new, is_current FROM {table}_history ORDER BY id"
        )

    def test_price_change_between_runs_is_recorded(self, table):
        self.scrape(table, [offer("koc", 100.0, date=YESTERDAY), offer("pled", 50.0, date=YESTERDAY)])
        self.scrape(table, [offer("koc", 120.0), offer("pled", 50.0)])

        assert self.history(table) == [("koc", 100.0, 120.0, True)]

    def test_second_change_closes_previous_entry(self, table):
        self.scrape(table, [offer("koc", 100.0, date=YESTERDAY)])
        self.scrape(table, [offer("koc", 120.0, date=YESTERDAY)])
        self.scrape(table, [offer("koc", 90.0)])

        assert self.history(table) == [("koc", 100.0, 120.0, False), ("koc", 120.0, 90.0, True)]

    @pytest.mark.parametrize("prices", [(2171.9, 2261.9), (2261.9, 2171.9)])
    def test_duplicate_sku_in_one_run_is_not_a_price_change(self, table, prices):
        """Np. dywaniki „60x100” i „60x100 falowany”."""
        self.scrape(table, [offer("dywanik", price, date=YESTERDAY) for price in prices])
        self.scrape(table, [offer("dywanik", price) for price in reversed(prices)])

        assert self.history(table) == []
        assert self.query(f"SELECT price_normal FROM {table}") == [(2171.9,)]

    def test_first_seen_is_kept_when_offer_is_updated(self, table):
        """first_seen służy raportowi zmian (nowe oferty)."""
        self.scrape(table, [offer("koc", 100.0, date=YESTERDAY)])
        self.scrape(table, [offer("koc", 120.0), offer("nowy", 50.0)])

        first_seen = dict(self.query(f"SELECT sku, first_seen FROM {table}"))

        assert first_seen["koc"] == datetime.fromisoformat(YESTERDAY)
        assert first_seen["nowy"] > datetime.fromisoformat(YESTERDAY)

    def test_stale_offers_of_one_store_are_removed(self, table):
        self.scrape(table, [
            offer("koc", 100.0, date=YESTERDAY),
            offer("wycofany", 80.0, date=YESTERDAY),
            offer("koc", 110.0, store="calavado", date=YESTERDAY),
        ])
        started_at = datetime.now()
        self.scrape(table, [offer("koc", 100.0)])

        removed = Dag.remove_stale_offers(table, "pod_pierzyna", started_at)

        assert removed == 1
        assert sorted(self.query(f"SELECT sku, store FROM {table}")) == [
            ("koc", "calavado"), ("koc", "podpierzyna"),
        ]

    def test_nothing_is_removed_when_run_saved_no_offers(self, table):
        self.scrape(table, [offer("koc", 100.0, date=YESTERDAY)])

        assert Dag.remove_stale_offers(table, "pod_pierzyna", datetime.now()) == 0
        assert self.query(f"SELECT sku FROM {table}") == [("koc",)]
