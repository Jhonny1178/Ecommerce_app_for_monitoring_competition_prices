"""Krótkie (max 3 zdania) uzasadnienie rekomendacji cenowej (price-recomendation)."""
import os
import sys

import pytest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "price-recomendation"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from explanation import (  # noqa: E402
    _SENTENCE_BREAK,
    contradicts_facts,
    facts_for_prompt,
    fallback_reason,
    format_pln,
    limit_sentences,
    market_facts,
    mentions_amount,
)


def sentence_count(text):
    return len(_SENTENCE_BREAK.split(text.strip()))


def test_format_pln():
    assert format_pln(509) == "509 zł"
    assert format_pln(514.333) == "514,33 zł"
    assert format_pln(98.99) == "98,99 zł"


@pytest.mark.parametrize("text, value, expected", [
    ("nie tak drastycznie jak przy 179 zł.", 179, True),
    ("kosztuje 179.", 179, True),
    ("kosztuje 1799 zł", 179, False),
    ("kosztuje 179,50 zł", 179, False),
    ("średnio 514,33 zł", 514.33, True),
])
def test_mentions_amount(text, value, expected):
    assert mentions_amount(text, value) is expected


def test_limit_sentences_cuts_to_three_and_keeps_abbreviations():
    text = "Pierwsze zdanie. Drugie ok. 12 zł taniej. Trzecie! Czwarte? Piąte."
    assert limit_sentences(text) == "Pierwsze zdanie. Drugie ok. 12 zł taniej. Trzecie!"


@pytest.mark.parametrize("prices, our, suggested, expected", [
    ([489.0, 519.0, 535.0], 549.0, 509.0, "lower"),
    ([489.0, 519.0, 535.0], 449.0, 509.0, "raise"),
    ([489.0, 519.0, 535.0], 509.0, 509.0, "keep"),
    ([825.0, 794.9], 825.0, 819.0, "lower"),
    ([489.0], 0.0, 469.0, "set"),
])
def test_direction(prices, our, suggested, expected):
    assert market_facts(prices, our, suggested)["direction"] == expected


@pytest.mark.parametrize("text, expected", [
    ("Twoja oferta pozostaje poniżej średniej rynkowej (809,95 zł).", True),
    ("Jesteś tuż pod średnią rynkową.", True),
    ("Twoja oferta będzie najtańsza na rynku.", True),
    ("Twoja oferta jest nieco powyżej średniej, ale tańsza od najdroższej.", False),
    ("Cena jest wyższa od najtańszej oferty (794,90 zł).", False),
])
def test_contradicts_facts(text, expected):
    # 819 zł: powyżej średniej 809,95 zł, jedna oferta tańsza (794,90 zł).
    facts = market_facts([825.0, 794.9], 825.0, 819.0)
    assert contradicts_facts(text, facts) is expected


@pytest.mark.parametrize("text, has_history, expected", [
    ("Brak historii zmian cen dla tego produktu.", False, True),
    ("Nie mamy danych o wcześniejszych cenach.", False, True),
    ("Historia cen pokazuje stabilny rynek.", False, True),
    ("Historia pokazuje, że Pod Pierzyna niedawno obniżył cenę.", True, False),
    ("Jesteś tańszy od dwóch z trzech konkurentów.", False, False),
])
def test_text_about_missing_data_is_rejected(text, has_history, expected):
    facts = market_facts([489.0, 519.0, 535.0], 549.0, 509.0)
    assert contradicts_facts(text, facts, has_history=has_history) is expected


def test_history_only_in_prompt_when_present():
    facts = market_facts([489.0, 519.0, 535.0], 549.0, 509.0)

    without = facts_for_prompt(facts, [])
    assert "histori" not in without.lower() and "zmiany cen" not in without

    with_history = facts_for_prompt(facts, [
        {"sklep": "Pod Pierzyna", "cena_przed": 549.0, "cena_po": 519.0, "data": "2026-11-07"},
        {"sklep": "Calavado", "cena_przed": 535.0, "cena_po": 535.0, "data": "2026-11-06"},
    ])
    assert "Pod Pierzyna obniżył cenę z 549 zł do 519 zł (07.11.2026)" in with_history
    assert "Calavado" not in with_history


