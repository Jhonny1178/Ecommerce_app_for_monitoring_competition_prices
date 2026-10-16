import os
import sys
import pytest

# Ensure Airflow DAGs and processors can be imported
DAGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "airflow", "dags"))
if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)

CONTAINER_DAGS_DIR = "/opt/airflow/dags"
if os.path.exists(CONTAINER_DAGS_DIR) and CONTAINER_DAGS_DIR not in sys.path:
    sys.path.insert(0, CONTAINER_DAGS_DIR)


@pytest.fixture
def sample_client():
    """Provides a realistic single-client dictionary matching schema.sql."""
    return {
        "id": 1,
        "name": "Nasz Klient",
        "slug": "nasz_klient",
        "store_prefix": "nasz_klient",
        "is_active": True,
        "source_type": "file",
        "source_path": "/shared/onboarding/nasz_klient.csv",
        "source_url": "https://lambfield.com/",
        "file_format": "csv",
        "field_mapping": {
            "title": "name",
            "price": "price",
            "url": "url",
            "sku": "sku",
        },
        "spiders_to_run": ["calavado", "jmbdesing", "pod_pierzyna"],
        "match_name_threshold": 90.0,
        "match_color_threshold": 80.0,
        "match_maker_threshold": 80.0,
    }


@pytest.fixture
def sample_clients_list(sample_client):
    """Provides a list of active clients for multi-client testing."""
    client_2 = {
        "id": 2,
        "name": "Klient Testowy 2",
        "slug": "klient_testowy_2",
        "store_prefix": "klient2",
        "is_active": True,
        "source_type": "file",
        "source_path": "/shared/onboarding/klient2.csv",
        "source_url": "https://example.com/",
        "file_format": "csv",
        "field_mapping": {},
        "spiders_to_run": ["calavado"],
        "match_name_threshold": 85.0,
        "match_color_threshold": 75.0,
        "match_maker_threshold": 75.0,
    }
    return [sample_client, client_2]
