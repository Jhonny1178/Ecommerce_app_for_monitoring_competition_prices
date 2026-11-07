import os
import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch
import pytest

DAG_FILE_PATH = os.environ.get("AIRFLOW_DAG_FILE")
if not DAG_FILE_PATH:
    if os.path.exists("/opt/airflow/dags/Dag.py"):
        DAG_FILE_PATH = "/opt/airflow/dags/Dag.py"
    else:
        DAG_FILE_PATH = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "airflow", "dags", "Dag.py")
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
        monkeypatch.setattr(Dag, "get_client_brands", lambda prefix: [])
        monkeypatch.setattr(Dag, "remove_stale_offers", lambda *args, **kwargs: 0)

    @staticmethod
    def _scrapy_stats_lines(items, finish_reason="closespider_itemcount", **extra):
        """Zrzut statystyk w formacie, który scrapy wypisuje na końcu działania."""
        stats = {"finish_reason": finish_reason, **extra}
        if items:
            stats["item_scraped_count"] = items
        lines = ["[scrapy.statscollectors] INFO: Dumping Scrapy stats:"]
        for index, (key, value) in enumerate(sorted(stats.items())):
            prefix = "{" if index == 0 else " "
            lines.append(f"{prefix}{key!r}: {value!r},")
        lines.append("[scrapy.core.engine] INFO: Spider closed")
        return lines

    def _create_mock_process(self, return_code=0, output_lines=None):
        """Pomocnik tworzący mock procesu subprocess.Popen."""
        if output_lines is None:
            output_lines = ["Scraping started..."] + self._scrapy_stats_lines(10)

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
                return self._create_mock_process(return_code=0, output_lines=self._scrapy_stats_lines(50))
            else:
                return self._create_mock_process(return_code=0, output_lines=self._scrapy_stats_lines(30))

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

        upstream_task_ids = {t.task_id for t in matching_task.upstream_list}
        for spider in spiders:
            if spider in Dag.PAUSED_SPIDERS:
                continue
            expected_scraper_task_id = f"client_{Dag.safe_task_id(client_prefix)}.scrape_{Dag.safe_task_id(spider)}"
            assert expected_scraper_task_id in upstream_task_ids, (
                f"Task scrapera {expected_scraper_task_id} powinien być bezpośrednim upstreamem matchingu!"
            )

    def test_scraper_without_items_is_reported_as_failure(self, sample_client, monkeypatch):
        """Scraper zakończony kodem 0, ale bez żadnego produktu, to błąd."""
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", False)

        mock_error_log = MagicMock()
        mock_update_task = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", mock_error_log)
        monkeypatch.setattr(Dag, "update_live_task_run", mock_update_task)

        output = self._scrapy_stats_lines(
            0,
            finish_reason="finished",
            **{"downloader/response_status_count/403": 2},
        )
        mock_proc = self._create_mock_process(return_code=0, output_lines=output)

        with patch("subprocess.Popen", return_value=mock_proc):
            result = Dag.run_spider_for_client(
                client=sample_client,
                spider_name="jmbdesing",
                pipeline_run_id=1,
            )

        assert result["success"] is False
        assert "nie pobrał żadnego produktu" in result["error"]
        assert "HTTP 403" in result["error"]
        assert mock_error_log.call_args[1]["error_code"] == "scraper_no_items"
        assert any(call.kwargs.get("status") == "failed" for call in mock_update_task.call_args_list)

    def test_scraper_without_items_fails_airflow_task(self, sample_client, monkeypatch):
        """W trybie domyślnym (SCRAPER_FAIL_DAG_ON_ERROR = True) brak produktów kończy zadanie Airflow błędem, więc jest widoczny na czerwono."""
        monkeypatch.setattr(Dag, "SCRAPER_FAIL_DAG_ON_ERROR", True)

        output = self._scrapy_stats_lines(
            0,
            finish_reason="closespider_timeout",
            **{"spider_exceptions/KeyError": 87, "spider_exceptions/count": 87},
        )
        mock_proc = self._create_mock_process(return_code=0, output_lines=output)

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError) as exc_info:
                Dag.run_spider_for_client(
                    client=sample_client,
                    spider_name="calavado",
                    pipeline_run_id=1,
                )

        assert "KeyError x87" in str(exc_info.value)
        assert "limicie czasu" in str(exc_info.value)

    def test_partial_errors_are_saved_as_warning(self, sample_client, monkeypatch):
        """Scraper, który pobrał produkty, ale część stron się nie udała, kończy się sukcesem z ostrzeżeniem zapisanym w error_logs."""
        mock_error_log = MagicMock()
        mock_clear_error = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", mock_error_log)
        monkeypatch.setattr(Dag, "clear_scraper_error", mock_clear_error)

        output = self._scrapy_stats_lines(
            48,
            **{"spider_exceptions/KeyError": 3, "spider_exceptions/count": 3},
        )
        mock_proc = self._create_mock_process(return_code=0, output_lines=output)

        with patch("subprocess.Popen", return_value=mock_proc):
            result = Dag.run_spider_for_client(
                client=sample_client,
                spider_name="pod_pierzyna",
                pipeline_run_id=1,
            )

        assert result["success"] is True
        assert result["scraped_items"] == 48
        assert "KeyError x3" in result["warning"]
        mock_clear_error.assert_called_once()
        assert mock_error_log.call_args[1]["error_type"] == "warning"
        assert mock_error_log.call_args[1]["error_code"] == "scraper_partial_errors"

    def test_clean_run_has_no_warning(self, sample_client, monkeypatch):
        mock_error_log = MagicMock()
        monkeypatch.setattr(Dag, "save_error_log", mock_error_log)

        with patch("subprocess.Popen", return_value=self._create_mock_process()):
            result = Dag.run_spider_for_client(
                client=sample_client,
                spider_name="calavado",
                pipeline_run_id=1,
            )

        assert result["success"] is True
        assert result["scraped_items"] == 10
        assert result["warning"] is None
        mock_error_log.assert_not_called()

    def test_airflow_interrupt_marks_task_failed_and_stops_scrapy(self, sample_client, monkeypatch):
        """Przerwanie przez Airflow (limit czasu, "Mark failed") to wyjątek spoza Exception."""
        from airflow.exceptions import AirflowTaskTimeout

        mock_update_task = MagicMock()
        mock_killpg = MagicMock()
        monkeypatch.setattr(Dag, "update_live_task_run", mock_update_task)
        monkeypatch.setattr(Dag.os, "killpg", mock_killpg)

        def interrupted_output():
            yield "[scrapy.core.engine] INFO: Spider opened"
            raise AirflowTaskTimeout("Timeout, PID: 1")

        mock_proc = MagicMock()
        mock_proc.stdout = interrupted_output()
        mock_proc.poll.return_value = None
        mock_proc.pid = 4321

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(AirflowTaskTimeout):
                Dag.run_spider_for_client(
                    client=sample_client,
                    spider_name="pod_pierzyna",
                    pipeline_run_id=1,
                )

        failed_calls = [call for call in mock_update_task.call_args_list if call.kwargs.get("status") == "failed"]
        assert failed_calls, "Przerwane zadanie musi dostać status failed"
        assert "AirflowTaskTimeout" in failed_calls[-1].kwargs["error_msg"]
        mock_killpg.assert_called_with(4321, Dag.signal.SIGTERM)


