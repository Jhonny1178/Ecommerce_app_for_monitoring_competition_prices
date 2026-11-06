import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
import time
import psycopg2
import psycopg2.extras
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.utils.task_group import TaskGroup
from airflow.utils.trigger_rule import TriggerRule
import contextlib
import functools
import io
from psycopg2 import sql

DB_CONFIG = {
    "host": os.environ.get("APP_DB_HOST"),
    "port": os.environ.get("APP_DB_PORT", "5432"),
    "user": os.environ.get("APP_DB_USER"),
    "password": os.environ.get("APP_DB_PASSWORD"),
    "database": os.environ.get("APP_DB_NAME"),
}


# ============================================================
# DEMO / TEST SETTINGS
# ============================================================
# Każdy scraper zakończy się po 200 zescrapowanych itemach.
# Później produkcyjnie możesz dać np. 0 albo zmienić env.
SCRAPY_ITEM_LIMIT = int(os.environ.get("SCRAPY_ITEM_LIMIT", "100"))

PIPELINE_SCHEDULE_CRON = os.environ.get(
    "PIPELINE_SCHEDULE_CRON",
    "0 9 * * *",
)
# Na pokaz lepiej wyczyścić tabelę competitors przed scrapowaniem,
# żeby matching nie mielił setek tysięcy starych rekordów.
# Produkcyjnie ustaw to na false.
DEMO_TRUNCATE_COMPETITORS = os.environ.get(
    "DEMO_TRUNCATE_COMPETITORS",
    "true"
).lower() in ("1", "true", "yes", "y")

# Na pokaz nie wywalamy całego DAG-a, jeśli jeden scraper padnie.
# Błąd zapisze się w pipeline_task_runs, ale matching dalej się odpali.
# Produkcyjnie możesz ustawić true.
SCRAPER_FAIL_DAG_ON_ERROR = os.environ.get(
    "SCRAPER_FAIL_DAG_ON_ERROR",
    "false"
).lower() in ("1", "true", "yes", "y")


default_args = {
    "owner": "Jhonny",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=5),
}


def safe_task_id(value):
    value = str(value or "unknown")
    value = value.replace("-", "_").replace(" ", "_").replace(".", "_")
    value = "".join(ch for ch in value if ch.isalnum() or ch == "_")
    return value.lower()


def normalize_spiders(spiders_to_run):
    if not spiders_to_run:
        return []

    if isinstance(spiders_to_run, list):
        return [str(spider).strip() for spider in spiders_to_run if str(spider).strip()]

    if isinstance(spiders_to_run, tuple):
        return [str(spider).strip() for spider in spiders_to_run if str(spider).strip()]

    if isinstance(spiders_to_run, str):
        value = spiders_to_run.strip()

        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]

        return [
            spider.strip().strip('"').strip("'")
            for spider in value.split(",")
            if spider.strip()
        ]

    return []


def normalize_field_mapping(field_mapping):
    if not field_mapping:
        return {}

    if isinstance(field_mapping, dict):
        return field_mapping

    if isinstance(field_mapping, str):
        try:
            return json.loads(field_mapping)
        except Exception:
            return {}

    return {}


def get_active_clients():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            id,
            name,
            slug,
            store_prefix,
            is_active,
            source_type,
            source_path,
            source_url,
            file_format,
            field_mapping,
            spiders_to_run,
            match_name_threshold,
            match_color_threshold,
            match_maker_threshold
        FROM clients
        WHERE is_active = TRUE
        ORDER BY id
    """)

    clients = [dict(row) for row in cur.fetchall()]

    cur.close()
    conn.close()

    return clients


def create_pipeline_run(client_id):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO pipeline_runs (client_id, status, started_at)
            VALUES (%s, 'running', NOW())
            RETURNING id
        """, (client_id,))

        run_id = cur.fetchone()[0]

        conn.commit()
        cur.close()
        conn.close()

        print(f"[PIPELINE_RUN] Created run_id={run_id} client_id={client_id}", flush=True)
        return run_id

    except Exception as e:
        print(f"[PIPELINE_RUN_LOG] Could not create pipeline run: {e}", flush=True)
        return None


