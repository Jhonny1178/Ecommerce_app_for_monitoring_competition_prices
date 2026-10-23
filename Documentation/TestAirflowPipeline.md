# Raport z testów: Integralność i Odporność Potoku Airflow

## 1. Informacje ogólne i wynik

| Metryka | Wartość |
| :--- | :--- |
| **Data wykonania** | 16.10.2026 |
| **Środowisko** | Apache Airflow 2.9.1 (Docker), Python 3.12, Pytest 9.1 |
| **Testowany moduł** | `airflow/dags/Dag.py` |
| **Status ogólny** | **12/12 PASSED (100% sukcesu)** |
| **Czas wykonania** | ~1.01 s |

---

## 2. Zakres i cel testów

Testy miały na celu weryfikację dwóch krytycznych obszarów potoku ETL w Apache Airflow:

1. **Integralność DAG-a (`tests/airflow_tests/test_dag_integrity.py`)**: Sprawdzenie poprawności składniowej, ładowania konfiguracji ze zmiennych środowiskowych, walidacji relacji w grafie oraz wykluczenia cykli i zakleszczeń (*deadlocków*) za pomocą `dag.test()`.
2. **Odporność i tolerancja na błędy (`tests/airflow_tests/test_scraper_resilience.py`)**: Weryfikacja działania flagi `SCRAPER_FAIL_DAG_ON_ERROR` oraz sprawdzenie, czy awaria pojedynczego pająka (np. kod błędu HTTP, błąd sieci, crash Scrapy) nie blokuje dalszych etapów potoku (dopasowywania ofert i raportowania dla pozostałych źródeł).

---

## 3. Wyniki szczegółowe

| Moduł testowy | Nazwa testu | Sprawdzany mechanizm | Wynik |
| :--- | :--- | :--- | :---: |
| **Integralność DAG-a** | `test_dag_compilation_syntax` | Kompilacja bytecode (`py_compile`) oraz drzewo składniowe AST | **PASSED** |
| | `test_dag_import_and_parameters` | Poprawność instancji DAG, `dag_id`, `default_args`, tagi | **PASSED** |
| | `test_configuration_loading` | Ładowanie `DB_CONFIG`, limitów `SCRAPY_ITEM_LIMIT` i crona | **PASSED** |
| | `test_dag_cycle_check` | Weryfikacja braku cykli w grafie (`check_cycle`) | **PASSED** |
| | `test_dag_validation_method` | Wewnętrzna walidacja Airflow (`dag.validate()`) | **PASSED** |
| | `test_trigger_rules_structure` | Reguły wyzwalania (`ALL_DONE` dla tasków `matching` i `end`) | **PASSED** |
| | `test_dag_test_execution_no_cycles` | Symulacja wykonania lokalnego całego grafu (`dag.test()`) | **PASSED** |
| **Odporność Scrapera** | `test_scraper_failure_with_fail_dag_false` | Kod błędu scrapera przy `FAIL_ON_ERROR=False` (brak przerwania) | **PASSED** |
| | `test_scraper_exception_with_fail_dag_false` | Nieoczekiwany wyjątek w pająku (logowanie i brak crasha DAG) | **PASSED** |
| | `test_scraper_failure_with_fail_dag_true` | Kod błędu przy `FAIL_ON_ERROR=True` (podniesienie `RuntimeError`) | **PASSED** |
| | `test_scraper_exception_with_fail_dag_true` | Re-rzucenie wyjątku w trybie produkcyjnym fail-fast | **PASSED** |
| | `test_multi_spider_isolation_and_matching` | Awaria 1 z 3 źródeł: poprawne przejście do matchingu pozostałych | **PASSED** |

---

## 4. Kluczowe wnioski

1. **Poprawność architektoniczna grafu**:
   DAG `multi_client_pipeline` kompiluje się czysto, nie posiada zapętleń cyklicznych, a przejście od zadania początkowego (`start`) przez grupy klientów aż do zakończenia (`end`) wykonuje się stabilnie.
2. **Skuteczna tolerancja na awarie źródeł zewnętrznych**:
   - W trybie demonstracyjnym/odpornym (`SCRAPER_FAIL_DAG_ON_ERROR = False`) awaria pojedynczego sklepu konkurencji jest rejestrowana w tabelach `error_logs` i `scraper_registry`, lecz sam proces nie rzuca niekontrolowanego wyjątku.
   - Task `matching` posiada regułę `TriggerRule.ALL_DONE` zamiast standardowej `ALL_SUCCESS`. Oznacza to, że dopasowywanie cen uruchamia się dla danych pobranych przez sprawne scrapery, a cały proces biznesowy nie zostaje zablokowany przez awarię pojedynczego sklepu.
3. **Zgodność z konfiguracją**:
   Wszystkie flagi i zmienne z pliku `.env` są prawidłowo mapowane na typy Python (`int`, `bool`, `dict`).