class TestPipelineRunStatus:
    """Status przebiegu klienta musi uwzględniać nieudane scrapery."""

    @staticmethod
    def _context_with_states(states):
        task_instances = []
        for task_id, state in states.items():
            ti = MagicMock()
            ti.task_id = task_id
            ti.state = state
            task_instances.append(ti)
        dag_run = MagicMock()
        dag_run.get_task_instances.return_value = task_instances
        return {"dag_run": dag_run}

    def test_finish_success_records_failure_of_scraper(self, sample_client, monkeypatch):
        from airflow.utils.state import TaskInstanceState

        mock_save = MagicMock()
        monkeypatch.setattr(Dag, "save_pipeline_run", mock_save)
        group = Dag.client_group_id_for(sample_client)
        context = self._context_with_states({
            f"{group}.ingest": TaskInstanceState.SUCCESS,
            f"{group}.scrape_jmbdesing": TaskInstanceState.FAILED,
            f"{group}.matching": TaskInstanceState.SUCCESS,
            "client_inny.scrape_x": TaskInstanceState.FAILED,
        })

        Dag.finish_pipeline_success(sample_client, pipeline_run_id=7, **context)

        args, kwargs = mock_save.call_args
        assert args[1] == "failed"
        assert "scrape_jmbdesing" in kwargs["error_msg"]
        assert "scrape_x" not in kwargs["error_msg"], "Błędy innego klienta nie mogą trafić do tego przebiegu"
        assert kwargs["run_id"] == 7

    def test_finish_success_records_success_when_all_tasks_succeeded(self, sample_client, monkeypatch):
        from airflow.utils.state import TaskInstanceState

        mock_save = MagicMock()
        monkeypatch.setattr(Dag, "save_pipeline_run", mock_save)
        group = Dag.client_group_id_for(sample_client)
        context = self._context_with_states({
            f"{group}.ingest": TaskInstanceState.SUCCESS,
            f"{group}.scrape_calavado": TaskInstanceState.SUCCESS,
            f"{group}.matching": TaskInstanceState.SUCCESS,
        })

        Dag.finish_pipeline_success(sample_client, pipeline_run_id=7, **context)

        mock_save.assert_called_once_with(sample_client["id"], "success", run_id=7)

    def test_new_run_closes_stale_running_entries(self, sample_client, monkeypatch):
        """Wpisy "running" z poprzedniego, przerwanego przebiegu są zamykane przy starcie nowego (DAG ma max_active_runs=1)."""
        cursor = MagicMock()
        cursor.rowcount = 3
        cursor.fetchone.return_value = [99]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        monkeypatch.setattr(Dag.psycopg2, "connect", lambda **kwargs: connection)

        run_id = Dag.create_pipeline_run(sample_client["id"])

        assert run_id == 99
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert "UPDATE pipeline_task_runs" in statements[0]
        assert "UPDATE pipeline_runs" in statements[1]
        assert "INSERT INTO pipeline_runs" in statements[2]
        for call in cursor.execute.call_args_list[:2]:
            assert call.args[1] == (Dag.STALE_RUN_MESSAGE, sample_client["id"])
        connection.commit.assert_called_once()

    def test_scraper_failures_fail_airflow_task_by_default(self):
        if "SCRAPER_FAIL_DAG_ON_ERROR" in os.environ:
            pytest.skip("Wartość ustawiona w środowisku")
        assert Dag.SCRAPER_FAIL_DAG_ON_ERROR is True


