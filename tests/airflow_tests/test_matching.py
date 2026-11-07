"""Dopasowanie produktów klienta do ofert konkurencji (processors/files_connector.py)."""
import pandas as pd
import pytest
from rapidfuzz import fuzz

from processors import files_connector
from processors.files_connector import (
    MultiTenantReportGenerator,
    comparable_color,
    distinctive_words,
    missing_words,
    size_key,
)

STORES = ["calavado", "jmbdesing", "nasz_klient", "pod_pierzyna"]
THRESHOLDS = {"name_threshold": 90, "color_threshold": 80, "maker_threshold": 80}


def client_product(sku, name, size="155x200", color="beżowy", manufacturer="elegante", price=500.0):
    return {
        "sku": sku, "name": name, "price_normal": price, "url": f"https://klient.test/{sku}",
        "manufacturer": manufacturer, "size": size, "color": color,
    }


def offer(name, store="podpierzyna", size="155x200", color="beżowy", manufacturer="elegante", price=600.0):
    return {
        "sku": name, "name": name, "store": store, "price_normal": price,
        "url": f"https://{store}.test/{name.replace(' ', '-')}",
        "manufacturer": manufacturer, "size": size, "color": color,
    }


def run_matching(clients, offers, **thresholds):
    generator = MultiTenantReportGenerator({"competitor_stores": STORES})
    result = generator.match_products(
        pd.DataFrame(clients), pd.DataFrame(offers), **{**THRESHOLDS, **thresholds}
    )
    return result.set_index("client_sku")


class TestDistinctiveWords:

    def test_skips_codes_colors_short_words_and_manufacturer(self):
        words = distinctive_words("pościel flanelowa soft checks 45000-07 cream", "elegante")
        assert words == ["pościel", "flanelowa", "soft", "checks"]

        assert distinctive_words("poszewka na poduszkę cornflower stripes 4069 11 deep rose", "joop!") == [
            "poszewka", "poduszkę", "cornflower", "stripes",
        ]
        assert distinctive_words("pościel joop toucan beige pl", "joop") == ["pościel", "toucan"]

    def test_polish_color_forms_are_colors(self):
        assert distinctive_words("koc kremowa jasnoszary") == ["koc"]

    def test_word_inflection_counts_as_same_word(self):
        assert missing_words(["pościel", "satynowa"], ["pościel", "satyna", "bawełniana"]) == []
        assert missing_words(["kołdry"], ["kołdra"]) == []
        assert missing_words(["lines"], ["checks", "linen"]) == ["lines"]


