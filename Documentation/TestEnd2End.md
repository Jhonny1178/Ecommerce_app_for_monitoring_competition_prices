# Raport z Testu End-to-End

## 1. Wynik

| Metryka | Wartość |
| :--- | :--- |
| **Wynik** | **PASSED** |
| **Czas wykonania** | 1.48 s |

---

## 2. Przebieg scenariusza

| Krok | Działanie | Weryfikacja techniczna | Wynik |
| :---: | :--- | :--- | :---: |
| **1** | **Rejestracja nowego klienta** | `POST /api/register` z unikalnym e-mailem, domeną sklepu i adresem konkurencji. | **PASSED** (status: `onboarding_required`, zwrot `request_id`) |
| **2** | **Próba logowania przed zatwierdzeniem** | `POST /api/login` na świeżo założone konto. | **PASSED** (konto nieaktywne, brak dostępu do danych) |
| **3** | **Interwencja Administratora** | Logowanie admina, odczyt `GET /api/admin/pending_users`, akceptacja wniosku `POST /api/admin/approve_user`. | **PASSED** (nowy wniosek znaleziony i zatwierdzony) |
| **4** | **Automatyczny Provisioning bazy** | Wygenerowanie dedykowanych tabel PostgreSQL: `<prefix>_products`, `<prefix>_competitors`, `<prefix>_matches`. | **PASSED** (tabele poprawnie utworzone w schemacie `public`) |
| **5** | **Zasilenie danymi produktowymi** | Dodanie pozycji asortymentowej do dedykowanej tabeli klienta. | **PASSED** (rekord poprawnie powiązany ze sklepem klienta) |
| **6** | **Logowanie zatwierdzonego klienta** | `POST /api/login` po akceptacji admina. | **PASSED** (status `active`) |
| **7** | **Korzystanie z platformy analitycznej** | Klient odpytuje: `/api/me` (tożsamość), `/api/products` (widzi dodany produkt), `/api/filters` (filtry katalogu), `/api/stats` (statystyki). | **PASSED** (wszystkie endpointy zwracają poprawne dane JSON) |
| **8** | **Wylogowanie klienta** | `POST /api/logout` oraz ponowna próba odczytu `/api/me`. | **PASSED** (unieważnienie sesji, odpowiedź `401 Unauthorized`) |
| **9** | **Sprzątanie** | Usunięcie tabel tymczasowych `<prefix>_*`, usunięcie rekordu użytkownika i klienta. | **PASSED** |
