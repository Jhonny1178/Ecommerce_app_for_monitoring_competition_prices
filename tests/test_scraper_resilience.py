import os
import sys
from unittest.mock import MagicMock, patch
import pytest

DAG_FILE_PATH = os.environ.get("AIRFLOW_DAG_FILE")
if not DAG_FILE_PATH:
    if os.path.exists("/opt/airflow/dags/Dag.py"):
        DAG_FILE_PATH = "/opt/airflow/dags/Dag.py"
    else:
        DAG_FILE_PATH = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "airflow", "dags", "Dag.py")
        )

DAG_DIR = os.path.dirname(DAG_FILE_PATH)
if DAG_DIR not in sys.path:
    sys.path.insert(0, DAG_DIR)

import Dag


class TestScraperResilience:
    """Testy tolerancji na błędy scrapera oraz weryfikacji flagi SCRAPER_FAIL_DAG_ON_ERROR."""

    @pytest.fixture(autouse=True)
    def mock_db_helpers(self, monkeypatch):
        """Mockuje operacje bazodanowe, aby testy mogły działać niezależnie od bazy."""
        monkeypatch.setattr(Dag, "create_live_task_run", lambda **kwargs: 123)
        monkeypatch.setattr(Dag, "update_live_task_run", lambda **kwargs: None)
        monkeypatch.setattr(Dag, "save_task_run", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "save_error_log", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "mark_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "clear_scraper_error", lambda *args, **kwargs: None)
        monkeypatch.setattr(Dag, "count_rows", lambda table: 10)

    def _create_mock_process(self, return_code=0, output_lines=None):
        """Pomocnik tworzący mock procesu subprocess.Popen."""
        if output_lines is None:
            output_lines = ["Scraping started...", "Scraped 10 items", "Finished"]

        mock_proc = MagicMock()
        mock_proc.stdout = iter(output_lines)
        mock_proc.wait.return_value = return_code
        return mock_proc

    def test_scraper_failure_with_fail_dag_false(self, sample_client, monkeypatch):
        """Gdy SCRAPER_FAIL_DAG_ON_ERROR = False:
        Awaria pojedynczego pająka (exit_code != 0) NIE rzuca wyjątku,
        rejestruje błąd i zwraca status success=False, nie blokując potoku.
        """
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", False)

        mock_error_log = MagicMock()
        mock_mark_error = MagicMock()
        mock_update_task = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", mock_error_log)
        monkeypatch.setattr(Dag, "mark_scraper_error", mock_mark_error)
        monkeypatch.setattr(Dag, "update_live_task_run", mock_update_task)

        mock_proc = self._create_mock_process(return_code=1, output_lines=["Error: 403 Forbidden"])

        with patch("subprocess.Popen", return_value=mock_proc):
            result = Dag.run_spider_for_client(
                client=sample_client,
                spider_name="calavado",
                pipeline_run_id=1,
            )

        # Weryfikacja: funkcja zwróciła słownik z błędem bez rzucania wyjątku
        assert isinstance(result, dict)
        assert result["success"] is False
        assert result["spider"] == "calavado"
        assert "failed with code 1" in result["error"]

        # Weryfikacja: błąd został zalogowany w bazie
        mock_error_log.assert_called_once()
        assert mock_error_log.call_args[1]["error_code"] == "scraper_failed"

        mock_mark_error.assert_called_once_with(
            sample_client["id"],
            "calavado",
            result["error"],
        )

        # Weryfikacja: task run oznaczony jako failed
        mock_update_task.assert_called()
        assert any(call.kwargs.get("status") == "failed" for call in mock_update_task.call_args_list)

    def test_scraper_exception_with_fail_dag_false(self, sample_client, monkeypatch):
        """Gdy SCRAPER_FAIL_DAG_ON_ERROR = False:
        Niekontrolowany wyjątek w scraperze (np. brak pamięci/błąd sieci)
        jest przechwytywany i NIE zatrzymuje wykonania potoku.
        """
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", False)

        mock_error_log = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", mock_error_log)

        with patch("subprocess.Popen", side_effect=OSError("Network interface down")):
            result = Dag.run_spider_for_client(
                client=sample_client,
                spider_name="jmbdesing",
                pipeline_run_id=1,
            )

        assert isinstance(result, dict)
        assert result["success"] is False
        assert result["spider"] == "jmbdesing"
        assert "Network interface down" in result["error"]
        assert mock_error_log.call_args[1]["error_code"] == "scraper_exception"

    def test_scraper_failure_with_fail_dag_true(self, sample_client, monkeypatch):
        """Gdy SCRAPER_FAIL_DAG_ON_ERROR = True:
        Awaria pająka (exit_code != 0) RZUCA wyjątek RuntimeError,
        zatrzymując dany task (działanie w trybie strict/produkcyjnym).
        """
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", True)

        mock_proc = self._create_mock_process(return_code=2, output_lines=["Spider crashed!"])

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError) as exc_info:
                Dag.run_spider_for_client(
                    client=sample_client,
                    spider_name="pod_pierzyna",
                    pipeline_run_id=1,
                )

            assert "Spider pod_pierzyna failed with code 2" in str(exc_info.value)

    def test_scraper_exception_with_fail_dag_true(self, sample_client, monkeypatch):
        """Gdy SCRAPER_FAIL_DAG_ON_ERROR = True:
        Wyjątek w scraperze jest re-rzucany w górę do Airflow.
        """
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", True)

        with patch("subprocess.Popen", side_effect=RuntimeError("Scrapy fatal crash")):
            with pytest.raises(RuntimeError) as exc_info:
                Dag.run_spider_for_client(
                    client=sample_client,
                    spider_name="calavado",
                    pipeline_run_id=1,
                )

            assert "Scrapy fatal crash" in str(exc_info.value)

    def test_multi_spider_isolation_and_matching_continuity(self, sample_client, monkeypatch):
        """Symulacja scenariusza produkcyjnego z wieloma pająkami:
        Awaria Spider A (np. calavado) NIE blokuje wykonania Spider B (np. jmbdesing).
        Oba kończą etap scrapowania, a task matching otrzymuje dane zebrane przez działające źródła.
        """
        from airflow.utils.trigger_rule import TriggerRule

        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", False)

        spiders = ["calavado", "jmbdesing", "pod_pierzyna"]
        results = {}

        # calavado padnie (return_code = 1), pozostałe dwa zakończą się sukcesem
        def mock_popen(command, **kwargs):
            if "calavado" in command:
                return self._create_mock_process(return_code=1, output_lines=["Calavado site down"])
            elif "jmbdesing" in command:
                return self._create_mock_process(return_code=0, output_lines=["Scraped 50 items"])
            else:
                return self._create_mock_process(return_code=0, output_lines=["Scraped 30 items"])

        with patch("subprocess.Popen", side_effect=mock_popen):
            for spider in spiders:
                results[spider] = Dag.run_spider_for_client(
                    client=sample_client,
                    spider_name=spider,
                    pipeline_run_id=1,
                )

        # Weryfikacja: Spider A zwrócił błąd, ale nie przerwał pętli
        assert results["calavado"]["success"] is False
        # Weryfikacja: Spider B i C zakończyły się sukcesem
        assert results["jmbdesing"]["success"] is True
        assert results["pod_pierzyna"]["success"] is True

        # Weryfikacja w grafie Airflow: matching posiada TriggerRule.ALL_DONE,
        # co gwarantuje jego odpalenie nawet w przypadku błędu któregoś z pająków.
        dag = Dag.dag
        client_prefix = sample_client["store_prefix"]
        matching_task_id = f"client_{Dag.safe_task_id(client_prefix)}.matching"
        matching_task = dag.get_task(matching_task_id)

        assert matching_task.trigger_rule == TriggerRule.ALL_DONE, (
            "Task matching musi mieć TriggerRule.ALL_DONE, aby uruchomić się "
            "dla pozostałych źródeł po awarii pojedynczego pająka!"
        )

        # Weryfikacja: wszystkie pająki są upstreamem dla matchingu
        upstream_task_ids = {t.task_id for t in matching_task.upstream_list}
        for spider in spiders:
            expected_scraper_task_id = f"client_{Dag.safe_task_id(client_prefix)}.scrape_{Dag.safe_task_id(spider)}"
            assert expected_scraper_task_id in upstream_task_ids, (
                f"Task scrapera {expected_scraper_task_id} powinien być bezpośrednim upstreamem matchingu!"
            )
