"""Ujednolicanie kolorów (ecommerce_price_comparer/utilities/data_utility.py)."""
import pytest

from ecommerce_price_comparer.utilities.data_utility import DataCleaner


@pytest.mark.parametrize("raw, expected", [
    ("wielokolorowy", "wielokolorowy"),
    ("ecru/wielokolorowy", "wielokolorowy"),
    ("różnokolorowy", "wielokolorowy"),
    ("wielobarwna", "wielokolorowy"),
    ("szara", "szary"),
    ("ciemnoszara", "szary"),
    ("jasnoniebieska", "niebieski"),
    ("granatowa", "niebieski"),
    ("kremowa", "beżowy"),
    ("srebrnej", "szary"),
    ("złota", "złoty"),
    ("gold", "złoty"),
    ("dark blue", "niebieski"),
    ("orange", "pomarańczowy"),
])
def test_competitor_colors(raw, expected):
    assert DataCleaner.standardize_color(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("szary", "szary"),
    ("beżowy", "beżowy"),
    ("ecru", "ecru"),
    ("złoty", "złoty"),
    ("pomarańczowy", "pomarańczowy"),
    ("chrom / stal", "chrom / stal"),
    ("bezbarwny", "bezbarwny"),
    ("ecru/beżowy", "beżowy"),
    ("szary/beżowy", "szary"),
    ("złoty/różowy", "różowy"),
    ("ciemny metal", "ciemny metal"),
])
def test_client_feed_colors_keep_meaning(raw, expected):
    assert DataCleaner.standardize_color(raw) == expected


def test_empty_color():
    assert DataCleaner.standardize_color(None) is None
    assert DataCleaner.standardize_color("") is None