def save_pipeline_run(client_id, status, error_msg=None, run_id=None):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        if run_id:
            cur.execute("""
                UPDATE pipeline_runs
                SET status = %s,
                    finished_at = NOW(),
                    error_msg = %s
                WHERE id = %s
            """, (status, error_msg, run_id))
        else:
            cur.execute("""
                INSERT INTO pipeline_runs (client_id, status, finished_at, error_msg)
                VALUES (%s, %s, NOW(), %s)
            """, (client_id, status, error_msg))

        conn.commit()
        cur.close()
        conn.close()

        print(
            f"[PIPELINE_RUN] Saved client_id={client_id} run_id={run_id} status={status}",
            flush=True,
        )

    except Exception as e:
        print(f"[PIPELINE_RUN_LOG] Could not save pipeline run: {e}", flush=True)


def save_task_run(
    client_id,
    pipeline_run_id,
    task_id,
    status,
    error_msg=None,
    log_excerpt=None,
    started_at=None,
    finished_at=None,
):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            SELECT
                id,
                COALESCE(log_excerpt, '')
            FROM pipeline_task_runs
            WHERE pipeline_run_id IS NOT DISTINCT FROM %s
              AND task_id = %s
            ORDER BY id DESC
            LIMIT 1
        """, (
            pipeline_run_id,
            task_id,
        ))

        existing_task = cur.fetchone()

        if existing_task:
            existing_id = existing_task[0]
            existing_log = existing_task[1] or ""
            summary = (log_excerpt or "").strip()

            combined_log = existing_log.rstrip()

            if summary and summary not in combined_log:
                if combined_log:
                    combined_log += "\n"

                combined_log += summary

            cur.execute("""
                UPDATE pipeline_task_runs
                SET
                    status = %s,
                    started_at = COALESCE(%s, started_at),
                    finished_at = %s,
                    log_excerpt = %s,
                    error_msg = %s
                WHERE id = %s
            """, (
                status,
                started_at,
                finished_at,
                combined_log,
                error_msg,
                existing_id,
            ))

        else:
            cur.execute("""
                INSERT INTO pipeline_task_runs
                (
                    client_id,
                    pipeline_run_id,
                    task_id,
                    status,
                    started_at,
                    finished_at,
                    log_excerpt,
                    error_msg
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                client_id,
                pipeline_run_id,
                task_id,
                status,
                started_at,
                finished_at,
                log_excerpt,
                error_msg,
            ))

        conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()

        print(
            f"[PIPELINE_TASK_LOG] "
            f"Could not save task run: {e}",
            flush=True,
        )

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()

def create_live_task_run(
    client_id,
    pipeline_run_id,
    task_id,
    started_at,
):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO pipeline_task_runs
            (
                client_id,
                pipeline_run_id,
                task_id,
                status,
                started_at,
                log_excerpt
            )
            VALUES (%s, %s, %s, 'running', %s, '')
            RETURNING id
        """, (
            client_id,
            pipeline_run_id,
            task_id,
            started_at,
        ))

        task_run_id = cur.fetchone()[0]

        conn.commit()
        return task_run_id

    except Exception as e:
        print(
            f"[PIPELINE_TASK_LOG] "
            f"Could not create live task: {e}",
            flush=True,
        )
        return None

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()


def update_live_task_run(
    task_run_id,
    status,
    log_excerpt,
    error_msg=None,
    finished_at=None,
):
    if not task_run_id:
        return

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            UPDATE pipeline_task_runs
            SET
                status = %s,
                log_excerpt = %s,
                error_msg = %s,
                finished_at = %s
            WHERE id = %s
        """, (
            status,
            log_excerpt,
            error_msg,
            finished_at,
            task_run_id,
        ))

        conn.commit()

    except Exception as e:
        print(
            f"[PIPELINE_TASK_LOG] "
            f"Could not update live task: {e}",
            flush=True,
        )

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()
class LiveTaskLogCapture(io.TextIOBase):
    def __init__(
        self,
        task_run_id,
        original_stream,
        update_interval=1.0,
        max_characters=60000,
    ):
        self.task_run_id = task_run_id
        self.original_stream = original_stream
        self.update_interval = update_interval
        self.max_characters = max_characters

        self._parts = []
        self._last_update = 0.0
        self._is_synchronizing = False

    def write(self, text):
        if not text:
            return 0

        self.original_stream.write(text)
        self._parts.append(text)

        if not self._is_synchronizing:
            self.synchronize()

        return len(text)

    def flush(self):
        self.original_stream.flush()

        if not self._is_synchronizing:
            self.synchronize()

    def get_log_text(self):
        value = "".join(self._parts)

        if len(value) > self.max_characters:
            value = value[-self.max_characters:]
            self._parts = [value]

        return value.strip()

    def synchronize(
        self,
        force=False,
        status="running",
        error_msg=None,
        finished_at=None,
    ):
        if not self.task_run_id:
            return

        current_time = time.monotonic()

        if (
            not force
            and current_time - self._last_update
            < self.update_interval
        ):
            return

        self._is_synchronizing = True

        try:
            update_live_task_run(
                task_run_id=self.task_run_id,
                status=status,
                log_excerpt=self.get_log_text(),
                error_msg=error_msg,
                finished_at=finished_at,
            )

            self._last_update = current_time

        finally:
            self._is_synchronizing = False