def test_fallback_reason_lower_price():
    facts = market_facts([489.0, 519.0, 535.0], 549.0, 509.0)
    text = fallback_reason(facts)

    assert text.startswith("Obniż cenę z 549 zł do 509 zł.")
    assert "3 oferty" in text and "489 zł–535 zł" in text and "514,33 zł" in text
    assert "tańsza niż 2 z 3 ofert" in text
    assert sentence_count(text) == 3


def test_fallback_reason_raise_price():
    text = fallback_reason(market_facts([1299.0, 1349.0, 1420.0], 1150.0, 1349.0))
    assert text.startswith("Podnieś cenę z 1150 zł do 1349 zł.")
    assert sentence_count(text) == 3


@pytest.mark.parametrize("prices, suggested, phrase", [
    ([100.0], 95.0, "Jedyna oferta konkurencji kosztuje 100 zł."),
    ([100.0, 120.0], 99.0, "Nikt z konkurencji nie będzie tańszy od Ciebie."),
    ([100.0, 100.0], 100.0, "Twoja cena będzie taka sama jak u konkurencji."),
])
def test_fallback_reason_edge_cases(prices, suggested, phrase):
    text = fallback_reason(market_facts(prices, 130.0, suggested))
    assert phrase in text
    assert sentence_count(text) <= 3


def test_fallback_reason_without_competitors():
    text = fallback_reason(market_facts([], 100.0, 120.0))
    assert "Nie znaleźliśmy ofert konkurencji" in text and "120 zł" in text


# --- endpoint z podstawionym modelem AI (bez prawdziwego wywołania Groq) ---

@pytest.fixture
def service(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("groq")
    monkeypatch.chdir(SERVICE_DIR)
    import main
    return main


def call(service, prices, our=549.0, history=None):
    return service.oblicz_cene_endpoint(service.DaneZeScrapera(
        nazwa_produktu="Pościel", lista_rynkowa=prices, nasza_cena=our, historia_cen=history or [],
    ))


def test_endpoint_passes_history_to_ai_only_when_present(service, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history: (
        seen.append(history) or {"cena_ostateczna": 509, "uzasadnienie": "Powód."}
    ))

    call(service, [489.0, 519.0, 535.0])
    call(service, [489.0, 519.0, 535.0], history=[
        {"sklep": "Pod Pierzyna", "cena_przed": 549.0, "cena_po": 519.0, "data": "2026-11-07"},
    ])

    assert seen[0] == []
    assert seen[1] == [{"sklep": "Pod Pierzyna", "cena_przed": 549.0, "cena_po": 519.0, "data": "2026-11-07"}]


def test_endpoint_rejects_ai_text_about_missing_history(service, monkeypatch):
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history: {
        "cena_ostateczna": 509,
        "uzasadnienie": "Brak historii cen, więc trudno ocenić trend.",
    })
    result = call(service, [489.0, 519.0, 535.0])
    assert "histori" not in result["uzasadnienie"].lower()
    assert result["uzasadnienie"].startswith("Obniż cenę z 549 zł do 509 zł.")


def test_endpoint_action_from_code_plus_two_ai_sentences(service, monkeypatch):
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history=(): {
        "cena_ostateczna": 509,
        "uzasadnienie": "Jeden. Dwa. Trzy. Cztery.",
    })
    result = call(service, [489.0, 519.0, 535.0])
    assert result == {"cena_ostateczna": 509, "uzasadnienie": "Obniż cenę z 549 zł do 509 zł. Jeden. Dwa."}


