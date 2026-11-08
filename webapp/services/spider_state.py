# Ustala, czy pipeline faktycznie uruchamia scraper klienta (te same reguły co DAG w airflow/dags/Dag.py).
import os

ACTIVE = "active"
PAUSED = "paused"
OWN_STORE = "own_store"
CLIENT_INACTIVE = "client_inactive"


def paused_spiders() -> set[str]:
    # Domyślna wartość jak w Dag.py; docker-compose ustawia ją wspólnie dla Airflow i webapp.
    raw = os.environ.get("PAUSED_SPIDERS", "jmbdesing")
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def spider_run_state(spider_name, client_prefix, client_is_active=True) -> str:
    name = str(spider_name or "").strip()

    if not client_is_active:
        return CLIENT_INACTIVE
    # DAG pomija scraper o nazwie prefiksu klienta: produkty klienta przychodzą z pliku feed (ingest).
    if client_prefix and name == client_prefix:
        return OWN_STORE
    if name.lower() in paused_spiders():
        return PAUSED
    return ACTIVE