class TestPausedSpiders:
    """Wstrzymany scraper nie ma zadania w DAG-u, ale nic nie jest usuwane."""

    def test_paused_spider_has_no_task_and_is_logged(self, monkeypatch, capsys):
        dag = Dag.dag
        paused_in_dag = []
        for client in Dag.ACTIVE_CLIENTS:
            group = Dag.client_group_id_for(client)
            for spider in Dag.normalize_spiders(client.get("spiders_to_run")):
                task_id = f"{group}.scrape_{Dag.safe_task_id(spider)}"
                if spider.lower() in Dag.PAUSED_SPIDERS:
                    paused_in_dag.append(spider)
                    assert task_id not in dag.task_dict, f"Wstrzymany scraper {spider} nie powinien mieć zadania"

                    create_run = dag.get_task(f"{group}.create_pipeline_run")
                    assert spider in create_run.op_kwargs["paused_spiders"]

        if not paused_in_dag:
            pytest.skip("Żaden aktywny klient nie ma wstrzymanego scrapera")

    def test_create_pipeline_run_logs_paused_spiders(self, monkeypatch, capsys):
        cursor = MagicMock()
        cursor.rowcount = 0
        cursor.fetchone.return_value = [5]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        monkeypatch.setattr(Dag.psycopg2, "connect", lambda **kwargs: connection)

        Dag.create_pipeline_run(1, paused_spiders=["jmbdesing"])

        assert "Wstrzymane scrapery" in capsys.readouterr().out


class TestMatchingStoreNames:
    """Nazwa sklepu w danych ("podpierzyna") różni się od nazwy scrapera ("pod_pierzyna"); dopasowanie nie może przez to przepaść."""

    def test_store_from_competitor_table_maps_to_result_column(self):
        from processors.files_connector import MultiTenantReportGenerator

        generator = MultiTenantReportGenerator({
            "competitor_stores": ["calavado", "jmbdesing", "nasz_klient", "pod_pierzyna"],
        })

        assert generator.result_column_store("podpierzyna") == "pod_pierzyna"
        assert generator.result_column_store("calavado") == "calavado"
        assert generator.result_column_store("Calavado") == "calavado"
        assert generator.result_column_store("nieznany_sklep") is None


class TestIngestIndependence:
    """Awaria feedu klienta (np. 502) nie może blokować scraperów konkurencji."""

    def test_scrapers_do_not_wait_for_ingest_and_matching_waits_for_both(self):
        from airflow.utils.trigger_rule import TriggerRule

        dag = Dag.dag
        groups = [task_id[:-len(".ingest")] for task_id in dag.task_dict if task_id.endswith(".ingest")]
        if not groups:
            pytest.skip("Brak aktywnych klientów w DAG-u")

        for group in groups:
            ingest = dag.get_task(f"{group}.ingest")
            reset = dag.get_task(f"{group}.reset_competitors_demo")
            matching = dag.get_task(f"{group}.matching")

            assert f"{group}.ingest" not in reset.upstream_task_ids, "Scrapery nie mogą czekać na ingest"
            assert f"{group}.create_pipeline_run" in reset.upstream_task_ids
            assert f"{group}.ingest" in matching.upstream_task_ids, "Matching musi poczekać na ingest"
            assert matching.trigger_rule == TriggerRule.ALL_DONE

            assert ingest.retries == Dag.INGEST_RETRIES
            assert ingest.retry_delay == timedelta(minutes=Dag.INGEST_RETRY_DELAY_MINUTES)
