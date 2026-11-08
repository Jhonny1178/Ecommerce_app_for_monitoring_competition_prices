# Fakty o cenach konkurencji i krótkie (max 3 zdania) uzasadnienie rekomendacji dla właściciela sklepu.
import re

MAX_SENTENCES = 3
# Koniec zdania tylko przed wielką literą, żeby nie ciąć na "ok. 12 zł".
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])")


def format_pln(value):
    """Kwota po polsku: 509 zł albo 514,33 zł."""
    value = round(float(value), 2)
    if abs(value - round(value)) < 0.005:
        return f"{int(round(value))} zł"
    return f"{value:.2f}".replace(".", ",") + " zł"


def mentions_amount(text, value):
    """Czy tekst zawiera kwotę (np. 179 albo 514,33) jako osobną liczbę."""
    amount = format_pln(value).replace(" zł", "")
    return re.search(rf"(?<![\d,.]){re.escape(amount)}(?![\d]|,\d)", str(text or "")) is not None


def price_direction(our_price, price):
    # "keep" tylko przy tej samej cenie, inaczej zdanie "Zostaw" nie zgadzałoby się z pokazaną ceną.
    if our_price <= 0:
        return "set"
    if abs(price - our_price) < 0.005:
        return "keep"
    return "lower" if price < our_price else "raise"


def _relation(price, reference):
    diff = round(price - reference, 2)
    if abs(diff) < 0.005:
        return "równa"
    return f"{'wyższa' if diff > 0 else 'niższa'} o {format_pln(abs(diff))}"


# Słowa porównania, które przy złym kierunku oznaczają sprzeczność z faktami.
_BELOW_WORDS = r"(?:poniżej|pod|niżej|niższ\w*|tańsz\w*|taniej)"
_ABOVE_WORDS = r"(?:powyżej|nad|wyżej|wyższ\w*|droższ\w*|drożej)"


# Zdania o brakujących danych ("brak historii", "nie mamy informacji") nic klientowi nie mówią.
_MISSING_DATA = re.compile(
    r"\b(?:brak\w*|nie\s+(?:ma|mamy|posiadamy|znamy))\s+(?:\w+\s+){0,2}(?:danych|informacj\w*|histori\w*)",
    re.IGNORECASE,
)


def contradicts_facts(text, facts, has_history=False):
    """Czy tekst modelu przeczy faktom albo pisze o danych, których model nie dostał."""
    text = str(text or "")
    price, avg = facts["suggested"], facts["avg"]

    if _MISSING_DATA.search(text):
        return True
    if not has_history and re.search(r"histori", text, re.IGNORECASE):
        return True

    if avg is not None and abs(price - avg) >= 0.005:
        wrong = _BELOW_WORDS if price > avg else _ABOVE_WORDS
        if re.search(rf"\b{wrong}\b[^.]{{0,25}}\bśredni", text, re.IGNORECASE):
            return True

    claim = r"(?:będzie|jest|jesteś|będziesz|byłaby)\s+(?:\w+\s+)?"
    if facts["cheaper"] > 0 and re.search(rf"{claim}najtańsz", text, re.IGNORECASE):
        return True
    if facts["more_expensive"] > 0 and re.search(rf"{claim}najdroższ", text, re.IGNORECASE):
        return True

    return False


def market_facts(prices, our_price, suggested):
    count = len(prices)
    change = round(suggested - our_price, 2)

    return {
        "count": count,
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "avg": round(sum(prices) / count, 2) if prices else None,
        "our_price": our_price,
        "suggested": suggested,
        "change": change,
        "change_pct": round(change / our_price * 100, 1) if our_price > 0 else None,
        "direction": price_direction(our_price, suggested),
        "cheaper": sum(1 for p in prices if p < suggested),
        "more_expensive": sum(1 for p in prices if p > suggested),
    }


def limit_sentences(text, limit=MAX_SENTENCES):
    sentences = [s for s in _SENTENCE_BREAK.split(str(text or "").strip()) if s]
    return " ".join(sentences[:limit])


def action_sentence(our_price, price):
    """Zdanie z decyzją, zawsze zgodne z ceną końcową."""
    return {
        "lower": f"Obniż cenę z {format_pln(our_price)} do {format_pln(price)}.",
        "raise": f"Podnieś cenę z {format_pln(our_price)} do {format_pln(price)}.",
        "keep": f"Zostaw obecną cenę {format_pln(our_price)}.",
        "set": f"Ustaw cenę {format_pln(price)}.",
    }[price_direction(our_price, price)]


def build_reason(our_price, price, ai_reason):
    """Decyzja z kodu + max 2 zdania uzasadnienia od modelu AI."""
    return f"{action_sentence(our_price, price)} {limit_sentences(ai_reason, MAX_SENTENCES - 1)}"


def _offers_word(count):
    if count == 1:
        return "oferta"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "oferty"
    return "ofert"


def fallback_reason(facts):
    """Uzasadnienie bez AI, z samych faktów (gdy model jest niedostępny)."""
    action = action_sentence(facts["our_price"], facts["suggested"])

    if facts["count"] == 0:
        return f"{action} Nie znaleźliśmy ofert konkurencji, więc sugestia to obecna cena powiększona o 20%."

    if facts["count"] == 1:
        market = f"Jedyna oferta konkurencji kosztuje {format_pln(facts['min'])}."
    else:
        market = (
            f"Konkurencja ({facts['count']} {_offers_word(facts['count'])}) sprzedaje ten produkt "
            f"za {format_pln(facts['min'])}–{format_pln(facts['max'])}, średnio {format_pln(facts['avg'])}."
        )

    if facts["cheaper"] == 0 and facts["more_expensive"] == 0:
        position = "Twoja cena będzie taka sama jak u konkurencji."
    elif facts["cheaper"] == 0:
        position = "Nikt z konkurencji nie będzie tańszy od Ciebie."
    elif facts["more_expensive"] == 0:
        position = "Nikt z konkurencji nie będzie droższy od Ciebie."
    else:
        position = f"Twoja oferta będzie tańsza niż {facts['more_expensive']} z {facts['count']} ofert konkurencji."

    return f"{action} {market} {position}"


def _format_date(iso_date):
    year, month, day = str(iso_date)[:10].split("-")
    return f"{day}.{month}.{year}"


def history_lines(history):
    """Linie o ostatnich zmianach cen; pusta lista, gdy zmian nie było (wtedy model nic o historii nie wie)."""
    lines = []
    for change in history or []:
        before, after = change["cena_przed"], change["cena_po"]
        if abs(after - before) < 0.005:
            continue
        verb = "obniżył" if after < before else "podniósł"
        lines.append(
            f"- {change['sklep']} {verb} cenę z {format_pln(before)} do {format_pln(after)} ({_format_date(change['data'])})"
        )
    return lines


def facts_for_prompt(facts, history=()):
    """Fakty jako tekst dla modelu AI; liczby są już policzone, model ma ich nie zmieniać."""
    direction = {
        "lower": "obniżka",
        "raise": "podwyżka",
        "keep": "bez zmian",
        "set": "pierwsza cena (brak obecnej)",
    }[facts["direction"]]
    change_pct = f", {facts['change_pct']:+.1f}%".replace(".", ",") if facts["change_pct"] is not None else ""

    lines = [
        f"- liczba ofert konkurencji: {facts['count']}",
        f"- ceny konkurencji: od {format_pln(facts['min'])} do {format_pln(facts['max'])}, średnio {format_pln(facts['avg'])}",
        f"- nasza obecna cena: {format_pln(facts['our_price'])}",
        f"- cena wyliczona przez algorytm: {format_pln(facts['suggested'])} ({direction} o {format_pln(abs(facts['change']))}{change_pct})",
        f"- cena wyliczona względem średniej rynkowej: {_relation(facts['suggested'], facts['avg'])}",
        f"- cena wyliczona względem najtańszej oferty: {_relation(facts['suggested'], facts['min'])}",
        f"- przy tej cenie tańszych ofert konkurencji: {facts['cheaper']}, droższych: {facts['more_expensive']}",
    ]

    changes = history_lines(history)
    if changes:
        lines.append("- ostatnie zmiany cen u konkurencji (30 dni):")
        lines.extend(f"  {line}" for line in changes)

    return "\n".join(lines)
