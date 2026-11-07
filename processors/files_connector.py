import os
import re
from collections import Counter
from functools import lru_cache
from dotenv import load_dotenv
import pandas as pd
from rapidfuzz import fuzz
from sqlalchemy import create_engine
from ecommerce_price_comparer.utilities.brands import brand_matches
from ecommerce_price_comparer.utilities.data_utility import DataCleaner, color_map
load_dotenv()
db_password = os.getenv("APP_DB_PASSWORD")
db_name = os.getenv("APP_DB_NAME")
db_user_name = os.getenv("APP_DB_USER")
db_port = os.getenv("APP_DB_PORT")
db_host = os.getenv("APP_DB_HOST")


def store_key(name):
    """Nazwa sklepu tylko z liter i cyfr (podpierzyna = pod_pierzyna)."""
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


COLOR_NAME_WORDS = {
    word
    for standard, words in color_map.items()
    for word in (standard, *words)
} | {
    "beige", "ecru", "natur", "natural", "navy", "mint", "orange", "coral", "petrol",
    "antrazit", "anthrazit", "grau", "weiss", "schwarz", "blau", "braun",
    "grün", "rosa",
}
COLOR_NAME_PREFIXES = (
    "beż", "biał", "biel", "błękit", "bordow", "brąz", "ciemn", "czarn",
    "czerw", "fiolet", "granat", "jasn", "kremow", "morsk", "musztard",
    "niebiesk", "oliwk", "piask", "pomarańcz", "popiel", "róż", "srebrn",
    "szar", "turkusow", "wielobarwn", "wielokolorow", "ziel", "złot", "żółt",
)
SHADE_WORDS = {"dark", "light", "deep", "pastel"}
GENERIC_NAME_WORDS = {"uni", "dla", "bez", "oraz"}

MIN_WORD_LENGTH = 3
_WORD_PATTERN = re.compile(r"\w+")

RARE_WORD_MAX_NAMES = 40

UNKNOWN_COLORS = {"", "none", "nan", "wielokolorowy"}


def _is_color_word(word):
    return (
        word in COLOR_NAME_WORDS
        or word in SHADE_WORDS
        or word.startswith(COLOR_NAME_PREFIXES)
    )


def distinctive_words(name, manufacturer=None):
    """Słowa nazwy opisujące model lub wzór, bez kodów, rozmiarów, kolorów i producenta."""
    skip = set(GENERIC_NAME_WORDS)
    skip.update(_WORD_PATTERN.findall(str(manufacturer or "").lower()))

    words = []
    for word in _WORD_PATTERN.findall(str(name or "").lower()):
        if len(word) < MIN_WORD_LENGTH or word in skip:
            continue
        if any(ch.isdigit() for ch in word) or "_" in word:
            continue
        if _is_color_word(word):
            continue
        words.append(word)
    return words


def words_similar(first, second):
    """To samo słowo z dokładnością do odmiany ("satynowa" i "satyna")."""
    if first == second:
        return True
    limit = 80 if min(len(first), len(second)) >= 6 else 85
    return fuzz.ratio(first, second) >= limit


def missing_words(competitor_words, client_words):
    """Słowa oferty konkurencji, których nie ma w nazwie produktu klienta."""
    return [
        word
        for word in competitor_words
        if not any(words_similar(word, client_word) for client_word in client_words)
    ]


def size_key(size):
    """Rozmiar bez spacji, wymiary od najmniejszego (220x200 = 200x220)."""
    key = str(size).lower().replace(" ", "")
    parts = key.split("x")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return "x".join(sorted(parts, key=int))
    return key


def name_color(name):
    """Kolor ze słów nazwy ("pościel soft frames blue" -> "niebieski")."""
    for word in _WORD_PATTERN.findall(str(name or "").lower()):
        if word in SHADE_WORDS or not _is_color_word(word):
            continue
        color = DataCleaner.standardize_color(word)
        if color in color_map and color != "wielokolorowy":
            return color
    return None


def comparable_color(color, name):
    """Kolor do porównania; bez koloru albo przy "wielokolorowy" kolor z nazwy."""
    value = "" if color is None or pd.isna(color) else str(color).strip()
    if value.lower() in UNKNOWN_COLORS:
        return name_color(name) or value
    return value