class TestMatchProducts:

    def test_other_pattern_with_common_words_is_not_matched(self):
        """"soft lines" ma dużo wspólnych słów z "soft checks", ale to inny wzór."""
        clients = [
            client_product("checks", "pościel flanelowa soft checks 45000-07 cream"),
            client_product("lines", "pościel flanelowa soft lines 45008-7 jasny beż"),
        ]
        offers = [offer("pościel flanelowa soft lines cream", price=654.9)]

        result = run_matching(clients, offers)

        assert fuzz.token_set_ratio(clients[0]["name"], offers[0]["name"]) >= 90, "Warunek testu: sam próg nazwy by to przepuścił"
        assert pd.isna(result.loc["checks", "pod_pierzyna_price"])
        assert result.loc["lines", "pod_pierzyna_price"] == 654.9
        assert result.loc["lines", "pod_pierzyna_name"] == "pościel flanelowa soft lines cream"
        assert result.loc["lines", "pod_pierzyna_sku"] == "pościel flanelowa soft lines cream", "SKU oferty jest potrzebny do historii cen"
        assert pd.isna(result.loc["checks", "pod_pierzyna_sku"])

    def test_same_model_with_color_named_differently_is_matched(self):
        clients = [client_product(
            "stripes", "poszewka na poduszkę cornflower stripes 4069 11 deep rose",
            size="80x80", color="różowy", manufacturer="joop!",
        )]
        offers = [offer(
            "poszewka cornflower stripes pink", store="calavado",
            size="80x80", color="różowy", manufacturer="joop", price=189.0,
        )]

        result = run_matching(clients, offers)

        assert result.loc["stripes", "calavado_price"] == 189.0

    def test_best_offer_wins_not_the_last_one(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-4 green", color="zielony")]
        best = offer("pościel żakardowa muriel 2406-4 green", color="zielony", price=707.9)
        weaker = offer("pościel żakardowa muriel 2407 green", color="zielony", price=999.0)

        weaker_score = fuzz.token_set_ratio(clients[0]["name"], weaker["name"])
        assert 90 <= weaker_score < 100, "Warunek testu: słabsza oferta też przechodzi próg"

        result = run_matching(clients, [best, weaker])

        assert result.loc["muriel", "pod_pierzyna_price"] == 707.9

    def test_each_store_gets_its_own_best_offer(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-4 green", color="zielony")]
        offers = [
            offer("pościel żakardowa muriel green", store="podpierzyna", color="zielony", price=707.9),
            offer("pościel muriel green", store="calavado", color="zielony", price=720.0),
            offer("pościel muriel green", store="nieznany_sklep", color="zielony", price=1.0),
        ]

        result = run_matching(clients, offers)

        assert result.loc["muriel", "pod_pierzyna_price"] == 707.9
        assert result.loc["muriel", "calavado_price"] == 720.0
        assert pd.isna(result.loc["muriel", "jmbdesing_price"])

    def test_different_size_or_color_is_not_matched(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-4 green", color="zielony")]
        offers = [
            offer("pościel żakardowa muriel green", size="200x220", color="zielony"),
            offer("pościel żakardowa muriel green", color="różowy"),
        ]

        result = run_matching(clients, offers)

        assert pd.isna(result.loc["muriel", "pod_pierzyna_price"])

    def test_every_client_product_has_a_row(self):
        clients = [
            client_product("a", "pościel żakardowa muriel 2406-4 green", color="zielony"),
            client_product("b", "ręcznik frotte lotus 50x100", size="50x100"),
        ]
        offers = [offer("pościel żakardowa muriel green", color="zielony")]

        result = run_matching(clients, offers)

        assert list(result.index) == ["a", "b"]
        for store in STORES:
            assert f"{store}_price" in result.columns
            assert f"{store}_price_special" in result.columns

    def test_special_price_of_offer_is_saved(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-4 green", color="zielony")]
        promo = {**offer("pościel żakardowa muriel green", color="zielony", price=707.9), "price_special": 599.0}

        result = run_matching(clients, [promo])

        assert result.loc["muriel", "pod_pierzyna_price"] == 707.9
        assert result.loc["muriel", "pod_pierzyna_price_special"] == 599.0


class TestSizeColorAndManufacturer:

    def test_size_key_ignores_order_of_dimensions(self):
        assert size_key("220x200") == size_key("200x220") == "200x220"
        assert size_key("90100x200220") == "90100x200220"
        assert size_key("uniwersalny") == "uniwersalny"

    def test_reversed_size_is_matched(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-4 green", size="200x220", color="zielony")]
        offers = [offer("pościel żakardowa muriel green", size="220x200", color="zielony", price=799.0)]

        result = run_matching(clients, offers)

        assert result.loc["muriel", "pod_pierzyna_price"] == 799.0

    def test_color_from_name_when_offer_is_multicolor(self):
        assert comparable_color("wielokolorowy", "pościel flanelowa soft frames blue") == "niebieski"
        assert comparable_color(None, "koc swinging paisley beige") == "beżowy"
        assert comparable_color("wielokolorowy", "koc paisley") == "wielokolorowy"

        clients = [client_product("frames", "pościel flanelowa soft frames 45007-2 blue", color="niebieski")]
        offers = [offer("pościel flanelowa soft frames blue", color="wielokolorowy", price=599.0)]

        result = run_matching(clients, offers)

        assert result.loc["frames", "pod_pierzyna_price"] == 599.0

    def test_manufacturer_written_without_umlaut(self):
        clients = [client_product("loft", "ręcznik loft 50x100", size="50x100", color="biały", manufacturer="möve")]
        offers = [offer("ręcznik loft", store="calavado", size="50x100", color="biały", manufacturer="move", price=89.0)]

        assert fuzz.token_set_ratio("möve", "move") < 80, "Warunek testu: sam próg producenta tego nie przepuszcza"
        result = run_matching(clients, offers)

        assert result.loc["loft", "calavado_price"] == 89.0


def filler_products(*names):
    """Produkty klienta w innym rozmiarze, żeby słowa z ich nazw nie były rzadkie."""
    return [
        client_product(f"filler-{index}", name, size="1x1")
        for index, name in enumerate(names)
    ]


class TestMatchByPattern:
    """Nazwa poniżej progu, ale ten sam rzadki wzór ("muriel")."""

    @pytest.fixture(autouse=True)
    def small_catalog(self, monkeypatch):
        monkeypatch.setattr(files_connector, "RARE_WORD_MAX_NAMES", 2)

    def test_color_code_on_client_side_is_matched(self):
        clients = [client_product("muriel", "pościel żakardowa muriel 2406-7 natur", color="beżowy")]
        offers = [offer("pościel żakardowa muriel natural", color="beżowy", price=707.9)]

        assert fuzz.token_set_ratio(clients[0]["name"], offers[0]["name"]) < 90, "Warunek testu: nazwa poniżej progu"
        result = run_matching(clients, offers)

        assert result.loc["muriel", "pod_pierzyna_price"] == 707.9

    def test_other_color_name_on_client_side_is_not_matched(self):
        clients = [client_product("leo", "pościel satyna bawełniana leo 4112-03 vanilla", color="beżowy")] + filler_products(
            "pościel satyna bawełniana alfa", "pościel satyna bawełniana beta", "pościel satyna bawełniana gamma",
        )
        cream = offer("pościel satynowa leo cream", color="beżowy", price=500.0)
        vanilla = offer("pościel satynowa leo vanilla", color="beżowy", price=520.0)

        assert pd.isna(run_matching(clients, [cream]).loc["leo", "pod_pierzyna_price"])
        assert run_matching(clients, [cream, vanilla]).loc["leo", "pod_pierzyna_price"] == 520.0

    def test_two_different_offers_for_one_product_are_skipped(self):
        clients = [client_product("colours", "poszewka na poduszkę colours 9100-6031", size="40x40", color="niebieski")] + filler_products(
            "poszewka na poduszkę alfa", "poszewka na poduszkę beta", "poszewka na poduszkę gamma",
        )
        offers = [
            offer("poszewka colours uni navy blue", store="calavado", size="40x40", color="niebieski"),
            offer("poszewka colours uni turkus", store="calavado", size="40x40", color="niebieski"),
        ]

        result = run_matching(clients, offers)

        assert pd.isna(result.loc["colours", "calavado_price"])

    def test_one_offer_for_two_products_goes_to_the_one_with_its_color(self):
        fillers = filler_products("poszewka na poduszkę alfa", "poszewka na poduszkę beta", "poszewka na poduszkę gamma")
        black = offer("poszewka colours uni black", store="calavado", size="40x40", color="czarny", price=99.0)

        unclear = [
            client_product("9100", "poszewka na poduszkę colours 9100-941", size="40x40", color="czarny"),
            client_product("9200", "poszewka na poduszkę colours 9200-941", size="40x40", color="czarny"),
        ]
        result = run_matching(unclear + fillers, [black])
        assert pd.isna(result.loc["9100", "calavado_price"])
        assert pd.isna(result.loc["9200", "calavado_price"])

        named = [
            client_product("9100", "poszewka na poduszkę colours 9100-941 czarna", size="40x40", color="czarny"),
            client_product("9200", "poszewka na poduszkę colours 9200-941", size="40x40", color="czarny"),
        ]
        result = run_matching(named + fillers, [black])
        assert result.loc["9100", "calavado_price"] == 99.0
        assert pd.isna(result.loc["9200", "calavado_price"])
