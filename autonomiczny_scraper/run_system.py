import glob
import os
import shutil
import tempfile
import psycopg2
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List,Optional
import uvicorn
from dotenv import load_dotenv, find_dotenv
import re
load_dotenv(find_dotenv())

# --- Importy Twojego silnika AI ---
from core.sitemap_finder import find_sitemap
from core.sitemap_processor import get_product_links
from core.odchudzacz import build_llm_dataset, wybierz_pewne_probki
from core.prompt import generate_scraper_from_html
from core.spider_builder import generate_spider_file

app = FastAPI(title="Scraper Generator API")

# --- Konfiguracja bazy i ścieżek ---
DB_CONFIG = {
    "host": os.environ.get("APP_DB_HOST", "localhost"),
    "port": int(os.environ.get("APP_DB_PORT", 5434)),
    "dbname": os.environ.get("APP_DB_NAME", "ecommerce_data"),
    "user": os.environ.get("APP_DB_USER", "postgres"),
    "password": os.environ.get("APP_DB_PASSWORD"),
}

# Definiujemy ścieżkę do projektu kolegi.
# Zmienna środowiskowa SPIDERS_DIR przyda się później w Dockerze.
SPIDERS_OUTPUT_DIR = os.environ.get("SPIDERS_DIR", "../ecommerce_price_comparer/spiders")
MAX_STORE_NAME_LENGTH = 30


class GenerationRequest(BaseModel):
    request_id: int
    user_id: int
    client_id: Optional[int] = None
    store_slug: str
    urls: List[str]


def pobierz_nazwe_sklepu(url):
    if "://" not in url:
        url = f"https://{url}"
    domain = urlparse(url).netloc
    name = domain.replace('www.', '').split('.')[0]
    name = re.sub(r'[^a-zA-Z0-9]', '_', name).lower()
    return name[:MAX_STORE_NAME_LENGTH]


def przygotuj_srodowisko_klienta(nazwa_firmy: str):
    """Tworzy bezpieczny folder dla klienta wewnątrz projektu Scrapy."""
    # Oczyszczamy nazwę firmy z dziwnych znaków i spacji, by była poprawną nazwą w Pythonie
    bezpieczna_nazwa = "".join(c if c.isalnum() else "_" for c in nazwa_firmy).lower()
    katalog_klienta = os.path.join(SPIDERS_OUTPUT_DIR, bezpieczna_nazwa)

    # 1. Tworzymy folder
    os.makedirs(katalog_klienta, exist_ok=True)

    # 2. Tworzymy plik __init__.py, bez którego Scrapy nie zauważy pająków!
    init_path = os.path.join(katalog_klienta, "__init__.py")
    if not os.path.exists(init_path):
        open(init_path, 'a').close()

    return katalog_klienta


def wniosek_istnieje(cur, request_id: int) -> bool:
    cur.execute("SELECT 1 FROM onboarding_requests WHERE id = %s", (request_id,))
    return cur.fetchone() is not None


def usun_pliki_wniosku(request_id: int) -> int:
    usuniete = 0
    katalogi = set()

    for wzorzec in (f"*/r{request_id}_*", f"*/__pycache__/r{request_id}_*"):
        for sciezka in glob.glob(os.path.join(SPIDERS_OUTPUT_DIR, wzorzec)):
            os.remove(sciezka)
            usuniete += 1
            katalogi.add(sciezka[:sciezka.index(os.sep, len(SPIDERS_OUTPUT_DIR) + 1)])

    for katalog in katalogi:
        if not set(os.listdir(katalog)) - {"__init__.py", "__pycache__"}:
            shutil.rmtree(katalog)

    return usuniete


