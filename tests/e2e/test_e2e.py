import uuid
import requests
import psycopg2.extras
import pytest

try:
    from tests.e2e.conftest import WEBAPP_URL, FRONTEND_URL, DB_CONFIG
except ImportError:
    from conftest import WEBAPP_URL, FRONTEND_URL, DB_CONFIG


class TestUserJourneyE2E:

    def test_complete_user_lifecycle_journey(self, admin_session, db_conn):
        unique_id = uuid.uuid4().hex[:6]
        test_email = f"e2e_user_{unique_id}@example.com"
        test_password = "TajneHaslo123"
        test_store_name = f"Sklep E2E {unique_id}"
        test_domain = f"sklep-e2e-{unique_id}.pl"
        test_competitor = "https://konkurent-testowy.pl"

        request_id = None
        client_id = None
        store_prefix = None

        try:
            reg_payload = {
                "first_name": "Jan",
                "last_name": "Kowalski",
                "email": test_email,
                "password": test_password,
                "company_domain": test_domain,
                "store_name": test_store_name,
                "urls": [test_competitor],
            }
            resp_reg = requests.post(f"{WEBAPP_URL}/api/register", json=reg_payload, timeout=10)
            assert resp_reg.status_code == 200, f"Błąd rejestracji: {resp_reg.text}"
            reg_data = resp_reg.json()
            assert reg_data.get("ok") is True, f"Rejestracja nie powiodła się: {reg_data}"
            assert reg_data.get("status") == "onboarding_required"
            request_id = reg_data.get("request_id")
            assert request_id is not None, "Brak request_id w odpowiedzi rejestracji"

            pre_session = requests.Session()
            resp_pre_login = pre_session.post(
                f"{WEBAPP_URL}/api/login",
                json={"username": test_email, "password": test_password},
                timeout=10,
            )
            assert resp_pre_login.status_code == 200
            pre_login_data = resp_pre_login.json()
            assert pre_login_data.get("status") != "active", "Konto nie powinno być aktywne przed zatwierdzeniem!"

            resp_pending = admin_session.get(f"{WEBAPP_URL}/api/admin/pending_users", timeout=10)
            assert resp_pending.status_code == 200
            pending_data = resp_pending.json()
            pending_users = pending_data.get("users", [])
            assert any(u.get("username") == test_email or u.get("email") == test_email for u in pending_users), \
                f"Użytkownik {test_email} nie pojawił się na liście pending_users admina! (Otrzymano: {pending_users})"

            resp_approve = admin_session.post(
                f"{WEBAPP_URL}/api/admin/approve_user",
                json={"request_id": request_id},
                timeout=15,
            )
            assert resp_approve.status_code == 200, f"Błąd zatwierdzenia przez admina: {resp_approve.text}"
            approve_data = resp_approve.json()
            assert approve_data.get("ok") is True, f"Zatwierdzenie nie powiodło się: {approve_data}"
            assert approve_data.get("status") == "active"
            client_id = approve_data.get("client_id")
            store_prefix = approve_data.get("store_prefix")
            assert client_id is not None, "Brak client_id po zatwierdzeniu"
            assert store_prefix is not None, "Brak store_prefix po zatwierdzeniu"

            with db_conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s) AS tbl", (f"public.{store_prefix}_products",))
                assert cur.fetchone()[0] is not None, f"Tabela {store_prefix}_products nie powstała w bazie!"

                cur.execute("SELECT to_regclass(%s) AS tbl", (f"public.{store_prefix}_competitors",))
                assert cur.fetchone()[0] is not None, f"Tabela {store_prefix}_competitors nie powstała w bazie!"

                cur.execute(f"""
                    INSERT INTO {store_prefix}_products (
                        sku, name, price_normal, price_special, category, store, availability
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    f"SKU-E2E-{unique_id}",
                    "Testowa Kołdra Puchowa E2E",
                    299.99,
                    249.99,
                    "Kołdry",
                    store_prefix,
                    "Dostępny",
                ))
                inserted_product_id = cur.fetchone()[0]
                assert inserted_product_id is not None

            client_session = requests.Session()
            resp_login = client_session.post(
                f"{WEBAPP_URL}/api/login",
                json={"username": test_email, "password": test_password},
                timeout=10,
            )
            assert resp_login.status_code == 200, f"Błąd logowania nowego klienta: {resp_login.text}"
            login_data = resp_login.json()
            assert login_data.get("ok") is True
            assert login_data.get("status") == "active"
            assert login_data.get("store_prefix") == store_prefix

            resp_me = client_session.get(f"{WEBAPP_URL}/api/me", timeout=10)
            assert resp_me.status_code == 200
            me_data = resp_me.json()
            assert me_data.get("ok") is True
            assert me_data["user"]["email"] == test_email
            assert me_data["user"]["is_admin"] is False

            resp_products = client_session.get(f"{WEBAPP_URL}/api/products?page=1&per_page=10", timeout=10)
            assert resp_products.status_code == 200
            prod_data = resp_products.json()
            assert prod_data.get("ok") is True
            products = prod_data.get("data", [])
            assert len(products) >= 1, "Brak produktów w katalogu nowo utworzonego klienta!"
            assert any(p.get("sku") == f"SKU-E2E-{unique_id}" for p in products), \
                "Dodany produkt nie został odnaleziony w odpowiedzi API katalogu produktów!"

            resp_filters = client_session.get(f"{WEBAPP_URL}/api/filters", timeout=10)
            assert resp_filters.status_code == 200
            filters_data = resp_filters.json()
            assert filters_data.get("ok") is True
            assert "categories" in filters_data

            resp_stats = client_session.get(f"{WEBAPP_URL}/api/stats", timeout=10)
            assert resp_stats.status_code == 200
            stats_data = resp_stats.json()
            assert stats_data.get("ok") is True
            assert "summary" in stats_data

            resp_logout = client_session.post(f"{WEBAPP_URL}/api/logout", timeout=10)
            assert resp_logout.status_code == 200
            resp_after_logout = client_session.get(f"{WEBAPP_URL}/api/me", timeout=10)
            assert resp_after_logout.status_code == 401, "Po wylogowaniu sesja powinna być unieważniona (401)!"

        finally:
            with db_conn.cursor() as cur:
                if store_prefix:
                    cur.execute(f"DROP TABLE IF EXISTS {store_prefix}_products CASCADE")
                    cur.execute(f"DROP TABLE IF EXISTS {store_prefix}_competitors CASCADE")
                    cur.execute(f"DROP TABLE IF EXISTS {store_prefix}_matches CASCADE")
                    cur.execute(f"DROP TABLE IF EXISTS {store_prefix}_report CASCADE")

                if request_id:
                    cur.execute("DELETE FROM onboarding_requests WHERE id = %s", (request_id,))

                cur.execute("DELETE FROM users WHERE email = %s", (test_email,))

                if client_id:
                    cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
