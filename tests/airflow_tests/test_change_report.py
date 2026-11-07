"""Raport zmian po aktualizacji (processors/change_report.py) i jego wpięcie w matching (Dag.py)."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import psycopg2
import psycopg2.extras
import pytest

import Dag
from processors.change_report import compare_states, update_change_report


def pair(client_sku, competitor_sku="oferta-1", store="pod_pierzyna", client_price=100.0, competitor_price=120.0):
    return {
        "client_sku": client_sku, "client_name": f"produkt {client_sku}", "size": "155x200",
        "store": store, "competitor_sku": competitor_sku, "competitor_name": competitor_sku,
        "competitor_url": f"https://sklep.test/{competitor_sku}",
        "client_price": client_price, "competitor_price": competitor_price,
    }


def state(*pairs):
    return {(item["client_sku"], item["store"]): item for item in pairs}


IN_STORE = {("podpierzyna", "oferta-1"), ("podpierzyna", "oferta-2")}


class TestCompareStates:

    def test_no_change_gives_empty_report(self):
        assert compare_states(state(pair("A")), state(pair("A")), IN_STORE, set()) == []

    @pytest.mark.parametrize("after, changed_by", [
        (pair("A", competitor_price=110.0), "competitor"),
        (pair("A", client_price=90.0), "client"),
        (pair("A", client_price=90.0, competitor_price=110.0), "both"),
    ])
    def test_price_change_says_whose_price_changed(self, after, changed_by):
        items = compare_states(state(pair("A")), state(after), IN_STORE, set())

        assert len(items) == 1
        assert items[0]["type"] == "price"
        assert items[0]["changed_by"] == changed_by
        assert items[0]["old_difference"] == 20.0
        assert items[0]["new_difference"] == round(after["competitor_price"] - after["client_price"], 2)

    def test_rounding_noise_is_not_a_change(self):
        assert compare_states(state(pair("A")), state(pair("A", competitor_price=120.001)), IN_STORE, set()) == []

    def test_offer_gone_from_store_is_removed(self):
        items = compare_states(state(pair("A", "wycofana")), {}, IN_STORE, set())

        assert [(item["type"], item["competitor_sku"], item["old_competitor_price"]) for item in items] == [
            ("removed", "wycofana", 120.0)
        ]
        assert items[0]["new_competitor_price"] is None

    def test_pair_lost_by_matching_while_offer_is_in_store_is_skipped(self):
        """Zmiana dopasowania albo produkt usunięty z feedu klienta to nie zdarzenie u konkurencji."""
        assert compare_states(state(pair("A")), {}, IN_STORE, set()) == []

    def test_offer_that_appeared_in_store_is_new(self):
        items = compare_states({}, state(pair("A", "oferta-2")), IN_STORE, {("podpierzyna", "oferta-2")})

        assert [(item["type"], item["new_competitor_price"], item["new_difference"]) for item in items] == [
            ("new", 120.0, 20.0)
        ]

    def test_new_pair_to_old_offer_is_skipped(self):
        """Np. klient dodał produkt do feedu, a oferta była w sklepie już wcześniej."""
        assert compare_states({}, state(pair("A")), IN_STORE, set()) == []

    def test_offer_replaced_by_new_one(self):
        items = compare_states(
            state(pair("A", "wycofana")),
            state(pair("A", "oferta-2", competitor_price=99.0)),
            IN_STORE,
            {("podpierzyna", "oferta-2")},
        )

        assert [(item["type"], item["competitor_sku"]) for item in items] == [
            ("removed", "wycofana"), ("new", "oferta-2"),
        ]

    def test_store_name_spelling_does_not_matter(self):
        """Kolumny dopasowań mają 'pod_pierzyna', oferty 'podpierzyna'."""
        assert compare_states(state(pair("A")), {}, {("podpierzyna", "oferta-1")}, set()) == []


class TestSaveChangeReport:

    @pytest.fixture(autouse=True)
    def mocks(self, monkeypatch):
        self.error_log = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", self.error_log)
        monkeypatch.setattr(Dag, "failed_scrapers", lambda run_id: [])
        monkeypatch.setattr(Dag.psycopg2, "connect", lambda **kwargs: MagicMock())

    def test_report_failure_does_not_stop_matching(self, sample_client, monkeypatch):
        import processors.change_report as change_report
        monkeypatch.setattr(change_report, "update_change_report", MagicMock(side_effect=RuntimeError("brak tabeli")))

        assert Dag.save_change_report(sample_client, ["calavado"], 1) is None
        kwargs = self.error_log.call_args.kwargs
        assert kwargs["error_code"] == "change_report_failed"
        assert kwargs["error_type"] == "warning"

    def test_failed_scrapers_are_passed_to_report(self, sample_client, monkeypatch):
        import processors.change_report as change_report
        update = MagicMock(return_value={"baseline": False, "pairs": 1, "price": 0, "new": 0, "removed": 0})
        monkeypatch.setattr(change_report, "update_change_report", update)
        monkeypatch.setattr(Dag, "failed_scrapers", lambda run_id: ["calavado"])

        Dag.save_change_report(sample_client, ["calavado", "pod_pierzyna"], 7)

        assert update.call_args.kwargs["unchecked_stores"] == ["calavado"]
        assert update.call_args.kwargs["pipeline_run_id"] == 7


# Testy na bazie (tymczasowe tabele test_chg_*, usuwane po teście).

def database_available():
    try:
        psycopg2.connect(**Dag.DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not database_available(), reason="Brak połączenia z bazą aplikacji")
class TestChangeReportInDatabase:
    T0 = datetime(2026, 11, 8, 9, 30)

    @pytest.fixture
    def db(self):
        prefix = f"test_chg_{uuid.uuid4().hex[:8]}"
        conn = psycopg2.connect(**Dag.DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE {prefix}_products (id SERIAL, sku TEXT, price_normal FLOAT, price_special FLOAT);
                CREATE TABLE {prefix}_competitors (sku TEXT, store TEXT, first_seen TIMESTAMP);
                CREATE TABLE {prefix}_matches (
                    client_sku TEXT, client_name TEXT, client_price FLOAT, size TEXT,
                    pod_pierzyna_price FLOAT, pod_pierzyna_price_special FLOAT,
                    pod_pierzyna_url TEXT, pod_pierzyna_name TEXT, pod_pierzyna_sku TEXT
                );
            """)
        conn.commit()
        yield prefix, conn
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                f"DROP TABLE IF EXISTS {prefix}_products, {prefix}_competitors, {prefix}_matches, "
                f"{prefix}_change_reports, {prefix}_match_state"
            )
        conn.commit()
        conn.close()

    @staticmethod
    def execute(conn, statement, rows=()):
        with conn.cursor() as cur:
            if rows:
                psycopg2.extras.execute_values(cur, statement, rows)
            else:
                cur.execute(statement)
        conn.commit()

    def test_baseline_then_report_with_all_change_types(self, db):
        prefix, conn = db
        self.execute(conn, f"INSERT INTO {prefix}_products (sku, price_normal, price_special) VALUES %s", [
            ("A", 100.0, None), ("B", 200.0, None), ("C", 300.0, None), ("D", 400.0, None),
        ])
        self.execute(conn, f"INSERT INTO {prefix}_competitors (sku, store, first_seen) VALUES %s", [
            ("oA", "podpierzyna", self.T0), ("oB", "podpierzyna", self.T0), ("oC", "podpierzyna", self.T0),
        ])
        self.execute(conn, f"INSERT INTO {prefix}_matches VALUES %s", [
            ("A", "koc a", 100.0, "150x200", 120.0, None, "u/a", "koc a", "oA"),
            ("B", "koc b", 200.0, "150x200", 210.0, None, "u/b", "koc b", "oB"),
            ("C", "koc c", 300.0, "150x200", 310.0, None, "u/c", "koc c", "oC"),
        ])

        baseline = update_change_report(conn, prefix, ["calavado", "pod_pierzyna"], now=self.T0)
        assert baseline == {"baseline": True, "pairs": 3, "price": 0, "new": 0, "removed": 0}

        # oA tańsza, promocja klienta na B, oC zniknęła ze sklepu, oD nowa w sklepie.
        self.execute(conn, f"UPDATE {prefix}_matches SET pod_pierzyna_price = 110.0 WHERE client_sku = 'A'")
        self.execute(conn, f"UPDATE {prefix}_products SET price_special = 180.0 WHERE sku = 'B'")
        self.execute(conn, f"DELETE FROM {prefix}_competitors WHERE sku = 'oC'")
        self.execute(conn, f"DELETE FROM {prefix}_matches WHERE client_sku = 'C'")
        self.execute(conn, f"INSERT INTO {prefix}_competitors (sku, store, first_seen) VALUES %s", [
            ("oD", "podpierzyna", self.T0 + timedelta(hours=23)),
        ])
        self.execute(conn, f"INSERT INTO {prefix}_matches VALUES %s", [
            ("D", "koc d", 400.0, "150x200", 390.0, None, "u/d", "koc d", "oD"),
        ])

        summary = update_change_report(
            conn, prefix, ["calavado", "pod_pierzyna"], pipeline_run_id=5,
            unchecked_stores=["calavado"], now=self.T0 + timedelta(days=1),
        )
        assert summary == {"baseline": False, "pairs": 3, "price": 2, "new": 1, "removed": 1}

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {prefix}_change_reports ORDER BY id")
            reports = cur.fetchall()
        conn.commit()

        assert [report["baseline"] for report in reports] == [True, False]
        report = reports[1]
        assert report["pipeline_run_id"] == 5
        assert report["unchecked_stores"] == ["calavado"]
        changes = {(item["type"], item["client_sku"]): item for item in report["items"]}
        assert set(changes) == {("price", "A"), ("price", "B"), ("removed", "C"), ("new", "D")}
        assert changes[("price", "A")]["changed_by"] == "competitor"
        assert (changes[("price", "B")]["old_client_price"], changes[("price", "B")]["new_client_price"]) == (200.0, 180.0)
        assert changes[("price", "B")]["new_difference"] == 30.0
        assert changes[("removed", "C")]["old_competitor_price"] == 310.0
        assert changes[("new", "D")]["new_difference"] == -10.0

        # Bez zmian też powstaje raport.
        again = update_change_report(conn, prefix, ["pod_pierzyna"], now=self.T0 + timedelta(days=2))
        assert again == {"baseline": False, "pairs": 3, "price": 0, "new": 0, "removed": 0}