def finish_live_task_if_running(
    task_run_id,
    status,
    log_excerpt,
    error_msg=None,
):
    if not task_run_id:
        return

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            UPDATE pipeline_task_runs
            SET
                status = %s,
                log_excerpt = %s,
                error_msg = %s,
                finished_at = NOW()
            WHERE id = %s
              AND status = 'running'
        """, (
            status,
            log_excerpt,
            error_msg,
            task_run_id,
        ))

        conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()

        print(
            f"[PIPELINE_TASK_LOG] "
            f"Could not finish live task: {e}",
            flush=True,
        )

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()


def capture_task_prints(task_name_factory):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(
            client,
            pipeline_run_id=None,
            *args,
            **kwargs,
        ):
            client_id = client["id"]
            task_name = task_name_factory(client)
            started_at = datetime.now()

            task_run_id = create_live_task_run(
                client_id=client_id,
                pipeline_run_id=pipeline_run_id,
                task_id=task_name,
                started_at=started_at,
            )

            capture = LiveTaskLogCapture(
                task_run_id=task_run_id,
                original_stream=sys.stdout,
            )

            try:
                with contextlib.redirect_stdout(capture):
                    with contextlib.redirect_stderr(capture):
                        result = function(
                            client,
                            pipeline_run_id,
                            *args,
                            **kwargs,
                        )

                capture.synchronize(force=True)

                finish_live_task_if_running(
                    task_run_id=task_run_id,
                    status="success",
                    log_excerpt=capture.get_log_text(),
                )

                return result

            except Exception as e:
                capture.write(
                    f"\n[ERROR] {type(e).__name__}: {e}\n"
                )

                capture.synchronize(
                    force=True,
                    status="failed",
                    error_msg=str(e),
                    finished_at=datetime.now(),
                )

                raise

        return wrapper

    return decorator
def save_error_log(client_id, category, message, error_type="runtime", error_code=None):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO error_logs
            (
                client_id,
                category,
                error_code,
                message,
                error_type,
                is_reviewed,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, FALSE, NOW())
        """, (
            client_id,
            category,
            error_code,
            message,
            error_type,
        ))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"[ERROR_LOG] Could not save error log: {e}", flush=True)


def mark_scraper_error(client_id, spider_name, error_msg):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            UPDATE scraper_registry
            SET last_error = %s
            WHERE client_id = %s
              AND spider_name = %s
        """, (
            error_msg,
            client_id,
            spider_name,
        ))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"[SCRAPER_REGISTRY] Could not update scraper error: {e}", flush=True)


def clear_scraper_error(client_id, spider_name):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            UPDATE scraper_registry
            SET last_error = NULL
            WHERE client_id = %s
              AND spider_name = %s
        """, (
            client_id,
            spider_name,
        ))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"[SCRAPER_REGISTRY] Could not clear scraper error: {e}", flush=True)


