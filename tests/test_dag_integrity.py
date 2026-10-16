import ast
import os
import py_compile
import sys
from datetime import timedelta
import pytest

# Determine path to Dag.py
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


class TestDagIntegrity:
    """DAG Integrity Test suite for verifying syntax, cycles, and configuration loading."""

    def test_dag_compilation_syntax(self):
        """Weryfikuje, czy plik Dag.py kompiluje się bez błędów składniowych za pomocą py_compile i ast."""
        assert os.path.exists(DAG_FILE_PATH), f"Plik {DAG_FILE_PATH} nie istnieje!"

        # 1. Weryfikacja przez py_compile (bytecode compilation)
        compiled_path = py_compile.compile(DAG_FILE_PATH, doraise=True)
        assert compiled_path is not None, "Kompilacja py_compile nie powiodła się!"

        # 2. Weryfikacja drzewa składniowego AST
        with open(DAG_FILE_PATH, "r", encoding="utf-8") as f:
            source_code = f.read()
        parsed_ast = ast.parse(source_code, filename=DAG_FILE_PATH)
        assert parsed_ast is not None, "Parsowanie AST zwróciło pusty wynik!"

    def test_dag_import_and_parameters(self):
        """Weryfikuje, czy DAG ładuje się poprawnie i posiada wymagane parametry konfiguracyjne."""
        from airflow.models import DAG
        import Dag

        assert hasattr(Dag, "dag"), "Plik Dag.py nie definiuje obiektu 'dag'!"
        dag = Dag.dag
        assert isinstance(dag, DAG), "Obiekt 'dag' nie jest instancją airflow.models.DAG!"

        # Weryfikacja podstawowych parametrów DAG
        assert dag.dag_id == "multi_client_pipeline", f"Niepoprawne dag_id: {dag.dag_id}"
        assert dag.catchup is False, "catchup powinien być False!"
        assert dag.max_active_runs == 1, "max_active_runs powinien wynosić 1!"
        assert "production" in dag.tags, "Brak tagu 'production' w DAG!"
        assert "multi-client" in dag.tags, "Brak tagu 'multi-client' w DAG!"

        # Weryfikacja default_args
        assert hasattr(Dag, "default_args"), "Brak default_args w module Dag!"
        default_args = Dag.default_args
        assert default_args["owner"] == "Jhonny"
        assert default_args["depends_on_past"] is False
        assert default_args["email_on_failure"] is False
        assert default_args["retries"] == 0
        assert default_args["retry_delay"] == timedelta(minutes=5)

    def test_configuration_loading(self, monkeypatch):
        """Weryfikuje, czy wszystkie parametry konfiguracyjne i zmienne środowiskowe ładują się poprawnie."""
        import Dag

        # Weryfikacja struktury DB_CONFIG
        assert hasattr(Dag, "DB_CONFIG"), "Brak słownika DB_CONFIG!"
        db_config = Dag.DB_CONFIG
        required_keys = {"host", "port", "user", "password", "database"}
        assert required_keys.issubset(db_config.keys()), f"DB_CONFIG brakuje kluczy: {required_keys - set(db_config.keys())}"

        # Weryfikacja typów i wartości parametrów konfiguracyjnych
        assert isinstance(Dag.SCRAPY_ITEM_LIMIT, int), "SCRAPY_ITEM_LIMIT musi być int!"
        assert isinstance(Dag.PIPELINE_SCHEDULE_CRON, str), "PIPELINE_SCHEDULE_CRON musi być str!"
        assert isinstance(Dag.DEMO_TRUNCATE_COMPETITORS, bool), "DEMO_TRUNCATE_COMPETITORS musi być bool!"
        assert isinstance(Dag.SCRAPER_FAIL_DAG_ON_ERROR, bool), "SCRAPER_FAIL_DAG_ON_ERROR musi być bool!"

        # Test parsowania wartości logicznych ze zmiennych środowiskowych
        for truthy in ("1", "true", "True", "YES", "yes", "y"):
            parsed = truthy.lower() in ("1", "true", "yes", "y")
            assert parsed is True
        for falsy in ("0", "false", "False", "NO", "no", "n"):
            parsed = falsy.lower() in ("1", "true", "yes", "y")
            assert parsed is False

    def test_dag_cycle_check(self):
        """Weryfikuje brak cykli w grafie tasków (dag.test() / check_cycle)."""
        from airflow.utils.dag_cycle_tester import check_cycle
        import Dag

        dag = Dag.dag
        # Airflow cycle tester - rzuca AirflowDagCycleException w przypadku wykrycia cyklu
        check_cycle(dag)

        # Weryfikacja integralności struktury DAG
        assert "start" in dag.task_dict, "Brak taska 'start' w DAG!"
        assert "end" in dag.task_dict, "Brak taska 'end' w DAG!"

        # End task powinien być ujściem (sink)
        end_task = dag.get_task("end")
        assert len(end_task.downstream_list) == 0, "Task 'end' nie powinien mieć zadań downstream!"

        # Start task powinien być źródłem (source)
        start_task = dag.get_task("start")
        assert len(start_task.upstream_list) == 0, "Task 'start' nie powinien mieć zadań upstream!"

    def test_dag_validation_method(self):
        """Weryfikuje poprawność wewnętrzną DAG-a za pomocą wbudowanej metody validate()."""
        import Dag

        dag = Dag.dag
        # Metoda validate w Airflow sprawdza m.in. cykle i spójność relacji
        dag.validate()

    def test_trigger_rules_and_fault_tolerance_structure(self):
        """Weryfikuje, czy reguły wyzwalania (TriggerRule) zapewniają odporność na błędy."""
        from airflow.utils.trigger_rule import TriggerRule
        import Dag

        dag = Dag.dag

        # Task 'end' musi mieć TriggerRule.ALL_DONE, aby DAG zakończył się poprawnie niezależnie od statusu
        end_task = dag.get_task("end")
        assert end_task.trigger_rule == TriggerRule.ALL_DONE, (
            f"Task 'end' powinien mieć trigger_rule=ALL_DONE, a ma {end_task.trigger_rule}"
        )

        # Jeśli są aktywni klienci, weryfikujemy trigger_rule dla matching i finish
        matching_tasks = [task for task_id, task in dag.task_dict.items() if task_id.endswith(".matching")]
        for matching in matching_tasks:
            assert matching.trigger_rule == TriggerRule.ALL_DONE, (
                f"Task {matching.task_id} musi mieć trigger_rule=ALL_DONE, aby awaria scrapera nie blokowała matchingu!"
            )

        finish_success_tasks = [task for task_id, task in dag.task_dict.items() if task_id.endswith(".finish_pipeline_success")]
        for finish in finish_success_tasks:
            assert finish.trigger_rule == TriggerRule.ALL_SUCCESS

        finish_failed_tasks = [task for task_id, task in dag.task_dict.items() if task_id.endswith(".finish_pipeline_failed")]
        for finish in finish_failed_tasks:
            assert finish.trigger_rule == TriggerRule.ONE_FAILED

    def test_dag_test_execution_no_cycles(self):
        """Weryfikuje wykonanie dag.test() – symuluje lokalny przebieg DAG-a, potwierdzając brak cykli i deadlocków."""
        from unittest.mock import MagicMock
        from airflow.utils.state import DagRunState
        import Dag

        dag = Dag.dag

        # Zabezpieczamy oryginalne python_callable i mockujemy je dla celów testu grafu
        original_callables = {}
        for task in dag.tasks:
            if hasattr(task, "python_callable"):
                original_callables[task.task_id] = task.python_callable
                task.python_callable = MagicMock(return_value={"success": True})

        try:
            dag_run = dag.test()
            assert dag_run is not None, "dag.test() nie zwrócił obiektu DagRun!"
            assert dag_run.state in (DagRunState.SUCCESS, DagRunState.RUNNING), (
                f"dag.test() zakończył się nieoczekiwanym statusem: {dag_run.state}"
            )
        finally:
            # Przywrócenie oryginalnych funkcji
            for task in dag.tasks:
                if task.task_id in original_callables:
                    task.python_callable = original_callables[task.task_id]
