"""Pobieranie tylko marek klienta: dopasowanie nazw marek, strony startowe scraperów Calavado i pod_pierzyna oraz argument -a brands w DAG-u."""
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse, Request

import Dag
from ecommerce_price_comparer.spiders.Calavado import CalavadoSpider
from ecommerce_price_comparer.spiders.pod_pierzyna import (
    PRODUCERS_URL,
    PodPierzynaSpider,
    next_page_url,
)
from ecommerce_price_comparer.utilities.brands import (
    brand_matches,
    brand_slug,
    parse_brands,
)


class TestBrandMatching:
    @pytest.mark.parametrize("shop_brand, client_brand", [
        ("Cawo", "cawö"),
        ("Moeve", "möve"),
        ("Eskimo", "eskimo switzerland"),
        ("Villeroy & Boch", "villeroy & boch"),
        ("JOOP!", "joop!"),
        ("Le Jacquard Français", "le jacquard français"),
        ("Curt Bauer", "curt bauer"),
        ("Schoner Wohnen", "schöner wohen"),
    ])
    def test_same_brand(self, shop_brand, client_brand):
        assert brand_matches(shop_brand, [client_brand])

    @pytest.mark.parametrize("shop_brand, client_brand", [
        ("Kleine Wolke", "elegante"),
        ("Elvang", "elegante"),
        ("Home", "roberto cavalli home"),
        ("Hobby", "hobb"),
    ])
    def test_other_brand(self, shop_brand, client_brand):
        assert not brand_matches(shop_brand, [client_brand])

    def test_brand_slug(self):
        assert brand_slug("Villeroy & Boch") == "villeroy_boch"
        assert brand_slug("decor walther") == "decor_walther"
        assert brand_slug("joop!") == "joop"

    def test_parse_brands(self):
        assert parse_brands(None) == []
        assert parse_brands("") == []
        assert parse_brands("joop!| cawö ||joop!") == ["joop!", "cawö"]


class TestCalavadoBrandPages:
    def test_without_brands_crawls_whole_shop(self):
        spider = CalavadoSpider()
        urls = [request.url for request in spider.initial_requests()]
        assert urls == CalavadoSpider.start_urls

    def test_brand_pages_with_name_variants(self):
        spider = CalavadoSpider(brands="decor walther|möve|eskimo switzerland")
        urls = [request.url for request in spider.initial_requests()]
        assert urls == [
            "https://www.calvado.com/kolekcja/decor_walther.html",
            "https://www.calvado.com/kolekcja/move.html",
            "https://www.calvado.com/kolekcja/moeve.html",
            "https://www.calvado.com/kolekcja/eskimo_switzerland.html",
            "https://www.calvado.com/kolekcja/eskimo.html",
        ]

    def test_brand_page_is_crawled_like_category(self):
        spider = CalavadoSpider(brands="decor walther")
        request = spider.initial_requests()[0]
        assert request.callback == spider.parse
        assert request.errback == spider.brand_page_failed


PRODUCERS_HTML = """<html><body>
  <a class="producers__icon" href="/firm-pol-1329223174-Cawo.html" title="Cawo"><img></a>
  <a class="producers__icon" href="/firm-pol-1111111111-Antilo.html" title="Antilo"><img></a>
  <a class="producers__icon" href="/firm-pol-2222222222-JOOP.html" title="JOOP!"><img></a>
</body></html>"""


def producers_page(body=PRODUCERS_HTML):
    return HtmlResponse(
        url=PRODUCERS_URL,
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=Request(PRODUCERS_URL),
    )


class TestPodPierzynaBrandPages:
    def test_without_brands_crawls_bedroom(self):
        spider = PodPierzynaSpider()
        urls = [request.url for request in spider.initial_requests()]
        assert urls == PodPierzynaSpider.start_urls

    def test_with_brands_starts_from_brand_list(self):
        spider = PodPierzynaSpider(brands="cawö|joop!")
        requests = spider.initial_requests()
        assert [request.url for request in requests] == [PRODUCERS_URL]
        assert requests[0].callback == spider.parse_producers

    def test_follows_only_client_brands(self):
        spider = PodPierzynaSpider(brands="cawö|joop!|decor walther")
        requests = list(spider.parse_producers(producers_page()))
        assert [request.url for request in requests] == [
            "https://podpierzyna.com/firm-pol-1329223174-Cawo.html",
            "https://podpierzyna.com/firm-pol-2222222222-JOOP.html",
        ]
        assert all(request.callback == spider.get_product_links for request in requests)

    def test_no_common_brand_falls_back_to_bedroom(self):
        spider = PodPierzynaSpider(brands="decor walther")
        requests = list(spider.parse_producers(producers_page()))
        assert [request.url for request in requests] == PodPierzynaSpider.start_urls
        assert requests[0].callback == spider.parse