def count_rows(table_name):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        result = cur.fetchone()[0]

        cur.close()
        conn.close()

        return result

    except Exception as e:
        print(f"[COUNT] Could not count table={table_name}: {e}", flush=True)
        return None

def remove_incomplete_match_rows(table_name):
    conn = None
    cur = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
        """, (table_name,))

        columns = {
            row[0]
            for row in cur.fetchall()
        }

        excluded_prefixes = {
            "client",
            "product",
            "our",
        }

        competitor_prefixes = sorted({
            column_name[:-4]
            for column_name in columns
            if column_name.endswith("_url")
            and column_name[:-4]
            not in excluded_prefixes
        })

        valid_match_conditions = []

        for competitor_prefix in competitor_prefixes:
            url_column = (
                f"{competitor_prefix}_url"
            )

            name_column = (
                f"{competitor_prefix}_name"
            )

            price_column = (
                f"{competitor_prefix}_price"
            )

            if (
                url_column not in columns
                or name_column not in columns
                or price_column not in columns
            ):
                continue

            valid_match_conditions.append(
                sql.SQL("""
                    (
                        NULLIF(
                            BTRIM(
                                CAST({url_column} AS TEXT)
                            ),
                            ''
                        ) IS NOT NULL
                        AND NULLIF(
                            BTRIM(
                                CAST({name_column} AS TEXT)
                            ),
                            ''
                        ) IS NOT NULL
                        AND NULLIF(
                            BTRIM(
                                CAST({price_column} AS TEXT)
                            ),
                            ''
                        ) IS NOT NULL
                    )
                """).format(
                    url_column=sql.Identifier(
                        url_column
                    ),
                    name_column=sql.Identifier(
                        name_column
                    ),
                    price_column=sql.Identifier(
                        price_column
                    ),
                )
            )

        if not valid_match_conditions:
            print(
                f"[MATCHING] Nie znaleziono kolumn "
                f"konkurencji w tabeli {table_name}.",
                flush=True,
            )

            return 0, count_rows(table_name) or 0

        valid_match_expression = sql.SQL(
            " OR "
        ).join(valid_match_conditions)

        delete_query = sql.SQL("""
            DELETE FROM {table_name}
            WHERE NOT (
                {valid_match_expression}
            )
        """).format(
            table_name=sql.Identifier(
                table_name
            ),
            valid_match_expression=(
                valid_match_expression
            ),
        )

        cur.execute(delete_query)

        removed_rows = cur.rowcount

        count_query = sql.SQL("""
            SELECT COUNT(*)
            FROM {table_name}
        """).format(
            table_name=sql.Identifier(
                table_name
            ),
        )

        cur.execute(count_query)

        valid_matches_count = cur.fetchone()[0]

        conn.commit()

        return (
            removed_rows,
            valid_matches_count,
        )

    except Exception:
        if conn:
            conn.rollback()

        raise

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()
def reset_competitors_table_for_demo(client, pipeline_run_id=None):
    client_id = client["id"]
    prefix = client.get("store_prefix") or client["slug"]
    table_name = f"{prefix}_competitors"

    if not DEMO_TRUNCATE_COMPETITORS:
        print(f"[DEMO RESET] Skipping truncate for table={table_name}", flush=True)
        return

    started_at = datetime.now()
    task_name = "reset_competitors_table"

    print(f"[DEMO RESET] Truncating competitors table if exists: {table_name}", flush=True)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("SELECT to_regclass(%s)", (table_name,))
        exists = cur.fetchone()[0]

        if exists:
            cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY")
            print(f"[DEMO RESET] Truncated table={table_name}", flush=True)
        else:
            print(f"[DEMO RESET] Table does not exist yet: {table_name}", flush=True)

        conn.commit()
        cur.close()
        conn.close()

        save_task_run(
            client_id,
            pipeline_run_id,
            task_name,
            "success",
            started_at=started_at,
            finished_at=datetime.now(),
            log_excerpt=f"Truncated table={table_name}" if exists else f"Table not exists={table_name}",
        )

    except Exception as e:
        error_msg = str(e)
        save_task_run(
            client_id,
            pipeline_run_id,
            task_name,
            "failed",
            error_msg=error_msg,
            started_at=started_at,
            finished_at=datetime.now(),
        )
        save_error_log(client_id, "pipeline", error_msg, error_code="reset_competitors_failed")
        raise


@capture_task_prints(
    lambda client:
        client.get("store_prefix")
        or client["slug"]
)
def ingest_client_products(client, pipeline_run_id=None):
    sys.path.insert(0, "/opt/airflow/dags")

    from processors.data_ingestor import DataExtractor, DataLoader

    client_id = client["id"]
    prefix = client.get("store_prefix") or client["slug"]
    products_table = f"{prefix}_products"

    source_type = client.get("source_type")
    source_path = client.get("source_path")
    source_url = client.get("source_url")
    file_format = client.get("file_format")
    field_mapping = normalize_field_mapping(
        client.get("field_mapping")
    )

    try:
        if source_type == "url" and source_url:
            source_path = source_url

        if not source_type or not source_path or not file_format:
            raise ValueError(
                f"Client {client['id']} has incomplete source config: "
                f"source_type={source_type}, "
                f"source_path={source_path}, "
                f"file_format={file_format}"
            )

        config = {
            "store_name": prefix,
            "source_type": source_type,
            "source_path": source_path,
            "file_format": file_format,
            "field_mapping": field_mapping,
        }

        print(
            f"[INGEST] Client={client['name']} "
            f"prefix={prefix}",
            flush=True,
        )

        print(
            f"[INGEST] Source={source_type} "
            f"path={source_path} "
            f"format={file_format}",
            flush=True,
        )

        print(
            f"[INGEST] Target table={products_table}",
            flush=True,
        )

        print(
            "[INGEST] Rozpoczynam pobieranie, "
            "walidację i normalizację danych.",
            flush=True,
        )

        extractor = DataExtractor(config=config)
        loader = DataLoader(table_name=products_table)

        loader.load(
            extractor.extract(),
            batch_size=500,
        )

        row_count = count_rows(products_table) or 0

        print(
            "[INGEST] Zakończono zapis danych "
            "klienta do bazy.",
            flush=True,
        )

        print(
            f"[INGEST] Produkty zapisane w tabeli: "
            f"{products_table}",
            flush=True,
        )

        print(
            f"[INGEST] Liczba produktów: "
            f"{row_count}",
            flush=True,
        )

        return {
            "products_table": products_table,
            "rows": row_count,
        }

    except Exception as e:
        error_msg = str(e)

        save_error_log(
            client_id,
            "ingest",
            error_msg,
            error_code="ingest_failed",
        )

        print(
            f"[INGEST] FAILED "
            f"client={client['name']}: "
            f"{error_msg}",
            flush=True,
        )

        raise




def run_spider_for_client(
    client,
    spider_name,
    pipeline_run_id=None,
):
    client_id = client["id"]
    prefix = (
        client.get("store_prefix")
        or client["slug"]
    )

    target_table = f"{prefix}_competitors"

    limit_setting = ""

    if SCRAPY_ITEM_LIMIT and SCRAPY_ITEM_LIMIT > 0:
        limit_setting = (
            f"-s CLOSESPIDER_ITEMCOUNT="
            f"{SCRAPY_ITEM_LIMIT} "
        )

    command = (
        "export PYTHONPATH="
        "$PYTHONPATH:/opt/airflow/dags && "
        "cd /opt/airflow/dags/"
        "ecommerce_price_comparer && "
        f"scrapy crawl {shlex.quote(spider_name)} "
        "-s LOG_LEVEL=INFO "
        f"{limit_setting}"
        f"-a target_table={shlex.quote(target_table)} "
        f"-a store_prefix={shlex.quote(prefix)}"
    )

    started_at = datetime.now()
    task_name = f"scraper_{spider_name}"

    print(
        f"[SCRAPER] Client={client['name']} "
        f"spider={spider_name}",
        flush=True,
    )
    print(
        f"[SCRAPER] Limit={SCRAPY_ITEM_LIMIT}",
        flush=True,
    )
    print(
        f"[SCRAPER] Target table={target_table}",
        flush=True,
    )
    print(
        f"[SCRAPER] Command={command}",
        flush=True,
    )

    lines = []

    task_run_id = create_live_task_run(
        client_id=client_id,
        pipeline_run_id=pipeline_run_id,
        task_id=task_name,
        started_at=started_at,
    )

    last_database_update = time.monotonic()

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )

        if process.stdout is None:
            raise RuntimeError(
                "Nie udało się odczytać "
                "wyjścia procesu scrapera."
            )

        for line in process.stdout:
            clean_line = line.rstrip()

            print(clean_line, flush=True)
            lines.append(clean_line)

            if len(lines) > 300:
                lines = lines[-300:]

            current_time = time.monotonic()

            if current_time - last_database_update >= 2:
                update_live_task_run(
                    task_run_id=task_run_id,
                    status="running",
                    log_excerpt="\n".join(
                        lines[-200:]
                    ),
                )

                last_database_update = current_time

        return_code = process.wait()

        log_excerpt = "\n".join(
            lines[-200:]
        )

        row_count = count_rows(target_table)

        if return_code != 0:
            error_msg = (
                f"Spider {spider_name} "
                f"failed with code {return_code}"
            )

            if task_run_id:
                update_live_task_run(
                    task_run_id=task_run_id,
                    status="failed",
                    log_excerpt=log_excerpt,
                    error_msg=error_msg,
                    finished_at=datetime.now(),
                )
            else:
                save_task_run(
                    client_id,
                    pipeline_run_id,
                    task_name,
                    "failed",
                    error_msg=error_msg,
                    log_excerpt=log_excerpt,
                    started_at=started_at,
                    finished_at=datetime.now(),
                )

            save_error_log(
                client_id,
                "scraper",
                error_msg,
                error_code="scraper_failed",
            )

            mark_scraper_error(
                client_id,
                spider_name,
                error_msg,
            )

            if SCRAPER_FAIL_DAG_ON_ERROR:
                raise RuntimeError(error_msg)

            return {
                "success": False,
                "spider": spider_name,
                "error": error_msg,
            }

        clear_scraper_error(
            client_id,
            spider_name,
        )

        final_log = (
            f"{log_excerpt}\n"
            f"[SCRAPER] Finished "
            f"spider={spider_name}, "
            f"target_rows={row_count}"
        ).strip()

        if task_run_id:
            update_live_task_run(
                task_run_id=task_run_id,
                status="success",
                log_excerpt=final_log,
                finished_at=datetime.now(),
            )
        else:
            save_task_run(
                client_id,
                pipeline_run_id,
                task_name,
                "success",
                log_excerpt=final_log,
                started_at=started_at,
                finished_at=datetime.now(),
            )

        print(
            f"[SCRAPER] Finished "
            f"spider={spider_name}, "
            f"target_rows={row_count}",
            flush=True,
        )

        return {
            "success": True,
            "spider": spider_name,
            "target_rows": row_count,
        }

    except Exception as e:
        error_msg = str(e)
        log_excerpt = "\n".join(
            lines[-200:]
        )

        if task_run_id:
            update_live_task_run(
                task_run_id=task_run_id,
                status="failed",
                log_excerpt=log_excerpt,
                error_msg=error_msg,
                finished_at=datetime.now(),
            )
        else:
            save_task_run(
                client_id,
                pipeline_run_id,
                task_name,
                "failed",
                error_msg=error_msg,
                log_excerpt=log_excerpt,
                started_at=started_at,
                finished_at=datetime.now(),
            )

        save_error_log(
            client_id,
            "scraper",
            error_msg,
            error_code="scraper_exception",
        )

        mark_scraper_error(
            client_id,
            spider_name,
            error_msg,
        )

        print(
            f"[SCRAPER] EXCEPTION "
            f"spider={spider_name}: "
            f"{error_msg}",
            flush=True,
        )

        if SCRAPER_FAIL_DAG_ON_ERROR:
            raise

        return {
            "success": False,
            "spider": spider_name,
            "error": error_msg,
        }
@capture_task_prints(
    lambda client: "run_matching"
)
def run_matching_for_client(client, pipeline_run_id=None):
    sys.path.insert(0, "/opt/airflow/dags")

    from processors.files_connector import (
        MultiTenantReportGenerator
    )

    client_id = client["id"]
    prefix = client.get("store_prefix") or client["slug"]

    spiders = normalize_spiders(
        client.get("spiders_to_run")
    )

    competitor_stores = [
        spider.lower()
        for spider in spiders
    ]

    try:
        if not competitor_stores:
            print(
                f"[MATCHING] Pominięto klienta "
                f"{client['name']}, ponieważ lista "
                f"scraperów jest pusta.",
                flush=True,
            )

            return {
                "skipped": True,
                "reason": "spiders_to_run is empty",
            }

        config = {
            "client_table": f"{prefix}_products",
            "competitor_table": f"{prefix}_competitors",
            "target_table": f"{prefix}_matches",
            "competitor_stores": competitor_stores,
        }

        name_threshold = (
            client.get("match_name_threshold")
            or 90
        )

        color_threshold = (
            client.get("match_color_threshold")
            or 80
        )

        maker_threshold = (
            client.get("match_maker_threshold")
            or 80
        )

        print(
            f"[MATCHING] Client={client['name']} "
            f"prefix={prefix}",
            flush=True,
        )

        print(
            f"[MATCHING] Tabela produktów klienta: "
            f"{config['client_table']}",
            flush=True,
        )

        print(
            f"[MATCHING] Tabela ofert konkurencji: "
            f"{config['competitor_table']}",
            flush=True,
        )

        print(
            f"[MATCHING] Tabela wynikowa: "
            f"{config['target_table']}",
            flush=True,
        )

        print(
            f"[MATCHING] Sklepy konkurencji: "
            f"{', '.join(competitor_stores)}",
            flush=True,
        )

        print(
            f"[MATCHING] Progi: "
            f"nazwa={name_threshold}, "
            f"kolor={color_threshold}, "
            f"producent={maker_threshold}",
            flush=True,
        )

        print(
            "[MATCHING] Rozpoczynam dopasowywanie "
            "produktów.",
            flush=True,
        )

        generator = MultiTenantReportGenerator(
            config=config
        )

        result = generator.generate_report(
            name_threshold=name_threshold,
            color_threshold=color_threshold,
            maker_threshold=maker_threshold,
        )

        matches_table = f"{prefix}_matches"

        generated_rows = (
            count_rows(matches_table)
            or 0
        )

        removed_empty_rows, matches_count = (
            remove_incomplete_match_rows(
                matches_table
            )
        )

        print(
            f"[MATCHING] Wygenerowano wierszy: "
            f"{generated_rows}",
            flush=True,
        )

        print(
            f"[MATCHING] Usunięto pustych lub "
            f"niekompletnych wierszy: "
            f"{removed_empty_rows}",
            flush=True,
        )

        print(
            f"[MATCHING] Poprawnych dopasowań: "
            f"{matches_count}",
            flush=True,
        )

        if result is None:
            print(
                "[MATCHING] Generator nie zwrócił "
                "obiektu raportu, ale wyniki zostały "
                "sprawdzone bezpośrednio w bazie.",
                flush=True,
            )
        else:
            print(
                "[MATCHING] Proces matchowania "
                "zakończony poprawnie.",
                flush=True,
            )

        return {
            "generated_rows": generated_rows,
            "removed_incomplete_rows": (
                removed_empty_rows
            ),
            "matches_count": matches_count,
        }

    except Exception as e:
        error_msg = str(e)

        save_error_log(
            client_id,
            "matching",
            error_msg,
            error_code="matching_failed",
        )

        print(
            f"[MATCHING] FAILED "
            f"client={client['name']}: "
            f"{error_msg}",
            flush=True,
        )

        raise


    except Exception as e:
        error_msg = str(e)

        save_task_run(
            client_id,
            pipeline_run_id,
            task_name,
            "failed",
            error_msg=error_msg,
            started_at=started_at,
            finished_at=datetime.now(),
        )

        save_error_log(client_id, "matching", error_msg, error_code="matching_failed")

        print(f"[MATCH] FAILED client={client['name']}: {error_msg}", flush=True)
        raise


def finish_pipeline_success(client, pipeline_run_id=None):
    client_id = client["id"]
    save_pipeline_run(client_id, "success", run_id=pipeline_run_id)


def finish_pipeline_failed(client, pipeline_run_id=None):
    client_id = client["id"]
    save_pipeline_run(
        client_id,
        "failed",
        error_msg="One or more tasks failed. Check pipeline_task_runs or Airflow logs.",
        run_id=pipeline_run_id,
    )


def load_clients_for_dag_parse():
    try:
        clients = get_active_clients()
        print(f"[DAG PARSE] Loaded active clients count={len(clients)}", flush=True)
        return clients
    except Exception as exc:
        print(f"[DAG PARSE] Could not load active clients: {exc}", flush=True)
        return []


ACTIVE_CLIENTS = load_clients_for_dag_parse()


with DAG(
    dag_id="multi_client_pipeline",
    start_date=datetime(2026, 5, 1),
    schedule=PIPELINE_SCHEDULE_CRON,
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["production", "multi-client", "demo"],
) as dag:
    start = EmptyOperator(task_id="start")

    end = EmptyOperator(
        task_id="end",
        trigger_rule=TriggerRule.ALL_DONE,
    )

    if not ACTIVE_CLIENTS:
        no_clients = EmptyOperator(task_id="no_active_clients")
        start >> no_clients >> end

    for client in ACTIVE_CLIENTS:
        prefix = client.get("store_prefix") or client["slug"]
        client_group_id = f"client_{safe_task_id(prefix)}"
        spiders = normalize_spiders(client.get("spiders_to_run"))

        with TaskGroup(group_id=client_group_id) as client_group:
            create_run = PythonOperator(
                task_id="create_pipeline_run",
                python_callable=create_pipeline_run,
                op_kwargs={
                    "client_id": client["id"],
                },
            )

            ingest = PythonOperator(
                task_id="ingest",
                python_callable=ingest_client_products,
                op_kwargs={
                    "client": client,
                    "pipeline_run_id": create_run.output,
                },
            )

            reset_competitors = PythonOperator(
                task_id="reset_competitors_demo",
                python_callable=reset_competitors_table_for_demo,
                op_kwargs={
                    "client": client,
                    "pipeline_run_id": create_run.output,
                },
            )

            scraper_tasks = []

            for spider_name in spiders:
                if spider_name == prefix:
                    continue
                    
                scraper_task = PythonOperator(
                    task_id=f"scrape_{safe_task_id(spider_name)}",
                    python_callable=run_spider_for_client,
                    op_kwargs={
                        "client": client,
                        "spider_name": spider_name,
                        "pipeline_run_id": create_run.output,
                    },
                )

                reset_competitors >> scraper_task
                scraper_tasks.append(scraper_task)

            matching = PythonOperator(
                task_id="matching",
                python_callable=run_matching_for_client,
                op_kwargs={
                    "client": client,
                    "pipeline_run_id": create_run.output,
                },
                trigger_rule=TriggerRule.ALL_DONE,
            )

            finish_success = PythonOperator(
                task_id="finish_pipeline_success",
                python_callable=finish_pipeline_success,
                op_kwargs={
                    "client": client,
                    "pipeline_run_id": create_run.output,
                },
                trigger_rule=TriggerRule.ALL_SUCCESS,
            )

            finish_failed = PythonOperator(
                task_id="finish_pipeline_failed",
                python_callable=finish_pipeline_failed,
                op_kwargs={
                    "client": client,
                    "pipeline_run_id": create_run.output,
                },
                trigger_rule=TriggerRule.ONE_FAILED,
            )

            create_run >> ingest >> reset_competitors

            if scraper_tasks:
                scraper_tasks >> matching
            else:
                reset_competitors >> matching

            matching >> [finish_success, finish_failed]

        start >> client_group >> end