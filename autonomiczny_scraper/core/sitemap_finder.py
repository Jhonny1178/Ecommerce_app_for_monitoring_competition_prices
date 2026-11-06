import requests
import re
import gzip
from urllib.parse import urljoin, urlparse

from core.url_safety import safe_get, safe_head

# Maskujemy się jako prawdziwa przeglądarka
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

COMMON_PATHS = [
    '/sitemap.xml', '/sitemap_index.xml', '/sitemap.xml.gz', '/sitemap1.xml',
    '/sitemap/sitemap.xml', '/sitemap-index.xml', '/sitemap_products.xml'
]


def format_domain(url):
    if not url.startswith('http'):
        url = 'https://' + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_valid_sitemap(url):
    try:
        response = safe_head(url, headers=HEADERS, timeout=5, allow_redirects=True)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '').lower()
            if 'xml' in content_type or 'gzip' in content_type or url.endswith('.gz'):
                return True
            # Fallback dla źle skonfigurowanych serwerów
            partial_body = safe_get(url, headers=HEADERS, timeout=5, stream=True)
            chunk = next(partial_body.iter_content(chunk_size=100)).decode('utf-8', errors='ignore')
            if '<?xml' in chunk or '<urlset' in chunk or '<sitemapindex' in chunk:
                return True
    except requests.RequestException:
        pass
    return False


def rozpakuj_indeks(url):
    """
    Wchodzi do sitemapy, dekoduje .gz (jeśli istnieje) i jeśli to indeks,
    wyciąga wszystkie linki podrzędne z ominięciem tagów takich jak <lastmod>.
    """
    print(f"   [Analiza] Zaglądam do wnętrza: {url}")
    try:
        response = safe_get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return [url]

        content = response.content
        # Dekompresja w lotcie jeśli plik to .gz
        if url.endswith('.gz') or response.headers.get('Content-Encoding') == 'gzip':
            try:
                content = gzip.decompress(content)
            except Exception:
                pass

        content_str = content.decode('utf-8', errors='ignore')

        # Jeśli to indeks, rozpakowujemy pod-linki
        if '<sitemapindex' in content_str.lower():
            print("   [!] Wykryto 'Sitemap Index'. Wyciągam sitemapy podrzędne...")
            # Ten Regex gwarantuje, że nie przyklei się data <lastmod> !
            nested_links = re.findall(r'<loc>\s*(.*?)\s*</loc>', content_str, re.IGNORECASE)

            czyste_linki = []
            for link in nested_links:
                link = link.replace('<![CDATA[', '').replace(']]>', '').strip()
                czyste_linki.append(link)

            return czyste_linki
        else:
            # To już jest ostateczna sitemapa, zwracamy oryginał
            return [url]

    except Exception as e:
        print(f"   [Błąd dekodowania] {url} -> {e}")
        return [url]


def find_sitemap(store_url):
    base_url = format_domain(store_url)
    print(f"\n==================================================")
    print(f"Rozpoczynam misję poszukiwawczą dla: {base_url}")
    print(f"==================================================")
    found_sitemaps = set()

    # KROK 1: Szukanie w robots.txt
    robots_url = urljoin(base_url, '/robots.txt')
    print("-> Krok 1: Przeszukanie pliku robots.txt...")
    try:
        response = safe_get(robots_url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            for line in response.text.splitlines():
                if line.lower().startswith('sitemap:'):
                    sitemap_link = line.split(':', 1)[1].strip()
                    if is_valid_sitemap(sitemap_link):
                        found_sitemaps.add(sitemap_link)
    except requests.RequestException:
        print("   [!] Nie można pobrać robots.txt")

    if found_sitemaps:
        print(f"   [OK] Sukces! Znaleziono {len(found_sitemaps)} deklaracji w robots.txt.")
    else:
        # KROK 2: Brute-Force (Strzelanie w standardowe ścieżki)
        print("-> Krok 2: Brak deklaracji. Uruchamiam skaner ścieżek (Brute-Force)...")
        for path in COMMON_PATHS:
            guess_url = urljoin(base_url, path)
            if is_valid_sitemap(guess_url):
                print(f"   [OK] Znalazłem sitemapę na ukrytej ścieżce: {path}")
                found_sitemaps.add(guess_url)
                break

    if not found_sitemaps:
        print("-> [PORAŻKA] Nie udało się zlokalizować żadnej sitemapy.")
        return []

    # KROK 3: ROZPAKOWYWANIE INDEKSÓW (Magia)
    print("-> Krok 3: Analiza zawartości i rozpakowywanie indeksów...")
    ostateczne_sitemapy = []
    for sitemap in found_sitemaps:
        rozpakowane = rozpakuj_indeks(sitemap)
        ostateczne_sitemapy.extend(rozpakowane)

    # Usuwamy duplikaty (np. HTTP i HTTPS kierujące w to samo miejsce)
    ostateczne_sitemapy = list(set(ostateczne_sitemapy))

    return ostateczne_sitemapy