@pytest.mark.parametrize("ai_price, our, action", [
    (188, 199.0, "Obniż cenę z 199 zł do 188 zł."),
    (199, 199.0, "Zostaw obecną cenę 199 zł."),
    (199, 150.0, "Podnieś cenę z 150 zł do 199 zł."),
])
def test_endpoint_action_matches_final_ai_price(service, monkeypatch, ai_price, our, action):
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history=(): {
        "cena_ostateczna": ai_price,
        "uzasadnienie": "Powód.",
    })
    result = call(service, [189.0, 205.0], our=our)
    assert result["cena_ostateczna"] == ai_price
    assert result["uzasadnienie"] == f"{action} Powód."


def test_endpoint_hides_algorithm_price_when_ai_changed_it(service, monkeypatch):
    algorithm_price = service.ustal_cene([189.0], 199.0)
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history=(): {
        "cena_ostateczna": 188,
        "uzasadnienie": f"Nie obniżasz marży tak drastycznie jak przy {format_pln(algorithm_price)}.",
    })
    result = call(service, [189.0], our=199.0)
    assert result["cena_ostateczna"] == 188
    assert not mentions_amount(result["uzasadnienie"], algorithm_price)
    assert result["uzasadnienie"] == (
        "Obniż cenę z 199 zł do 188 zł. Jedyna oferta konkurencji kosztuje 189 zł. "
        "Nikt z konkurencji nie będzie tańszy od Ciebie."
    )


def test_endpoint_reported_case_825_to_819(service, monkeypatch):
    # Przypadek zgłoszony z aplikacji: sugerowane 819 zł przy obecnych 825 zł i tekst sprzeczny z faktami.
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history=(): {
        "cena_ostateczna": 819,
        "uzasadnienie": "Twoja oferta jest nieco wyższa od najtańszej konkurencji (794,90 zł), "
                        "ale pozostaje poniżej średniej rynkowej (809,95 zł).",
    })
    result = call(service, [825.0, 794.9], our=825.0)

    assert result["cena_ostateczna"] == 819
    assert result["uzasadnienie"].startswith("Obniż cenę z 825 zł do 819 zł.")
    assert "poniżej średniej" not in result["uzasadnienie"]
    assert "średnio 809,95 zł" in result["uzasadnienie"]


def test_endpoint_without_ai_returns_algorithm_price_and_facts(service, monkeypatch):
    def broken(nazwa, facts, history=()):
        raise RuntimeError("Wszystkie klucze API Groq zostały wykorzystane")

    monkeypatch.setattr(service, "skonsultuj_z_llm", broken)
    result = call(service, [489.0, 519.0, 535.0])
    assert result["cena_ostateczna"] == service.ustal_cene([489.0, 519.0, 535.0], 549.0)
    assert result["uzasadnienie"].startswith("Obniż cenę")


@pytest.mark.parametrize("bad_answer", [
    {"cena_ostateczna": "dużo", "uzasadnienie": "Tekst."},
    {"cena_ostateczna": -5, "uzasadnienie": "Tekst."},
    {"cena_ostateczna": 999, "uzasadnienie": "Tekst."},
    {"cena_ostateczna": 509, "uzasadnienie": ""},
])
def test_endpoint_ignores_invalid_ai_answer(service, monkeypatch, bad_answer):
    monkeypatch.setattr(service, "skonsultuj_z_llm", lambda nazwa, facts, history=(): bad_answer)
    result = call(service, [489.0, 519.0, 535.0])
    assert result["cena_ostateczna"] == service.ustal_cene([489.0, 519.0, 535.0], 549.0)
    assert result["uzasadnienie"].startswith("Obniż cenę")
    assert "Tekst." not in result["uzasadnienie"]


def test_endpoint_without_competitors_skips_ai(service, monkeypatch):
    def must_not_be_called(nazwa, facts, history=()):
        raise AssertionError("model AI nie powinien być wołany bez ofert konkurencji")

    monkeypatch.setattr(service, "skonsultuj_z_llm", must_not_be_called)
    result = call(service, [], our=100.0)
    assert result["cena_ostateczna"] == 120.0
