"""Porównywanie marek klienta z markami sklepów ("Cawö" = "Cawo", "Möve" = "Moeve")."""
import re
import unicodedata
from difflib import SequenceMatcher

BRANDS_SEPARATOR = "|"

GENERIC_BRAND_WORDS = {
    "home", "living", "design", "designs", "by", "the", "and", "collection",
    "switzerland", "germany", "france", "paris", "milano", "barcelona",
}

MIN_PARTIAL_LENGTH = 4
TYPO_SIMILARITY = 0.9
MIN_TYPO_LENGTH = 8

_GERMAN_LETTERS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
_POLISH_LETTERS = {"ł": "l"}


def _ascii(text, german):
    text = str(text or "").lower()
    if german:
        for letter, replacement in _GERMAN_LETTERS.items():
            text = text.replace(letter, replacement)
    for letter, replacement in _POLISH_LETTERS.items():
        text = text.replace(letter, replacement)
    text = text.replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def brand_variants(name):
    """Zestawy słów nazwy marki: z "ö" zamienionym na "o" oraz na "oe"."""
    variants = []
    for german in (False, True):
        words = tuple(re.findall(r"[a-z0-9]+", _ascii(name, german)))
        if words and words not in variants:
            variants.append(words)
    return variants


def brand_slug(name):
    """Nazwa marki w adresie URL: "Villeroy & Boch" -> "villeroy_boch"."""
    variants = brand_variants(name)
    return "_".join(variants[0]) if variants else ""


def parse_brands(raw):
    """Lista marek z argumentu scrapera ("joop!|cawö|...") bez pustych i powtórzeń."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = str(raw).split(BRANDS_SEPARATOR)
    brands = []
    for item in items:
        item = str(item).strip()
        if item and item not in brands:
            brands.append(item)
    return brands


def _words_match(first, second):
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    meaningful = [word for word in shorter if word not in GENERIC_BRAND_WORDS]
    if (
        meaningful
        and len("".join(meaningful)) >= MIN_PARTIAL_LENGTH
        and set(shorter) <= set(longer)
    ):
        return True
    first_key, second_key = "".join(first), "".join(second)
    return (
        min(len(first_key), len(second_key)) >= MIN_TYPO_LENGTH
        and SequenceMatcher(None, first_key, second_key).ratio() >= TYPO_SIMILARITY
    )


def brand_matches(shop_brand, client_brands):
    """Czy marka ze sklepu konkurencji jest jedną z marek klienta."""
    shop_variants = brand_variants(shop_brand)
    for client_brand in client_brands:
        for client_words in brand_variants(client_brand):
            if any(_words_match(shop_words, client_words) for shop_words in shop_variants):
                return True
    return False