@lru_cache(maxsize=None)
def same_manufacturer(client_maker, competitor_maker, threshold):
    """Ten sam producent: podobieństwo nazwy albo ta sama marka ("möve" = "move")."""
    if fuzz.token_set_ratio(client_maker, competitor_maker) >= threshold:
        return True
    return brand_matches(competitor_maker, [client_maker])


class MultiTenantReportGenerator:

    def __init__(self, config: dict):

        db_url = (
            f"postgresql://{db_user_name}:{db_password}@{db_host}:{db_port}/{db_name}"
        )
        self.engine = create_engine(db_url)

        self.client_table = config.get("client_table")
        self.competitor_table = config.get("competitor_table")
        self.target_table_name = config.get("target_table")

        self.competitor_stores = config.get("competitor_stores", [])

    def result_column_store(self, store):
        """Nazwa sklepu w kolumnach wyniku dla wartości kolumny store albo None."""
        wanted = store_key(store)
        for comp_store in self.competitor_stores:
            if store_key(comp_store) == wanted:
                return comp_store
        return None

    def generate_report(self, name_threshold, color_threshold, maker_threshold):
        print(
            f"\n[MATCHING] Rozpoczynam dopasowywanie produktów dla tabeli: {self.client_table}"
        )
        print(
            f"[MATCHING] Pobieranie danych z dedykowanej tabeli konkurencji: {self.competitor_table}"
        )

        # 1. Dynamiczne pobieranie danych klienta
        query_client = f"""
            SELECT sku, name, price_normal, url, manufacturer, size, color
            FROM {self.client_table}
            WHERE manufacturer IS NOT NULL
              AND size IS NOT NULL;
        """
        client_df = pd.read_sql(query_client, self.engine)

        # 2. Dynamiczne pobieranie danych konkurencji z jej dedykowanej tabeli
        query_comp = f"""
            SELECT sku, name, store, price_normal, price_special, url, manufacturer, size, color
            FROM {self.competitor_table}
            WHERE manufacturer IS NOT NULL
              AND size IS NOT NULL;
        """
        comp_df = pd.read_sql(query_comp, self.engine)

        print(f"[MATCHING] Liczba produktów klienta: {len(client_df)}")
        print(f"[MATCHING] Liczba produktów konkurencji: {len(comp_df)}")

        if client_df.empty or comp_df.empty:
            print(
                f"[MATCHING] Pomijam generowanie raportu - jedna z tabel jest pusta."
            )
            return None

        final_df = self.match_products(
            client_df,
            comp_df,
            name_threshold=name_threshold,
            color_threshold=color_threshold,
            maker_threshold=maker_threshold,
        )

        print(
            f"[MATCHING] Zapisywanie {len(final_df)} wierszy do tabeli analizy '{self.target_table_name}'..."
        )
        final_df.to_sql(
            self.target_table_name, self.engine, if_exists="replace", index=False
        )

        return final_df

    @staticmethod
    def fill_offer(record, store, comp_item):
        record[f"{store}_price"] = comp_item["price_normal"]
        record[f"{store}_price_special"] = comp_item.get("price_special")
        record[f"{store}_url"] = comp_item["url"]
        record[f"{store}_name"] = comp_item["name"]

    def match_products(self, client_df, comp_df, name_threshold, color_threshold, maker_threshold):
        """Dla każdego produktu klienta najlepsza oferta tego samego rozmiaru w każdym sklepie."""
        print("[MATCHING] Normalizacja danych do Smart Blockingu (ROZMIAR)...")
        client_df = client_df.copy()
        comp_df = comp_df.copy()
        for frame in [client_df, comp_df]:
            frame["block_size"] = frame["size"].map(size_key)
            frame["model_words"] = [
                distinctive_words(name, manufacturer)
                for name, manufacturer in zip(frame["name"], frame["manufacturer"])
            ]
            frame["match_color"] = [
                comparable_color(color, name)
                for color, name in zip(frame["color"], frame["name"])
            ]
        comp_df["result_store"] = comp_df["store"].map(self.result_column_store)

        word_frequency = Counter(
            word
            for words in client_df.drop_duplicates("name")["model_words"]
            for word in set(words)
        )

        def rare(word):
            return word_frequency.get(word, 0) <= RARE_WORD_MAX_NAMES

        def same_pattern(client_words, competitor_words):
            """Wspólne rzadkie słowo wzoru i brak rzadkiego słowa klienta spoza oferty."""
            shared = [
                word for word in client_words
                if rare(word) and any(words_similar(word, other) for other in competitor_words)
            ]
            unexplained = [
                word for word in client_words
                if rare(word) and not any(words_similar(word, other) for other in competitor_words)
            ]
            return bool(shared) and not unexplained

        matches_list = []
        pattern_matches = []
        rejected_other_model = 0
        matched_by_pattern = 0
        ambiguous_pattern = 0

        print(
            f"[MATCHING] Szukanie dopasowań przy progach (Name: {name_threshold}%, Color: {color_threshold}%, Maker: {maker_threshold}%)..."
        )
        for _, client_item in client_df.iterrows():

            base_record = {
                "client_sku": client_item["sku"],
                "client_name": client_item["name"],
                "client_price": client_item["price_normal"],
                "client_url": client_item["url"],
                "manufacturer": client_item["manufacturer"],
                "size": client_item["size"],
                "color": client_item["color"],
            }

            # Dynamiczne inicjalizowanie pustych kolumn dla sklepów konkurencji tego konkretnego klienta
            for comp_store in self.competitor_stores:
                base_record[f"{comp_store}_price"] = None
                base_record[f"{comp_store}_price_special"] = None
                base_record[f"{comp_store}_url"] = None
                base_record[f"{comp_store}_name"] = None

            candidates = comp_df[
                comp_df["block_size"] == client_item["block_size"]
            ]

            best_per_store = {}
            pattern_candidates = {}
            for _, comp_item in candidates.iterrows():
                store = comp_item["result_store"]
                if not store:
                    continue

                if not same_manufacturer(
                    str(client_item["manufacturer"]),
                    str(comp_item["manufacturer"]),
                    maker_threshold,
                ):
                    continue

                client_color = client_item["match_color"]
                comp_color = comp_item["match_color"]
                color_score = (
                    100
                    if not client_color and not comp_color
                    else fuzz.token_set_ratio(client_color, comp_color)
                )
                if color_score < color_threshold:
                    continue

                name_score = fuzz.token_set_ratio(
                    str(client_item["name"]), str(comp_item["name"])
                )

                if missing_words(comp_item["model_words"], client_item["model_words"]):
                    if name_score >= name_threshold:
                        rejected_other_model += 1
                    continue

                score = (name_score, color_score)

                if name_score >= name_threshold:
                    best = best_per_store.get(store)
                    if best is None or score > best[0]:
                        best_per_store[store] = (score, comp_item)
                elif same_pattern(client_item["model_words"], comp_item["model_words"]):
                    pattern_candidates.setdefault(store, []).append((score, comp_item))

            for store, offers in pattern_candidates.items():
                if store in best_per_store:
                    continue
                if len({comp_item["name"] for _, comp_item in offers}) > 1:
                    ambiguous_pattern += 1
                    continue
                _, comp_item = max(offers, key=lambda pair: pair[0])
                pattern_matches.append((len(matches_list), client_item["name"], store, comp_item))

            for store, (_, comp_item) in best_per_store.items():
                self.fill_offer(base_record, store, comp_item)

            matches_list.append(base_record)

        by_offer = {}
        for match in pattern_matches:
            _, _, store, comp_item = match
            key = (store, comp_item["name"], comp_item["block_size"])
            by_offer.setdefault(key, []).append(match)
        for matches in by_offer.values():
            if len({client_name for _, client_name, _, _ in matches}) > 1:
                offer_color = name_color(matches[0][3]["name"])
                matches = [
                    match for match in matches
                    if offer_color and name_color(match[1]) == offer_color
                ]
                if len({client_name for _, client_name, _, _ in matches}) != 1:
                    ambiguous_pattern += 1
                    continue
            for index, _, store, comp_item in matches:
                self.fill_offer(matches_list[index], store, comp_item)
                matched_by_pattern += 1

        print(
            f"[MATCHING] Odrzucono par z innym wzorem w nazwie: {rejected_other_model}"
        )
        print(
            f"[MATCHING] Pary po wzorze (nazwa poniżej progu): {matched_by_pattern}, "
            f"pominięte niejednoznaczne: {ambiguous_pattern}"
        )
        print("[MATCHING] Generowanie płaskiej tabeli wynikowej...")
        return pd.DataFrame(matches_list)
