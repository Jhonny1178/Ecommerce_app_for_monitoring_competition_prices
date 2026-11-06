# webapp/utils.py
import os
import psycopg2
from psycopg2 import sql
import psycopg2.extras
import hashlib
import hmac
import ipaddress
import re
import socket
import unicodedata
from functools import wraps
from urllib.parse import urlparse
from flask import session, redirect, url_for, flash,request,jsonify,Blueprint
from werkzeug.security import generate_password_hash, check_password_hash

DB_CONFIG = {
    "host": os.environ.get("APP_DB_HOST", "ecommerce-db"),
    "port": int(os.environ.get("APP_DB_PORT", 5432)),
    "dbname": os.environ.get("APP_DB_NAME", "ecommerce_data"),
    "user": os.environ.get("APP_DB_USER", "postgres"),
    "password": os.environ.get("APP_DB_PASSWORD"),
}

def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn
def get_db_transaction():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    return conn

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    """
    Zwraca (czy_hasło_poprawne, czy_trzeba_przehashować).
    Obsługuje stare hashe SHA-256 bez soli — po udanym logowaniu
    należy je zastąpić nowym hashem (hash_password).
    """
    if not stored_hash:
        return False, False

    if re.fullmatch(r"[0-9a-f]{64}", stored_hash):
        legacy = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(legacy, stored_hash), True

    try:
        return check_password_hash(stored_hash, password), False
    except ValueError:
        return False, False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def is_public_host(hostname: str, allow_unresolved: bool = False) -> bool:
    if not hostname or any(ord(char) <= 32 or ord(char) == 127 for char in hostname):
        return False

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return allow_unresolved
    except ValueError:
        return False

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False

    return True


def is_allowed_external_url(url: str) -> bool:
    if "://" not in url:
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    return is_public_host(hostname, allow_unresolved=True)


def session_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:32]


def refresh_session_from_db():
    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT u.password_hash, u.is_admin, u.status, u.client_id,
                   c.slug AS client_slug, c.store_prefix
            FROM users u
            LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.id = %s
        """, (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()
    except psycopg2.Error:
        return jsonify({"ok": False, "error": "Serwis chwilowo niedostępny"}), 503

    if not user or session.get("password_fingerprint") != session_fingerprint(user["password_hash"]):
        session.clear()
        return None

    session["is_admin"] = bool(user["is_admin"])
    session["status"] = user["status"] or "pending_admin"
    session["client_id"] = user["client_id"]
    session["client_slug"] = user["client_slug"]
    session["store_prefix"] = user["store_prefix"]
    return None


WEBSITE_HOST_PATTERN = re.compile(r"(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}")


def is_existing_website(url: str) -> bool:
    if "://" not in url:
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if not hostname or not WEBSITE_HOST_PATTERN.fullmatch(hostname):
        return False

    try:
        socket.getaddrinfo(hostname, None)
    except (socket.gaierror, ValueError):
        return False

    return True


def generate_store_prefix(raw_name: str) -> str:
    value = unicodedata.normalize("NFKD", str(raw_name))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")

    if not value:
        value = "client"

    if value[0].isdigit():
        value = f"client_{value}"

    return value[:50]

def generate_unique_store_prefix(conn, raw_name: str) -> str:
    base = generate_store_prefix(raw_name)

    with conn.cursor() as cur:
        candidate = base
        i = 1

        while True:
            cur.execute("""
                SELECT 1
                FROM clients
                WHERE slug = %s OR store_prefix = %s
                LIMIT 1
            """, (candidate, candidate))

            if cur.fetchone() is None:
                return candidate

            i += 1
            candidate = f"{base}_{i}"

def table_identifier(prefix: str, suffix: str):
    return sql.Identifier(f"{prefix}_{suffix}")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
            return redirect(url_for("auth.login"))
        if not session.get("is_admin") and session.get("status") != "active":
            return jsonify({"ok": False, "error": "Konto nie jest aktywne", "status": session.get("status")}), 403
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
            return redirect(url_for("auth.login"))
        if not session.get("is_admin"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            flash("Brak uprawnień administratora.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated