import json
import re
import os
from core.groq_manager import GroqManager

def generate_scraper_from_html(html_file_path: str, output_json_path: str):
    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Nie znaleziono pliku {html_file_path}.")
        return

    manager = GroqManager()

    system_prompt = """
    Jesteś wybitnym inżynierem danych. Twoim zadaniem jest stworzenie "Mapy Selektorów CSS" dla podanego kodu HTML strony produktowej.
    ZASADY BEZWZGLĘDNE:
    1. Zwróć odpowiedź WYŁĄCZNIE w poprawnym formacie JSON. Żadnych wstępów, formy konwersacyjnej ani znaczników markdown.
    2. Jeśli jakiegoś elementu nie ma na stronie, przypisz mu wartość `null`.
    3. Musisz zwrócić DOKŁADNIE takie klucze, o jakie prosi użytkownik.
    """

    user_prompt = f"""
    Przeanalizuj poniższy, odchudzony HTML produktu. Znajdź w nim kluczowe informacje i stwórz precyzyjne ścieżki (selektory CSS), które pozwolą je wyciągnąć za pomocą BeautifulSoup.select().

    WYMAGANA STRUKTURA JSON:
    {{
        "product_name": "Selektor CSS do nazwy produktu (np. 'h1.product-title')",
        "product_id": "Selektor CSS do ID produktu",
        "brand": "Selektor CSS do marki (używaj klas lub atrybutów, np. 'div[data-producer=\"true\"] a')",
        "color": "Względny selektor CSS do głównego koloru produktu, jeśli występuje poza tabelą (np. 'b.apropo__features-product--label'). Jeśli go nie ma, wpisz null.",
        "old_price": "Selektor CSS do starej/przekreślonej ceny",
        "special_price": "Selektor CSS do nowej/promocyjnej ceny",

        "breadcrumbs_wrapper": "Selektor CSS do głównego kontenera okruszków (np. 'nav.breadcrumb' lub 'ol.breadcrumbs')",
        "breadcrumb_item": "Selektor CSS do pojedynczego linku wewnątrz okruszków (np. 'li a')",

        "specifications_row": "Selektor CSS do pojedynczego wiersza z cechą (np. 'tr' lub 'div.dictionary__param')",
        "spec_name": "Względny selektor CSS do NAZWY cechy wewnątrz specifications_row (np. 'span.name')",
        "spec_value": "Względny selektor CSS do WARTOŚCI cechy wewnątrz specifications_row (np. 'span.value')",

        "variants_wrapper": "Selektor CSS do głównego kontenera wariantów na obecnej stronie (np. 'select.sizes'). UWAGA: Szukaj tylko wariantów zmieniających cenę/rozmiar na tej samej stronie. Zignoruj listy krajów wysyłki!",
        "variant_option": "Względny selektor CSS do pojedynczego wariantu wewnątrz variants_wrapper (np. 'option')",
        "variant_price_attribute": "Nazwa atrybutu HTML (NIE selektor!), w którym ukryta jest cena wariantu (np. 'data-price' lub 'value'). Jeśli wariant nie ma w atrybucie unikalnej ceny, wpisz null",
        "description": "Selektor CSS do bloku z głównym opisem produktu (np. 'div.product-description' lub 'div.opis')"
    }}

    ZASADY BUDOWANIA SELEKTORÓW:
    1. PRIORYTET: Jeśli widzisz atrybuty `data-testid`, `data-id` itp., UŻYWAJ ICH. Są odporne na zmiany (np. `[data-testid='product-price']`).
    2. ZABRONIONE: Nie używaj pseudoklas (`:first-child`, `:nth-child`) ani selektorów rodzeństwa (`+`, `~`).
    3. ZABRONIONE ZMYŚLANIE: Kopiuj nazwy atrybutów DOKŁADNIE 1:1 z kodu HTML. Jeśli główna cena ma atrybut `data-testid='product-price'`, wpisz go dokładnie tak. NIE zmyślaj atrybutów typu 'sale-price', jeśli ich fizycznie nie ma w kodzie.
    4. Dla `spec_name` i `spec_value` podawaj ścieżki WZGLĘDNE (będą wywoływane w pętli). Szukaj wspólnych kontenerów zawierających cechy i wymiary.

    KOD HTML DO ANALIZY:
    {html_content}
    """

    try:
        print("Wysyłam żądanie do API Groq. Analiza HTML i generowanie Mapy Selektorów...")
        
        def _call_groq(client):
            return client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model="openai/gpt-oss-120b",
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=2000
            )

        chat_completion = manager.execute_with_fallback(_call_groq)

        response_text = chat_completion.choices[0].message.content

        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            clean_json_str = match.group(0)
        else:
            clean_json_str = response_text

        parsed_json = json.loads(clean_json_str)

        with open(output_json_path, "w", encoding="utf-8") as json_file:
            json.dump(parsed_json, json_file, ensure_ascii=False, indent=4)

        print(f"Sukces! Poprawna Mapa Selektorów została zapisana w: {output_json_path}")

    except json.JSONDecodeError as e:
        print(f"\n[BŁĄD KRYTYCZNY] Model LLM nie zwrócił poprawnego formatu JSON!")
        print(f"Szczegóły błędu: {e}")
        print(f"Surowa odpowiedź modelu przed próbą zapisu:\n{response_text}")
        raise
    except Exception as e:
        print(f"\nBłąd łączności lub API: {e}")
        raise


if __name__ == "__main__":
    generate_scraper_from_html("dataset_dla_llm.txt", "selectors_map.json")