class TestPodPierzynaPagination:
    def test_first_page_of_brand(self):
        assert (
            next_page_url("https://podpierzyna.com/firm-pol-1329223174-Cawo.html")
            == "https://podpierzyna.com/firm-pol-1329223174-Cawo.html?counter=1"
        )

    def test_brand_id_is_not_changed(self):
        assert (
            next_page_url("https://podpierzyna.com/firm-pol-1329223174-Cawo.html?counter=1")
            == "https://podpierzyna.com/firm-pol-1329223174-Cawo.html?counter=2"
        )

    def test_category_pages(self):
        assert next_page_url("https://podpierzyna.com/posciel/") == "https://podpierzyna.com/posciel/?counter=1"
        assert next_page_url("https://podpierzyna.com/posciel/?counter=9") == "https://podpierzyna.com/posciel/?counter=10"
        assert next_page_url("https://podpierzyna.com/posciel/?sort=a") == "https://podpierzyna.com/posciel/?sort=a&counter=1"


class TestDagBrandsArgument:
    @pytest.fixture(autouse=True)
    def mock_db_helpers(self, monkeypatch):
        monkeypatch.setattr(Dag, "create_live_task_run", lambda **kwargs: 123)
        monkeypatch.setattr(Dag, "update_live_task_run", lambda **kwargs: None)
        monkeypatch.setattr(Dag, "save_task_run", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "save_error_log", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "mark_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "clear_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "count_rows", lambda table: 10)
        monkeypatch.setattr(Dag, "remove_stale_offers", lambda *args, **kwargs: 0)

    @staticmethod
    def run_and_get_command(client):
        process = MagicMock()
        process.stdout = iter([
            "[scrapy.statscollectors] INFO: Dumping Scrapy stats:",
            "{'finish_reason': 'finished',",
            " 'item_scraped_count': 10,",
            "[scrapy.core.engine] INFO: Spider closed",
        ])
        process.wait.return_value = 0
        with patch("subprocess.Popen", return_value=process) as popen:
            Dag.run_spider_for_client(client=client, spider_name="pod_pierzyna", pipeline_run_id=1)
        return popen.call_args[0][0]

    def test_brands_are_passed_to_scrapy(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "SCRAPE_CLIENT_BRANDS_ONLY", True)
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: ["decor walther", "joop!", "a|b"])

        command = self.run_and_get_command(sample_client)

        assert "-a brands='decor walther|joop!|a b' " in command

    def test_no_brands_means_whole_shop(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "SCRAPE_CLIENT_BRANDS_ONLY", True)
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: [])

        assert "-a brands" not in self.run_and_get_command(sample_client)

    def test_no_item_limit_for_client_brands(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "SCRAPE_CLIENT_BRANDS_ONLY", True)
        monkeypatch.setattr(Dag, "SCRAPY_ITEM_LIMIT", 1000)
        monkeypatch.setattr(Dag, "SCRAPY_BRAND_ITEM_LIMIT", 0)
        monkeypatch.setattr(Dag, "SCRAPY_CLOSE_TIMEOUT_SECONDS", 5400)
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: ["joop!"])

        command = self.run_and_get_command(sample_client)

        assert "CLOSESPIDER_ITEMCOUNT" not in command
        assert "-s CLOSESPIDER_TIMEOUT=5400 " in command

    def test_item_limit_for_whole_shop(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "SCRAPE_CLIENT_BRANDS_ONLY", True)
        monkeypatch.setattr(Dag, "SCRAPY_ITEM_LIMIT", 1000)
        monkeypatch.setattr(Dag, "SCRAPY_BRAND_ITEM_LIMIT", 0)
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: [])

        assert "-s CLOSESPIDER_ITEMCOUNT=1000 " in self.run_and_get_command(sample_client)

    def test_setting_off(self, sample_client, monkeypatch):
        monkeypatch.setattr(Dag, "SCRAPE_CLIENT_BRANDS_ONLY", False)
        get_brands = MagicMock(return_value=["joop!"])
        monkeypatch.setattr(Dag, "get_client_brands", get_brands)

        assert "-a brands" not in self.run_and_get_command(sample_client)
        get_brands.assert_not_called()
