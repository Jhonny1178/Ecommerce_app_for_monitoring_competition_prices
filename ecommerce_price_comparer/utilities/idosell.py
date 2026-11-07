"""Ceny rozmiarów ze skryptu "ajaxLoad" strony produktu sklepu IdoSell."""
import json
import re

_OBJECT_PATTERN = re.compile(r"\{(.*)\}", re.DOTALL)


def idosell_sizes(script_texts, logger=None, url=None):
    """Rozmiary produktu ze skryptów strony ({} gdy ich nie ma)."""
    for text in script_texts:
        if "cena_raty" not in text:
            continue
        match = _OBJECT_PATTERN.search(text)
        if not match:
            continue
        try:
            data = json.loads(match.group(0).replace("'", '"'))
        except ValueError as error:
            if logger:
                logger.warning("Nie udało się odczytać rozmiarów na stronie %s: %s", url, error)
            continue
        sizes = data.get("sizes") or {}
        return sizes if isinstance(sizes, dict) else {}
    return {}


def size_prices(size_info):
    """(cena regularna, cena promocyjna albo None) jednego rozmiaru."""
    price_info = size_info.get("price") or {}
    value = price_info.get("value")
    if "normalprice" in price_info:
        return price_info["normalprice"], value
    return value, None