def log_scraper_step(cur, user_id: int, client_id: Optional[int], request_id: int, url: str | None, step: str, status: str, message: str = ""):
    try:
        cur.execute("""
            INSERT INTO scraper_logs (
                user_id,
                client_id,
                request_id,
                url,
                step,
                status,
                message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            client_id,
            request_id,
            url,
            step,
            status,
            message
        ))
    except Exception as e:
        print(f"[!] Błąd zapisu logu: {e}")


def register_generated_spider(
    cur,
    client_id: Optional[int],
    request_id: int,
    store_slug: str,
    competitor_url: str,
    competitor_name: str,
    spider_name: str,
    spider_module: str,
    spider_path: str
):
    """
    Rejestruje wygenerowany scraper.

    Przed akceptacją admina:
      client_id = NULL
      request_id = ID wniosku
      output_table = NULL

    Po akceptacji admina auth.py przypina client_id i ustawia output_table.
    """

    if client_id is None:
        cur.execute("""
            INSERT INTO scraper_registry (
                client_id,
                request_id,
                store_slug,
                competitor_url,
                competitor_name,
                spider_name,
                spider_module,
                spider_path,
                output_table,
                status
            )
            VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, NULL, 'generated')
            ON CONFLICT (request_id, spider_name)
            WHERE client_id IS NULL
            DO UPDATE SET
                store_slug = EXCLUDED.store_slug,
                competitor_url = EXCLUDED.competitor_url,
                competitor_name = EXCLUDED.competitor_name,
                spider_module = EXCLUDED.spider_module,
                spider_path = EXCLUDED.spider_path,
                output_table = NULL,
                status = 'generated',
                last_error = NULL,
                generated_at = NOW()
        """, (
            request_id,
            store_slug,
            competitor_url,
            competitor_name,
            spider_name,
            spider_module,
            spider_path
        ))

        return

    output_table = f"{store_slug}_competitors"

    cur.execute("""
        INSERT INTO scraper_registry (
            client_id,
            request_id,
            store_slug,
            competitor_url,
            competitor_name,
            spider_name,
            spider_module,
            spider_path,
            output_table,
            status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'generated')
        ON CONFLICT (client_id, spider_name)
        WHERE client_id IS NOT NULL
        DO UPDATE SET
            request_id = EXCLUDED.request_id,
            store_slug = EXCLUDED.store_slug,
            competitor_url = EXCLUDED.competitor_url,
            competitor_name = EXCLUDED.competitor_name,
            spider_module = EXCLUDED.spider_module,
            spider_path = EXCLUDED.spider_path,
            output_table = EXCLUDED.output_table,
            status = 'generated',
            last_error = NULL,
            generated_at = NOW()
    """, (
        client_id,
        request_id,
        store_slug,
        competitor_url,
        competitor_name,
        spider_name,
        spider_module,
        spider_path,
        output_table
    ))


def mark_onboarding_status(cur, request_id: int, status: str):
    pass # Disabled so it doesn't interfere with the user's onboarding flow (onboarding_required -> awaiting_payment -> pending_admin)

def uruchom_autonomiczny_potok(
    cur,
    request_id: int,
    user_id: int,
    client_id: Optional[int],
    store_slug: str,
    url_sklepu: str,
    docelowy_limit_scrapowania=None
):
    with tempfile.TemporaryDirectory() as katalog_roboczy:
        return _generuj_scraper(
            cur,
            request_id,
            user_id,
            client_id,
            store_slug,
            url_sklepu,
            docelowy_limit_scrapowania,
            katalog_roboczy
        )


def _generuj_scraper(
    cur,
    request_id: int,
    user_id: int,
    client_id: Optional[int],
    store_slug: str,
    url_sklepu: str,
    docelowy_limit_scrapowania,
    katalog_roboczy: str
):
    if not wniosek_istnieje(cur, request_id):
        print(f"[!] Wniosek #{request_id} już nie istnieje. Pomijam {url_sklepu}.")
        return False

    nazwa_sklepu = pobierz_nazwe_sklepu(url_sklepu)

    # To jest nazwa spidera, którą później wpiszemy do scraper_registry.
    # Musi być taka sama jak name = "..." wewnątrz wygenerowanego spidera.
    nazwa_spidera = f"r{request_id}_{nazwa_sklepu}"
    spider_name = f"{nazwa_spidera}_spider"

    print(f"\nURUCHAMIAM AI DLA SKLEPU: {nazwa_sklepu.upper()} (Klient: {store_slug})")

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Sitemap Discovery",
        "info",
        "Rozpoczynam wyszukiwanie sitemapy"
    )

    # KROK 0-2: Sitemapy i linki
    znalezione_sitemapy = find_sitemap(url_sklepu)

    if not znalezione_sitemapy:
        print(f"[!] Błąd: Nie znaleziono sitemapy dla {url_sklepu}.")

        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            url_sklepu,
            "Sitemap Discovery",
            "error",
            "Nie znaleziono sitemapy"
        )

        return False

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Sitemap Discovery",
        "success",
        "Sitemapa znaleziona"
    )

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Product Links",
        "info",
        "Pobieranie linków produktów"
    )

    czyste_linki = get_product_links(znalezione_sitemapy)

    if not czyste_linki:
        print(f"[!] Błąd: Brak linków dla {url_sklepu}.")

        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            url_sklepu,
            "Product Links",
            "error",
            "Brak linków w sitemapie"
        )

        return False

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Product Links",
        "success",
        f"Pobrano {len(czyste_linki)} linków"
    )

    linki_do_pracy = (
        list(czyste_linki)[:docelowy_limit_scrapowania]
        if docelowy_limit_scrapowania
        else list(czyste_linki)
    )

    probki_dla_llm = wybierz_pewne_probki(set(linki_do_pracy), ilosc=3)
    plik_datasetu = os.path.join(katalog_roboczy, "dataset_dla_llm.txt")
    plik_mapy = os.path.join(katalog_roboczy, "selectors_map.json")

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "LLM Dataset",
        "info",
        "Generowanie zestawu danych HTML dla LLM"
    )

    build_llm_dataset(probki_dla_llm, plik_datasetu)

    if not os.path.exists(plik_datasetu) or os.path.getsize(plik_datasetu) == 0:
        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            url_sklepu,
            "LLM Dataset",
            "error",
            "Nie udało się pobrać próbek HTML"
        )

        return False

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "LLM Dataset",
        "success",
        "Wygenerowano próbki dla LLM"
    )

    # Generowanie Mapy
    print("[AI] Generowanie mapy CSS...")

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "CSS Mapping",
        "info",
        "Uruchomiono model LLM do mapowania CSS"
    )

    generate_scraper_from_html(
        plik_datasetu,
        plik_mapy
    )

    if not os.path.exists(plik_mapy):
        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            url_sklepu,
            "CSS Mapping",
            "error",
            "Model LLM nie zwrócił mapy selektorów"
        )

        return False

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "CSS Mapping",
        "success",
        "Mapa wygenerowana"
    )

    if not wniosek_istnieje(cur, request_id):
        print(f"[!] Wniosek #{request_id} już nie istnieje. Nie zapisuję spidera.")
        return False

    # KROK 6: Zapis spidera do folderu klienta po store_slug
    katalog_klienta = przygotuj_srodowisko_klienta(store_slug)
    plik_spidera = os.path.join(katalog_klienta, f"{spider_name}.py")

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Spider Builder",
        "info",
        "Tworzenie pliku pająka Scrapy"
    )

    generate_spider_file(
        plik_mapy,
        plik_spidera,
        nazwa_spidera,
        links=czyste_linki
    )

    log_scraper_step(
        cur,
        user_id,
        client_id,
        request_id,
        url_sklepu,
        "Spider Builder",
        "success",
        f"Zapisano w {plik_spidera}"
    )

    # Rejestracja wygenerowanego spidera w scraper_registry.
    # Dopiero admin approve wpisze go do clients.spiders_to_run.
    register_generated_spider(
        cur=cur,
        client_id=client_id,
        request_id=request_id,
        store_slug=store_slug,
        competitor_url=url_sklepu,
        competitor_name=nazwa_sklepu,
        spider_name=spider_name,
        spider_module=f"{store_slug}.{spider_name}",
        spider_path=plik_spidera
    )

    print(f"-> Sukces! Pająk zapisany bezpośrednio na produkcję: {plik_spidera}")
    print(f"-> Zarejestrowano scraper w scraper_registry: {spider_name}")

    return True


def procesuj_wniosek_w_tle(request_id: int, user_id: int, client_id: Optional[int], store_slug: str, urls: List[str]):
    print(f"\n[WORKER] Rozpoczynam wniosek #{request_id} dla klienta: {store_slug}")
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()

        mark_onboarding_status(cur, request_id, "scraper_generating")

        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            None,
            "AI Generator",
            "started",
            "Rozpoczęto generowanie scraperów"
        )

        success_count = 0
        error_count = 0

        for url in urls:
            try:
                ok = uruchom_autonomiczny_potok(
                    cur,
                    request_id,
                    user_id,
                    client_id,
                    store_slug,
                    url,
                    docelowy_limit_scrapowania=10
                )

                if ok:
                    success_count += 1
                else:
                    error_count += 1

            except Exception as e:
                print(f"[!] Błąd krytyczny dla {url}: {e}")
                log_scraper_step(
                    cur,
                    user_id,
                    client_id,
                    request_id,
                    url,
                    "Pipeline Error",
                    "error",
                    str(e)
                )
                error_count += 1

        final_status = "scraper_review" if success_count > 0 else "scraper_failed"
        mark_onboarding_status(cur, request_id, final_status)

        log_scraper_step(
            cur,
            user_id,
            client_id,
            request_id,
            None,
            "AI Generator",
            final_status,
            f"Zakończono. Sukcesy: {success_count}, błędy: {error_count}"
        )

        print(f"\n[WORKER] Koniec potoku. Pliki dla '{store_slug}' dostarczone.")

    except Exception as e:
        print(f"\n[WORKER] Błąd bazy danych: {e}")

        if conn:
            try:
                cur = conn.cursor()
                mark_onboarding_status(cur, request_id, "scraper_failed")
                log_scraper_step(
                    cur,
                    user_id,
                    client_id,
                    request_id,
                    None,
                    "AI Generator",
                    "failed",
                    str(e)
                )
            except Exception as inner_e:
                print(f"[WORKER] Nie udało się zapisać błędu: {inner_e}")


    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()


@app.post("/api/check")
def trigger_generation(payload: GenerationRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        procesuj_wniosek_w_tle,
        payload.request_id,
        payload.user_id,
        payload.client_id,
        payload.store_slug,
        payload.urls
    )

    return {
        "ok": True,
        "message": f"Wniosek #{payload.request_id} przyjęty. Proces AI wystartował w tle.",
        "request_id": payload.request_id,
        "client_id": payload.client_id,
        "store_slug": payload.store_slug
    }


@app.delete("/api/requests/{request_id}/spiders")
def usun_scrapery_wniosku(request_id: int):
    return {"ok": True, "removed": usun_pliki_wniosku(request_id)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)