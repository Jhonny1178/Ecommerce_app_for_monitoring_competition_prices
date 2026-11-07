"""Odczyt danych strukturalnych JSON-LD ze stron sklepów."""

import json


def _flatten(data):
    if isinstance(data, list):
        for element in data:
            yield from _flatten(element)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _flatten(data["@graph"])


def iter_jsonld_nodes(raw_blocks):
    """Zwraca wszystkie węzły ze wszystkich bloków JSON-LD; nieczytelne bloki pomija."""
    for raw in raw_blocks or []:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        yield from _flatten(data)


def find_jsonld_node(raw_blocks, node_type):
    """Zwraca pierwszy węzeł danego typu (np. "Product") albo None."""
    for node in iter_jsonld_nodes(raw_blocks):
        types = node.get("@type")
        if types == node_type or (isinstance(types, list) and node_type in types):
            return node
    return None


def jsonld_offers(product):
    """Zwraca oferty produktu jako listę słowników (pole bywa obiektem albo listą)."""
    offers = (product or {}).get("offers")
    if isinstance(offers, dict):
        return [offers]
    if isinstance(offers, list):
        return [offer for offer in offers if isinstance(offer, dict)]
    return []


def jsonld_text(value):
    """Zwraca tekst z pola, które może być tekstem, obiektem {"name": ...} albo listą."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def jsonld_first_url(value):
    """Zwraca pierwszy adres z pola obrazka (tekst, lista albo obiekt ImageObject)."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value if isinstance(value, str) and value else None


def breadcrumb_name(element):
    """Zwraca nazwę pozycji BreadcrumbList; nazwa bywa w elemencie albo w jego "item"."""
    if not isinstance(element, dict):
        return None
    name = element.get("name")
    if not name and isinstance(element.get("item"), dict):
        name = element["item"].get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None
