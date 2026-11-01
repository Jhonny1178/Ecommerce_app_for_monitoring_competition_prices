import os
import psycopg2
from psycopg2.extras import DictCursor
from groq import Groq
import time
from datetime import datetime, timedelta

class GroqManager:
    def __init__(self):
        self.db_host = os.getenv("APP_DB_HOST", "ecommerce-db")
        self.db_port = os.getenv("APP_DB_PORT", "5432")
        self.db_name = os.getenv("APP_DB_NAME", "ecommerce_data")
        self.db_user = os.getenv("APP_DB_USER", "postgres")
        self.db_password = os.getenv("APP_DB_PASSWORD", "eroch")
        
        self.keys = {
            "GROQ_API_KEY_1": os.getenv("GROQ_API_KEY_1"),
            "GROQ_API_KEY_2": os.getenv("GROQ_API_KEY_2"),
            "GROQ_API_KEY_3": os.getenv("GROQ_API_KEY_3"),
        }

    def _get_conn(self):
        return psycopg2.connect(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password
        )

    def _unblock_keys_if_needed(self, conn):
        # Odblokowuje klucze wyczerpane dawniej niż 24 godziny temu
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE api_keys_usage
                SET is_exhausted = FALSE, exhausted_at = NULL
                WHERE is_exhausted = TRUE AND exhausted_at < NOW() - INTERVAL '24 hours'
            """)
            conn.commit()

    def get_client(self):
        with self._get_conn() as conn:
            self._unblock_keys_if_needed(conn)
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("""
                    SELECT key_name FROM api_keys_usage
                    WHERE is_exhausted = FALSE
                    ORDER BY key_name ASC
                    LIMIT 1
                """)
                row = cur.fetchone()
                if not row:
                    self._log_critical_error(conn, "Wszystkie tokeny Groq zostały wykorzystane!")
                    raise Exception("Wszystkie klucze API Groq zostały wykorzystane. Chwilowo ta usługa jest niedostępna.")
                
                key_name = row['key_name']
                api_key = self.keys.get(key_name)
                
                if not api_key:
                    raise Exception(f"Brak wartości dla klucza {key_name} w pliku .env!")
                
                return Groq(api_key=api_key), key_name

    def mark_exhausted(self, key_name):
        print(f"\\n[GROQ MANAGER] Oznaczam klucz {key_name} jako wyczerpany (Rate Limit/Quota) i przechodzę do kolejnego...\\n", flush=True)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE api_keys_usage
                    SET is_exhausted = TRUE, exhausted_at = NOW()
                    WHERE key_name = %s
                """, (key_name,))
                conn.commit()

    def add_usage(self, key_name, tokens):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE api_keys_usage
                    SET total_tokens = total_tokens + %s, last_used = NOW()
                    WHERE key_name = %s
                """, (tokens, key_name))
                conn.commit()

    def _log_critical_error(self, conn, message):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM error_logs 
                WHERE error_type = 'GROQ_API_EXHAUSTED' 
                AND created_at > NOW() - INTERVAL '5 minutes'
            """)
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO error_logs (category, message, error_type)
                    VALUES ('API', %s, 'GROQ_API_EXHAUSTED')
                """, (message,))
                conn.commit()

    def execute_with_fallback(self, func):
        last_exception = None
        for _ in range(3):
            try:
                client, key_name = self.get_client()
            except Exception as e:
                # Jeśli Exception to "Wszystkie klucze...", rzucamy wyżej.
                raise e

            try:
                response = func(client)
                
                if hasattr(response, 'usage') and response.usage:
                    tokens = response.usage.total_tokens
                    self.add_usage(key_name, tokens)
                
                return response
            except Exception as e:
                error_str = str(e).lower()
                if 'per minute' in error_str and 'request too large' not in error_str:
                    response = getattr(e, 'response', None)
                    headers = getattr(response, 'headers', None) or {}
                    wait_seconds = min(float(headers.get('retry-after', 60)) + 1, 65)
                    print(f"[GROQ MANAGER] Limit minutowy klucza {key_name}. Czekam {wait_seconds} s.", flush=True)
                    time.sleep(wait_seconds)
                    last_exception = e
                    continue

                if '429' in error_str or 'rate limit' in error_str or 'quota' in error_str or 'insufficient_quota' in error_str:
                    self.mark_exhausted(key_name)
                    last_exception = e
                    continue
                else:
                    raise e
        
        if last_exception:
            raise last_exception
        raise Exception("Wszystkie klucze API Groq zostały wykorzystane. Chwilowo ta usługa jest niedostępna.")
