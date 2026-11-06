import requests
import gzip
import re
from urllib.parse import urlparse

from core.url_safety import safe_get

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}


def filter_grouped_products(urls_set):
    print(f"\nRozpoczynam filtrowanie ewidentnych stron zbiorczych (Grouped Products)...")
    sorted_urls = sorted(list(urls_set), key=len, reverse=True)
    final_urls = set()
    usunięte_zbiorcze = 0

    for url in sorted_urls:
        base = url.replace('.html', '').rstrip('/')
        base_core = re.sub(r'-\d+$', '', base)
        base_pattern = base_core + '-'

        is_grouped_parent = False
        for longer_url in final_urls:
            if longer_url.startswith(base_pattern):
                is_grouped_parent = True
                break

        if not is_grouped_parent:
            final_urls.add(url)
        else:
            usunięte_zbiorcze += 1

    print(f"-> Usunięto stron zbiorczych: {usunięte_zbiorcze}")
    return final_urls


MAX_SITEMAPS = 200


def get_product_links(sitemap_urls):
    if isinstance(sitemap_urls, str):
        sitemap_urls = [sitemap_urls]

    unikalne_linki_produktow = set()

    sitemaps_to_process = list(sitemap_urls)
    processed_sitemaps = set()

    # Odrzucamy TYLKO oczywiste strony techniczne i pliki.
    # Resztę (w tym dziwne kategorie) niech sprawdzi Scrapy.
    zakazane_slowa = ['/kontakt', '/regulamin', '/koszyk', '/polityka', '/polityka-prywatnosci']
    zakazane_rozszerzenia = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf', '.svg']

    while sitemaps_to_process and len(processed_sitemaps) < MAX_SITEMAPS:
        url = sitemaps_to_process.pop(0)

        if url in processed_sitemaps:
            continue
        processed_sitemaps.add(url)

        print(f"Przetwarzanie sitemapy: {url}")
        try:
            response = safe_get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()

            content = gzip.decompress(response.content) if url.endswith('.gz') else response.content

            content_str = content.decode('utf-8', errors='ignore')
            tymczasowe_linki = re.findall(r'<loc>\s*(.*?)\s*</loc>', content_str, re.IGNORECASE)

            print(f"   - Znaleziono {len(tymczasowe_linki)} linków/węzłów.")

            for link in tymczasowe_linki:
                link = link.replace('<![CDATA[', '').replace(']]>', '').strip()
                link_lower = link.lower()

                if link_lower.endswith('.xml') or link_lower.endswith('.xml.gz'):
                    sitemaps_to_process.append(link)
                    continue

                path = urlparse(link_lower).path
                if path in ['', '/']:
                    continue

                if any(zakazane in link_lower for zakazane in zakazane_slowa):
                    continue
                if any(link_lower.endswith(ext) for ext in zakazane_rozszerzenia):
                    continue

                unikalne_linki_produktow.add(link)

        except Exception as e:
            print(f"Błąd przy sitemapie {url}: {e}")

    czyste_linki = filter_grouped_products(unikalne_linki_produktow)
    return czyste_linki