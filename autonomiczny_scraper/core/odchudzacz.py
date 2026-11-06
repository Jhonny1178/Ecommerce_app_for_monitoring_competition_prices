import requests
import re
from bs4 import BeautifulSoup, Comment

from core.url_safety import safe_get

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}

def wybierz_pewne_probki(linki_set, ilosc=3):
    linki_lista = sorted(list(linki_set), key=len)

    if len(linki_lista) < ilosc:
        return linki_lista

    start_idx = int(len(linki_lista) * 0.2)
    end_idx = int(len(linki_lista) * 0.8)
    bezpieczny_srodek = linki_lista[start_idx:end_idx]

    step = len(bezpieczny_srodek) // ilosc
    probki = [bezpieczny_srodek[i * step] for i in range(ilosc)]

    return probki

def basic_clean(soup):
    tags_to_remove = ['script', 'style', 'svg', 'path', 'noscript', 'iframe', 'meta', 'link', 'header', 'aside']
    for tag in tags_to_remove:
        for element in soup.find_all(tag):
            element.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    allowed_attributes = ['id', 'class', 'name', 'type', 'value']
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue

        attrs_to_remove = []
        for attr in tag.attrs:
            if attr in allowed_attributes:
                continue
            elif attr.startswith('data-') and len(str(tag.get(attr, ''))) < 30:
                continue
            else:
                attrs_to_remove.append(attr)

        for attr in attrs_to_remove:
            del tag[attr]

    return soup


def risky_clean(soup):
    for tag in soup.find_all(['img', 'picture', 'footer']):
        tag.decompose()

    classes_to_remove = ['newsletter', 'cookie', 'popup', 'modal']
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue

        classes = tag.get('class', [])
        if any(any(bad_class in str(c).lower() for bad_class in classes_to_remove) for c in classes):
            tag.decompose()

    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue

        if len(tag.get_text(strip=True)) == 0 and not tag.contents:
            if tag.name not in ['br', 'hr', 'input']:
                tag.decompose()

    return soup


def get_html_string(soup):
    html_str = str(soup)
    html_str = re.sub(r'>\s+<', '><', html_str)
    return html_str.strip()


def build_llm_dataset(urls_to_process, output_filename):
    final_output = []
    total_chars = 0
    processed_count = 0

    print("Rozpoczynam mądre odchudzanie HTML...\n")

    for i, url in enumerate(urls_to_process):
        link_number = i + 1
        print(f"Pobieranie i analiza: {url} (Link {link_number})")

        try:
            response = safe_get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            if not soup.body:
                print(" -> Brak tagu <body> na stronie!")
                continue

            clean_soup = basic_clean(soup.body)
            html_str = get_html_string(clean_soup)

            if link_number == 1:
                if len(html_str) > 15000:
                    print(" -> [Krok 2.1.1] HTML > 15000 znaków. Wdrażam czyszczenie ryzykowne.")
                    clean_soup = risky_clean(clean_soup)
                    html_str = get_html_string(clean_soup)

                    if len(html_str) > 15000:
                        ucieto = len(html_str) - 14800
                        html_str = html_str[:14800]
                        print(f" -> [Krok 2.1.2] Nadal > 15000. Ucinam na sztywno {ucieto} znaków od dołu.")

                block = f"\n--- URL: {url} ---\n{html_str}\n"
                final_output.append(block)
                total_chars += len(block)
                processed_count += 1

                if total_chars > 9000:
                    print(f" -> [Krok 2.1.3 / 2.2] Link 1 ma {total_chars} znaków (> 9000). Zamykam zbiór.")
                    break
                else:
                    print(f" -> [Krok 2.1.4 / 2.3] Link 1 ma {total_chars} znaków. Przechodzę do Linku 2.")
                    continue

            elif link_number == 2:
                block = f"\n--- URL: {url} ---\n{html_str}\n"

                if total_chars + len(block) > 15000:
                    print(" -> [Krok 3.1.1] Suma > 15000. Wdrażam czyszczenie ryzykowne dla Linku 2.")
                    clean_soup = risky_clean(clean_soup)
                    html_str = get_html_string(clean_soup)
                    block = f"\n--- URL: {url} ---\n{html_str}\n"

                    if total_chars + len(block) > 15000:
                        print(
                            " -> [Krok 3.1.2] Suma po ryzykownym nadal > 15000. Odrzucam Link 2. Zapisuję tylko Link 1.")
                        break

                final_output.append(block)
                total_chars += len(block)
                processed_count += 1

                if total_chars > 12000:
                    print(f" -> [Krok 3.1.3] Suma wynosi {total_chars} znaków (> 12000). Zamykam zbiór.")
                    break
                else:
                    print(f" -> [Krok 3.1.4] Suma wynosi {total_chars} znaków. Przechodzę do Linku 3.")
                    continue

            elif link_number == 3:
                block = f"\n--- URL: {url} ---\n{html_str}\n"

                if total_chars + len(block) > 15000:
                    print(" -> [Krok 4] Suma dla 3 linków > 15000. Odrzucam Link 3. Zapisuję 2 linki.")
                    break

                final_output.append(block)
                total_chars += len(block)
                processed_count += 1
                print(f" -> [Krok 4] Suma wynosi {total_chars} znaków. Zapisuję 3 linki.")
                break

        except Exception as e:
            print(f" -> Błąd podczas pobierania {url}: {e}")

    final_text = "".join(final_output)

    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(final_text)

    print(f"\nPodsumowanie:")
    print(f"- Zapisano do: {output_filename}")
    print(f"- Liczba linków w pliku: {processed_count}")
    print(f"- Łączna waga dla LLM: {total_chars} znaków")