from flask import Blueprint, request, session, jsonify
import glob
import json
import os
import traceback
import smtplib
from email.message import EmailMessage
import string
import secrets
import re
import datetime
from zoneinfo import ZoneInfo
import psycopg2
import psycopg2.extras
from psycopg2 import errors
import requests
from werkzeug.utils import secure_filename

from utils import get_db, get_db_transaction, hash_password, verify_password, hash_token, admin_required, is_allowed_external_url, is_existing_website, session_fingerprint
from services.provisioning import provision_client_from_request
from services.spider_state import ACTIVE, spider_run_state
from extensions import limiter, user_rate_key


auth_bp = Blueprint("auth", __name__)

UPLOAD_ROOT = os.environ.get("ONBOARDING_UPLOAD_DIR", "/tmp/onboarding")

_DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


MAX_COMPETITOR_URLS = 5
EMAIL_PATTERN = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")


def _forbidden_url_response(competitor_urls, *other_urls):
    if len(competitor_urls) > MAX_COMPETITOR_URLS:
        return jsonify({"ok": False, "error": f"Można podać maksymalnie {MAX_COMPETITOR_URLS} adresów konkurencji"}), 400

    for url in (*other_urls, *competitor_urls):
        if not url:
            continue
        if not is_allowed_external_url(url):
            return jsonify({"ok": False, "error": f"Niedozwolony adres: {url}"}), 400
        if not is_existing_website(url):
            return jsonify({"ok": False, "error": f"Nie znaleziono strony pod adresem: {url}. Sprawdź, czy link jest poprawny."}), 400
    return None


def _email_rate_key():
    data = request.get_json(silent=True) or {}
    return "email:" + str(data.get("email", "")).strip().lower()


# ============================================================
# Helpers
# ============================================================

def _normalize_list(value):
    if value is None:
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return []

        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass

        return [line.strip() for line in value.splitlines() if line.strip()]

    return []


def _latest_onboarding_request(cur, user_id):
    cur.execute("""
        SELECT *
        FROM onboarding_requests
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (user_id,))
    return cur.fetchone()


def _safe_slug(value, fallback):
    value = (value or fallback or "").strip().lower()

    value = (
        value
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .replace(".", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace("?", "_")
        .replace("&", "_")
        .replace("=", "_")
        .replace(" ", "_")
        .strip("_")
    )

    return value or fallback


def _trigger_scraper_generator_async(request_id, user_id, store_slug, urls):
    """
    Odpala generator scraperów po rejestracji.
    Na tym etapie NIE istnieje jeszcze client_id.
    Generator powinien zapisać scrapery w scraper_registry po request_id.
    """
    urls = _normalize_list(urls)

    if not urls:
        print("No competitor URLs provided. Skipping scraper generator.")
        return

    payload = {
        "request_id": request_id,
        "user_id": user_id,
        "client_id": None,
        "store_slug": store_slug,
        "urls": urls,
    }

    def _fire_generator(p):
        try:
            response = requests.post(
                "http://ai_generator:8080/api/check",
                json=p,
                timeout=10,
            )
            print(
                f"Scraper generator triggered. "
                f"Status={response.status_code}, body={response.text[:300]}"
            )
        except Exception as e:
            print(f"Failed to auto-trigger scraper generator: {e}")

    import threading
    threading.Thread(
        target=_fire_generator,
        args=(payload,),
        daemon=True,
    ).start()


def _remove_rejected_application_files(user_id, request_ids):
    warning = None

    try:
        for path in glob.glob(os.path.join(UPLOAD_ROOT, f"user_{user_id}_*")):
            os.remove(path)
    except OSError as e:
        print(f"Failed to remove uploaded files of user {user_id}: {e}")
        warning = "Wniosek odrzucono, ale nie udało się usunąć przesłanego pliku."

    for request_id in request_ids:
        try:
            response = requests.delete(
                f"http://ai_generator:8080/api/requests/{request_id}/spiders",
                timeout=10,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Failed to remove spiders of request {request_id}: {e}")
            warning = "Wniosek odrzucono, ale nie udało się usunąć plików scrapera."

    return warning


def _attach_scrapers_to_client(cur, request_id, client_id, store_prefix, admin_user_id):
    """
    Po akceptacji admina przypina wygenerowane scrapery z request_id do client_id
    i zwraca listę spider_name do zapisania w clients.spiders_to_run.
    """
    output_table = f"{store_prefix}_competitors"

    cur.execute("""
        UPDATE scraper_registry
        SET client_id = %s,
            store_slug = %s,
            output_table = %s,
            status = CASE
                WHEN status IN ('generated', 'ready', 'success', 'scraper_review') THEN 'approved'
                ELSE status
            END,
            approved_by = %s,
            approved_at = COALESCE(approved_at, NOW())
        WHERE request_id = %s
          AND client_id IS NULL
          AND status NOT IN ('failed', 'rejected')
        RETURNING spider_name
    """, (
        client_id,
        store_prefix,
        output_table,
        admin_user_id,
        request_id,
    ))

    rows = cur.fetchall()
    spider_names = [
        row["spider_name"]
        for row in rows
        if row.get("spider_name")
    ]

    cur.execute("""
        SELECT spider_name
        FROM scraper_registry
        WHERE request_id = %s
          AND client_id = %s
          AND status NOT IN ('failed', 'rejected')
    """, (
        request_id,
        client_id,
    ))

    for row in cur.fetchall():
        spider_name = row.get("spider_name")
        if spider_name and spider_name not in spider_names:
            spider_names.append(spider_name)

    return spider_names


def send_email(subject, body, to_email=None):
    app_password = os.environ.get("EROCH_GMAIL")
    sender_email = os.environ.get("GMAIL_USER")
    default_to = os.environ.get("SMTP_DEFAULT_TO")
    to_email = to_email or default_to

    if not app_password or not sender_email or not to_email:
        print(f"Brak pełnych danych do wysłania e-maila (to_email={to_email}).")
        return

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, app_password)
            server.send_message(msg)
        print("E-mail wysłany pomyślnie przez Gmail.")
    except Exception as e:
        print(f"Błąd wysyłania e-maila przez Gmail: {e}")

def _utc_iso(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=datetime.timezone.utc
        )

    return (
        value
        .astimezone(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

def _cron_as_polish_time(cron_expression):
    try:
        parts = cron_expression.split()

        if len(parts) != 5:
            return cron_expression

        minute = int(parts[0])
        hour = int(parts[1])

        current_utc = datetime.datetime.now(
            datetime.timezone.utc
        )

        scheduled_utc = current_utc.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        scheduled_poland = scheduled_utc.astimezone(
            ZoneInfo("Europe/Warsaw")
        )

        return (
            "Codziennie o "
            f"{scheduled_poland.strftime('%H:%M')}"
        )

    except Exception:
        return cron_expression
# ============================================================
# Auth
# ============================================================

@auth_bp.route("/api/change_password", methods=["POST"])
@limiter.limit("10 per hour")
def api_change_password():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Brak autoryzacji"}), 401

    data = request.get_json() or {}
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "").strip()

    if not current_password:
        return jsonify({"ok": False, "error": "Podaj aktualne hasło"}), 400

    if len(new_password) < 12 or not any(char.isdigit() for char in new_password):
        return jsonify({"ok": False, "error": "Hasło musi mieć minimum 12 znaków i zawierać przynajmniej jedną cyfrę"}), 400

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        
        if not user or not verify_password(user["password_hash"], current_password)[0]:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Aktualne hasło jest niepoprawne"}), 400

        hashed = hash_password(new_password)
        cur.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s", (hashed, user_id))
        conn.commit()
        session["password_fingerprint"] = session_fingerprint(hashed)
        cur.close()
        conn.close()
        return jsonify({"ok": True, "message": "Hasło zostało zmienione"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@auth_bp.route("/api/forgot_password", methods=["POST"])
@limiter.limit("5 per hour")
@limiter.limit("3 per hour", key_func=_email_rate_key)
def api_forgot_password():
    data = request.get_json() or {}
    email = data.get("email", "").strip()

    if not email:
        return jsonify({"ok": False, "error": "Podaj adres e-mail"}), 400

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id FROM users WHERE email = %s OR username = %s", (email, email))
        user = cur.fetchone()

        if user:
            code = ''.join(secrets.choice(string.digits) for _ in range(6))
            hashed_code = hash_token(code)
            expires_at = datetime.datetime.now() + datetime.timedelta(minutes=15)

            cur.execute("DELETE FROM password_resets WHERE user_id = %s", (user["id"],))
            cur.execute("""
                INSERT INTO password_resets (user_id, reset_token_hash, expires_at)
                VALUES (%s, %s, %s)
            """, (user["id"], hashed_code, expires_at))
            conn.commit()

            subject = "Kod resetowania hasła"
            body = f"Twój kod do zresetowania hasła to: {code}\n\nKod jest ważny przez 15 minut."
            send_email(subject, body, to_email=email)

        cur.close()
        conn.close()

        return jsonify({"ok": True, "message": "Jeśli konto istnieje, wysłaliśmy kod na Twój e-mail."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@auth_bp.route("/api/reset_password", methods=["POST"])
@limiter.limit("10 per 15 minutes")
@limiter.limit("5 per 15 minutes", key_func=_email_rate_key)
def api_reset_password():
    data = request.get_json() or {}
    email = data.get("email", "").strip()
    code = data.get("code", "").strip()
    new_password = data.get("new_password", "").strip()

    if not email or not code or not new_password:
        return jsonify({"ok": False, "error": "Wypełnij wszystkie pola"}), 400

    if len(new_password) < 12 or not any(char.isdigit() for char in new_password):
        return jsonify({"ok": False, "error": "Hasło musi mieć minimum 12 znaków i zawierać przynajmniej jedną cyfrę"}), 400

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("SELECT id FROM users WHERE email = %s OR username = %s", (email, email))
        user = cur.fetchone()

        if not user:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Nieprawidłowy kod lub e-mail"}), 400

        user_id = user["id"]
        hashed_code = hash_token(code)

        cur.execute("""
            SELECT id FROM password_resets
            WHERE user_id = %s AND reset_token_hash = %s AND expires_at > NOW()
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, hashed_code))
        reset_record = cur.fetchone()

        if not reset_record:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Nieprawidłowy kod lub e-mail"}), 400

        hashed_password = hash_password(new_password)
        cur.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s", (hashed_password, user_id))
        cur.execute("DELETE FROM password_resets WHERE user_id = %s", (user_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"ok": True, "message": "Hasło zostało pomyślnie zmienione"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@auth_bp.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True, "message": "Logged out successfully"})


@auth_bp.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute; 30 per hour")
def api_login():
    data = request.get_json() or {}

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "Podaj login i hasło."}), 400

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                u.id,
                u.username,
                COALESCE(u.email, u.username) AS email,
                u.password_hash,
                u.is_admin,
                u.client_id,
                u.status,
                u.first_name,
                u.last_name,
                c.name AS client_name,
                c.slug AS client_slug,
                COALESCE(c.store_prefix, c.slug) AS store_prefix
            FROM users u
            LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.username = %s
        """, (username,))

        user = cur.fetchone()

        if user:
            password_ok, needs_rehash = verify_password(user["password_hash"], password)
        else:
            verify_password(_DUMMY_PASSWORD_HASH, password)
            password_ok, needs_rehash = False, False

        if password_ok and needs_rehash:
            user["password_hash"] = hash_password(password)
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (user["password_hash"], user["id"]),
            )

        cur.close()
        conn.close()

        if not password_ok:
            return jsonify({
                "ok": False,
                "error": "Nieprawidłowy login lub hasło."
            }), 401
        session.clear()
        session.permanent = True

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = bool(user.get("is_admin"))
        session["status"] = user.get("status") or "pending_admin"
        session["client_id"] = user.get("client_id")
        session["client_slug"] = user.get("client_slug")
        session["store_prefix"] = user.get("store_prefix")
        session["password_fingerprint"] = session_fingerprint(user["password_hash"])

        return jsonify({
            "ok": True,
            "message": "Zalogowano pomyślnie",
            "status": session["status"],
            "is_admin": session["is_admin"],
            "client_id": session.get("client_id"),
            "client_slug": session.get("client_slug"),
            "store_prefix": session.get("store_prefix"),
            "profile": {
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "email": user.get("email"),
                "client_name": user.get("client_name"),
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": f"Błąd połączenia z bazą: {e}"
        }), 500


@auth_bp.route("/api/register", methods=["POST"])
@limiter.limit("5 per hour")
def api_register():
    data = request.get_json() or {}

    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    company_domain = data.get("company_domain", "").strip()
    website_url = data.get("website_url", "").strip() or None

    competitor_urls = _normalize_list(
        data.get("urls") or data.get("competitor_urls")
    )

    requested_store_name = (
        data.get("store_name")
        or data.get("requested_store_name")
        or company_domain
    )
    requested_store_name = (requested_store_name or "").strip()

    if not first_name or not last_name or not email or not password or not company_domain:
        return jsonify({
            "ok": False,
            "error": "Wypełnij wszystkie pola!"
        }), 400

    if not EMAIL_PATTERN.fullmatch(email):
        return jsonify({
            "ok": False,
            "error": "Podaj poprawny adres e-mail"
        }), 400

    if len(password) < 12 or not any(char.isdigit() for char in password):
        return jsonify({
            "ok": False,
            "error": "Hasło musi mieć minimum 12 znaków i zawierać przynajmniej jedną cyfrę"
        }), 400

    if not competitor_urls:
        return jsonify({
            "ok": False,
            "error": "Provide at least one competitor URL!"
        }), 400

    forbidden = _forbidden_url_response(competitor_urls, website_url)
    if forbidden:
        return forbidden

    hashed = hash_password(password)
    urls_json = json.dumps(competitor_urls)

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users (
                username,
                email,
                password_hash,
                first_name,
                last_name,
                company_domain,
                competitor_urls,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, 'onboarding_required')
            RETURNING id
        """, (
            email,
            email,
            hashed,
            first_name,
            last_name,
            company_domain,
            urls_json
        ))

        user_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO onboarding_requests (
                user_id,
                requested_store_name,
                company_domain,
                website_url,
                competitor_urls,
                status
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, 'onboarding_required')
            RETURNING id
        """, (
            user_id,
            requested_store_name,
            company_domain,
            website_url,
            urls_json
        ))

        request_id = cur.fetchone()[0]

        temporary_store_slug = _safe_slug(
            company_domain or requested_store_name,
            f"request_{request_id}"
        )

        conn.commit()

        # Generator scraperów uruchamiamy teraz, ale BEZ client_id.
        # Wynik powinien zostać zapisany po request_id.
        _trigger_scraper_generator_async(
            request_id=request_id,
            user_id=user_id,
            store_slug=temporary_store_slug,
            urls=competitor_urls,
        )

        return jsonify({
            "ok": True,
            "message": "Registration successful. Please complete onboarding.",
            "request_id": request_id,
            "status": "onboarding_required"
        })

    except errors.UniqueViolation:
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": "User with this email already exists."
        }), 400

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"Database error: {e}"
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()


# ============================================================
# Profile / current user
# ============================================================

@auth_bp.route("/api/me", methods=["GET"])
def api_me():
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "Not authenticated"
        }), 401

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                u.id,
                u.username,
                COALESCE(u.email, u.username) AS email,
                u.first_name,
                u.last_name,
                u.phone,
                u.status,
                u.is_admin,
                u.company_domain,
                u.competitor_urls,
                u.subscription_plan,

                c.id AS client_id,
                c.name AS client_name,
                c.slug AS client_slug,
                c.store_prefix,
                c.website_url AS client_website_url,
                c.source_type AS client_source_type,
                c.source_path AS client_source_path,
                c.source_url AS client_source_url,
                c.file_format AS client_file_format,
                c.field_mapping AS client_field_mapping,
                c.spiders_to_run,
                c.is_active AS client_is_active
            FROM users u
            LEFT JOIN clients c ON c.id = u.client_id
            WHERE u.id = %s
        """, (user_id,))

        user = cur.fetchone()

        if not user:
            cur.close()
            conn.close()
            return jsonify({
                "ok": False,
                "error": "User not found"
            }), 404

        onboarding_request = _latest_onboarding_request(cur, user_id)

        onboarding_source = None
        field_mappings = []

        if onboarding_request:
            cur.execute("""
                SELECT
                    source_kind,
                    source_path,
                    source_url,
                    file_format,
                    original_name,
                    mime_type,
                    uploaded_at
                FROM onboarding_sources
                WHERE request_id = %s
                ORDER BY uploaded_at DESC
                LIMIT 1
            """, (onboarding_request["id"],))

            onboarding_source = cur.fetchone()

            cur.execute("""
                SELECT
                    external_field,
                    internal_field,
                    is_required,
                    sample_value
                FROM onboarding_field_mappings
                WHERE request_id = %s
                ORDER BY internal_field, external_field
            """, (onboarding_request["id"],))

            field_mappings = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "user": user,
            "onboarding_request": onboarding_request,
            "onboarding_source": onboarding_source,
            "field_mappings": field_mappings
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/onboarding/current", methods=["GET"])
def api_onboarding_current():
    return api_me()


# ============================================================
# Client profile / onboarding configuration
# ============================================================

def _save_onboarding_submission():
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "Not authenticated"
        }), 401

    source_type = (
        request.form.get("source_type")
        or request.form.get("source_kind")
        or "local"
    ).strip().lower()

    if source_type not in {"local", "url"}:
        return jsonify({
            "ok": False,
            "error": "source_type must be 'local' or 'url'"
        }), 400

    file_format = request.form.get("file_format", "").strip().lower() or None

    if file_format not in {"csv", "xml", "xlsx", "excel"}:
        return jsonify({
            "ok": False,
            "error": "file_format must be csv, xml, xlsx or excel"
        }), 400

    store_name = (
        request.form.get("store_name")
        or request.form.get("requested_store_name")
        or ""
    ).strip()

    company_domain = request.form.get("company_domain", "").strip() or None
    website_url = request.form.get("website_url", "").strip() or None
    source_url = request.form.get("source_url", "").strip() or None

    forbidden = _forbidden_url_response([], website_url, source_url)
    if forbidden:
        return forbidden

    field_mapping_raw = request.form.get("field_mapping", "{}")

    try:
        field_mapping = json.loads(field_mapping_raw)
    except json.JSONDecodeError:
        return jsonify({
            "ok": False,
            "error": "Invalid field_mapping JSON"
        }), 400

    if not isinstance(field_mapping, dict):
        return jsonify({
            "ok": False,
            "error": "field_mapping must be a JSON object"
        }), 400

    uploaded_file = request.files.get("file")

    source_path = None
    original_name = None
    mime_type = None

    if source_type == "local":
        if not uploaded_file or uploaded_file.filename == "":
            return jsonify({
                "ok": False,
                "error": "File is required for local source"
            }), 400

        filename = secure_filename(uploaded_file.filename)

        source_path = os.path.join(
            UPLOAD_ROOT,
            f"user_{user_id}_{filename}"
        )

        original_name = uploaded_file.filename
        mime_type = uploaded_file.mimetype

    if source_type == "url":
        if not source_url:
            return jsonify({
                "ok": False,
                "error": "source_url is required for url source"
            }), 400

        source_path = source_url

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                id,
                client_id,
                requested_store_name
            FROM onboarding_requests
            WHERE user_id = %s
              AND status IN (
                    'onboarding_required',
                    'onboarding_submitted'
              )
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id,))

        onboarding_request = cur.fetchone()

        if not onboarding_request:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "No approved onboarding request found. Admin approval is required first."
            }), 403

        if source_type == "local":
            os.makedirs(UPLOAD_ROOT, exist_ok=True)
            uploaded_file.save(source_path)

        request_id = onboarding_request["id"]
        client_id = onboarding_request["client_id"]

        if not client_id:
            pass # Client will be created upon admin approval

        if not store_name:
            store_name = onboarding_request["requested_store_name"]

        cur.execute("""
            DELETE FROM onboarding_sources
            WHERE request_id = %s
        """, (request_id,))

        cur.execute("""
            INSERT INTO onboarding_sources (
                request_id,
                source_kind,
                source_path,
                source_url,
                file_format,
                original_name,
                mime_type
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            request_id,
            source_type,
            source_path,
            source_url,
            file_format,
            original_name,
            mime_type
        ))

        cur.execute("""
            DELETE FROM onboarding_field_mappings
            WHERE request_id = %s
        """, (request_id,))

        for external_field, internal_field in field_mapping.items():
            external = str(external_field).strip()
            internal = str(internal_field).strip()

            if not external or not internal:
                continue

            cur.execute("""
                INSERT INTO onboarding_field_mappings (
                    request_id,
                    external_field,
                    internal_field,
                    is_required
                )
                VALUES (%s, %s, %s, FALSE)
            """, (
                request_id,
                external,
                internal
            ))

        cur.execute("""
            UPDATE onboarding_requests
            SET requested_store_name = %s,
                company_domain = COALESCE(%s, company_domain),
                website_url = COALESCE(%s, website_url),
                source_type = %s,
                source_path = %s,
                source_url = %s,
                file_format = %s,
                field_mapping = %s::jsonb,
                status = 'awaiting_payment',
                updated_at = NOW()
            WHERE id = %s
        """, (
            store_name,
            company_domain,
            website_url,
            source_type,
            source_path,
            source_url,
            file_format,
            json.dumps(field_mapping),
            request_id
        ))

        cur.execute("""
            UPDATE users
            SET status = 'awaiting_payment',
                company_domain = COALESCE(%s, company_domain),
                updated_at = NOW()
            WHERE id = %s
        """, (
            company_domain,
            user_id
        ))

        conn.commit()

        session["status"] = "awaiting_payment"

        return jsonify({
            "ok": True,
            "message": "Onboarding completed. Awaiting package selection.",
            "request_id": request_id,
            "client_id": client_id,
            "status": "awaiting_payment",
            "request_status": "awaiting_payment",
            "source_type": source_type,
            "source_path": source_path,
            "source_url": source_url,
            "file_format": file_format
        })

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": f"Database error: {e}"
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()


@auth_bp.route("/api/onboarding/submit", methods=["POST"])
@limiter.limit("10 per hour", key_func=user_rate_key)
def api_onboarding_submit():
    return _save_onboarding_submission()

@auth_bp.route("/api/subscription/buy", methods=["POST"])
def api_subscription_buy():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json() or {}
    package = data.get("package", "Podstawowy")

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor()
        
        cur.execute("SELECT client_id, status FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        client_id = row[0] if row else None

        if not row or row[1] not in ("awaiting_payment", "active"):
            conn.rollback()
            return jsonify({"ok": False, "error": "Nie można wybrać planu przy obecnym statusie konta"}), 409
        
        new_status = 'active' if client_id else 'pending_admin'
        
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s, status = %s 
            WHERE id = %s
        """, (package, new_status, user_id))

        if not client_id:
            cur.execute("""
                UPDATE onboarding_requests 
                SET status = 'pending_admin' 
                WHERE user_id = %s AND status = 'awaiting_payment'
            """, (user_id,))

        conn.commit()
        session["status"] = new_status

        return jsonify({
            "ok": True,
            "message": "Subscription plan selected successfully.",
            "status": new_status
        })
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        conn.close()


@auth_bp.route("/api/upload_onboarding_file", methods=["POST"])
@limiter.limit("10 per hour", key_func=user_rate_key)
def upload_onboarding_file():
    return _save_onboarding_submission()


# ============================================================
# Contact / bug report
# ============================================================

MAX_MESSAGE_LENGTH = 5000


@auth_bp.route("/api/send_message", methods=["POST"])
@limiter.limit("5 per hour")
def api_send_message():
    data = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({
            "ok": False,
            "error": "Pusta wiadomość"
        }), 400

    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify({
            "ok": False,
            "error": f"Wiadomość może mieć maksymalnie {MAX_MESSAGE_LENGTH} znaków"
        }), 400

    username = session.get("username", "Nieznany")
    user_id = session.get("user_id")

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO error_logs (
                user_id,
                category,
                message,
                error_type,
                is_reviewed
            )
            VALUES (%s, 'Klient', %s, 'Zgłoszony', FALSE)
        """, (user_id, f"Wiadomość od {username}: {message}"))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        import traceback
        traceback.print_exc()

    return jsonify({
        "ok": True,
        "message": "Wysłano"
    })


@auth_bp.route("/api/report_bug", methods=["POST"])
@limiter.limit("10 per hour")
def api_report_bug():
    data = request.get_json() or {}

    message = data.get("message", "").strip()

    if not message:
        return jsonify({
            "ok": False,
            "error": "Brak treści"
        }), 400

    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify({
            "ok": False,
            "error": f"Zgłoszenie może mieć maksymalnie {MAX_MESSAGE_LENGTH} znaków"
        }), 400

    user_id = session.get("user_id")

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO error_logs (
                user_id,
                category,
                message,
                error_type,
                is_reviewed
            )
            VALUES (%s, 'Klient', %s, 'Zgłoszony', FALSE)
        """, (user_id, message))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"ok": True})

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ============================================================
# Admin - users / registration
# ============================================================

@auth_bp.route("/api/admin/pending_users", methods=["GET"])
@admin_required
def admin_pending_users():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                u.id,
                u.username,
                u.first_name,
                u.last_name,
                u.status AS user_status,
                u.client_id,
                u.company_domain,
                u.competitor_urls,

                r.id AS request_id,
                r.requested_store_name,
                r.requested_store_slug,
                r.competitor_urls AS request_competitor_urls,
                r.source_type,
                r.source_path,
                r.source_url,
                r.file_format,
                r.status AS request_status,
                TO_CHAR(r.created_at, 'DD.MM.YYYY') AS requested_date
            FROM onboarding_requests r
            JOIN users u ON u.id = r.user_id
            WHERE r.status IN (
                'onboarding_required',
                'pending_admin',
                'scraper_generating',
                'scraper_review'
            )
            ORDER BY r.created_at DESC
        """)

        users = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "users": users
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/admin/approve_user", methods=["POST"])
@admin_required
def admin_approve_user():
    data = request.get_json() or {}

    request_id = data.get("request_id")
    user_id = data.get("user_id")

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if not request_id and user_id:
            cur.execute("""
                SELECT id
                FROM onboarding_requests
                WHERE user_id = %s
                  AND status IN ('onboarding_required', 'pending_admin', 'scraper_generating', 'scraper_review')
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,))

            row = cur.fetchone()

            if not row:
                conn.rollback()
                return jsonify({
                    "ok": False,
                    "error": "No pending onboarding request found for this user."
                }), 404

            request_id = row["id"]

        if not request_id:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Missing request_id."
            }), 400

        cur.execute("""
            SELECT
                r.id,
                r.user_id,
                r.requested_store_name,
                r.requested_store_slug,
                r.company_domain,
                r.website_url,
                r.competitor_urls,
                r.source_type,
                r.source_path,
                r.source_url,
                r.file_format,
                r.field_mapping,
                r.status
            FROM onboarding_requests r
            WHERE r.id = %s
              AND r.status IN ('onboarding_required', 'pending_admin', 'scraper_generating', 'scraper_review')
            LIMIT 1
        """, (request_id,))

        onboarding_request = cur.fetchone()

        if not onboarding_request:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Onboarding request not found or is not pending_admin/scraper_review."
            }), 404

        result = provision_client_from_request(
            conn=conn,
            user_id=onboarding_request["user_id"],
            request_id=onboarding_request["id"],
            store_name=onboarding_request["requested_store_name"],
            requested_store_slug=onboarding_request.get("requested_store_slug"),
            company_domain=onboarding_request.get("company_domain"),
            website_url=onboarding_request.get("website_url"),
            source_type=onboarding_request.get("source_type") or "pending",
            source_path=onboarding_request.get("source_path"),
            source_url=onboarding_request.get("source_url"),
            file_format=onboarding_request.get("file_format"),
            field_mapping=onboarding_request.get("field_mapping") or {},
        )

        spider_names = _attach_scrapers_to_client(
            cur=cur,
            request_id=onboarding_request["id"],
            client_id=result["client_id"],
            store_prefix=result["store_prefix"],
            admin_user_id=session["user_id"],
        )

        cur.execute("""
            UPDATE clients
            SET spiders_to_run = %s,
                is_active = TRUE,
                updated_at = NOW()
            WHERE id = %s
        """, (
            spider_names,
            result["client_id"],
        ))

        cur.execute("""
            UPDATE onboarding_requests
            SET approved_by = %s,
                approved_at = NOW(),
                status = 'active',
                updated_at = NOW()
            WHERE id = %s
        """, (
            session["user_id"],
            request_id
        ))

        cur.execute("""
            UPDATE users
            SET status = 'active',
                updated_at = NOW()
            WHERE id = %s
        """, (
            onboarding_request["user_id"],
        ))

        conn.commit()

        return jsonify({
            "ok": True,
            "message": "User approved, client provisioned and scrapers attached successfully.",
            "client_id": result.get("client_id"),
            "store_prefix": result.get("store_prefix"),
            "slug": result.get("slug"),
            "spiders_to_run": spider_names,
            "status": "active"
        })

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()


@auth_bp.route("/api/admin/reject_user", methods=["POST"])
def admin_reject_user():
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 403

    data = request.get_json() or {}

    user_id = data.get("user_id")
    request_id = data.get("request_id")
    reason = data.get("reason", "Odrzucono przez administratora")

    if not user_id and not request_id:
        return jsonify({
            "ok": False,
            "error": "Missing user_id or request_id"
        }), 400

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if request_id:
            cur.execute("""
                SELECT
                    r.user_id,
                    u.username,
                    u.first_name,
                    u.client_id
                FROM onboarding_requests r
                JOIN users u ON u.id = r.user_id
                WHERE r.id = %s
                LIMIT 1
            """, (request_id,))
        else:
            cur.execute("""
                SELECT
                    r.user_id,
                    u.username,
                    u.first_name,
                    u.client_id
                FROM onboarding_requests r
                JOIN users u ON u.id = r.user_id
                WHERE r.user_id = %s
                ORDER BY r.created_at DESC
                LIMIT 1
            """, (user_id,))

        onboarding_request = cur.fetchone()

        if not onboarding_request:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Onboarding request not found"
            }), 404

        if onboarding_request["client_id"]:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Nie można odrzucić zatwierdzonego klienta"
            }), 409

        rejected_user_id = onboarding_request["user_id"]

        cur.execute("SELECT id FROM onboarding_requests WHERE user_id = %s", (rejected_user_id,))
        request_ids = [row["id"] for row in cur.fetchall()]

        cur.execute("""
            DELETE FROM scraper_registry
            WHERE request_id = ANY(%s)
              AND client_id IS NULL
        """, (request_ids,))

        cur.execute("DELETE FROM scraper_logs WHERE user_id = %s", (rejected_user_id,))
        cur.execute("DELETE FROM onboarding_requests WHERE user_id = %s", (rejected_user_id,))

        cur.execute("""
            UPDATE users
            SET status = 'rejected',
                rejection_reason = %s,
                company_domain = NULL,
                competitor_urls = '[]'::jsonb,
                updated_at = NOW()
            WHERE id = %s
        """, (
            reason,
            rejected_user_id
        ))

        conn.commit()

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()

    warning = _remove_rejected_application_files(rejected_user_id, request_ids)

    send_email(
        "Twój wniosek do e-ROCH został odrzucony",
        (
            f"Witaj {onboarding_request['first_name']},\n\n"
            "Niestety, Twój wniosek o założenie konta został odrzucony.\n"
            "Po zalogowaniu możesz napisać do nas wiadomość."
        ),
        to_email=onboarding_request["username"]
    )

    result = {
        "ok": True,
        "message": "User rejected."
    }
    if warning:
        result["warning"] = warning

    return jsonify(result)


@auth_bp.route("/api/admin/registration_requests", methods=["GET"])
@admin_required
def get_registration_requests():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                r.id AS request_id,
                r.requested_store_name AS company_name,
                r.company_domain,
                r.website_url,
                r.competitor_urls,
                r.source_type,
                r.source_path,
                r.source_url,
                r.file_format,
                r.status,
                TO_CHAR(r.created_at, 'DD.MM.YYYY') AS requested_date,

                u.id AS user_id,
                u.username AS email,
                u.first_name,
                u.last_name,
                u.client_id
            FROM onboarding_requests r
            JOIN users u ON u.id = r.user_id
            ORDER BY r.created_at DESC
        """)

        requests_list = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "data": requests_list
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/admin/user_logs/<int:user_id>", methods=["GET"])
def admin_user_logs(user_id):
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 403

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                url,
                step,
                status,
                message,
                created_at
            FROM scraper_logs
            WHERE user_id = %s
            ORDER BY created_at ASC
        """, (user_id,))

        logs = cur.fetchall()

        for log in logs:
            if log.get("created_at"):
                log["created_at"] = log["created_at"].isoformat()

        cur.execute("""
            SELECT
                username AS email,
                first_name,
                last_name,
                company_domain,
                competitor_urls
            FROM users
            WHERE id = %s
        """, (user_id,))

        user_info = cur.fetchone()

        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "logs": logs,
            "user": user_info
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/admin/supported_stores", methods=["GET"])
@admin_required
def get_supported_stores():
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                id,
                id AS client_id,
                name AS store_name,
                slug AS store_domain,
                CASE
                    WHEN is_active = TRUE THEN 'Aktywny'
                    ELSE 'Wstrzymany'
                END AS status,
                TO_CHAR(created_at, 'DD.MM.YYYY') AS added_date,
                COALESCE(match_name_threshold, 70) AS match_name_threshold,
                COALESCE(match_color_threshold, 0) AS match_color_threshold,
                COALESCE(match_maker_threshold, 60) AS match_maker_threshold
            FROM clients
            ORDER BY name ASC
        """)

        stores = [dict(row) for row in cur.fetchall()]

        return jsonify({
            "ok": True,
            "data": stores
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ============================================================
# Admin - scrapers
# ============================================================

@auth_bp.route("/api/admin/onboarding_requests/<int:request_id>/generate_scrapers", methods=["POST"])
def generate_scrapers_for_request(request_id):
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 403

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                r.id AS request_id,
                r.user_id,
                r.client_id,
                r.requested_store_slug,
                r.requested_store_name,
                r.company_domain,
                r.competitor_urls,
                r.status,
                c.slug AS client_slug,
                c.store_prefix
            FROM onboarding_requests r
            LEFT JOIN clients c ON c.id = r.client_id
            WHERE r.id = %s
            LIMIT 1
        """, (request_id,))

        onboarding_request = cur.fetchone()

        if not onboarding_request:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Onboarding request not found"
            }), 404

        competitor_urls = _normalize_list(onboarding_request.get("competitor_urls") or [])

        store_slug = (
            onboarding_request.get("store_prefix")
            or onboarding_request.get("client_slug")
            or onboarding_request.get("requested_store_slug")
            or _safe_slug(
                onboarding_request.get("company_domain") or onboarding_request.get("requested_store_name"),
                f"request_{request_id}"
            )
        )

        payload = {
            "request_id": onboarding_request["request_id"],
            "user_id": onboarding_request["user_id"],
            "client_id": onboarding_request.get("client_id"),
            "store_slug": store_slug,
            "urls": competitor_urls
        }

        try:
            response = requests.post(
                "http://ai_generator:8080/api/check",
                json=payload,
                timeout=10
            )

            response_ok = 200 <= response.status_code < 300

        except requests.exceptions.RequestException as e:
            cur.execute("""
                INSERT INTO scraper_logs (
                    user_id,
                    client_id,
                    request_id,
                    url,
                    step,
                    status,
                    message
                )
                VALUES (%s, %s, %s, NULL, 'ai_generator_request', 'failed', %s)
            """, (
                onboarding_request["user_id"],
                onboarding_request.get("client_id"),
                onboarding_request["request_id"],
                str(e)
            ))

            conn.commit()

            return jsonify({
                "ok": False,
                "error": f"AI generator request failed: {e}"
            }), 500

        if response_ok:
            cur.execute("""
                UPDATE onboarding_requests
                SET status = 'scraper_generating',
                    updated_at = NOW()
                WHERE id = %s
            """, (request_id,))

            cur.execute("""
                INSERT INTO scraper_logs (
                    user_id,
                    client_id,
                    request_id,
                    url,
                    step,
                    status,
                    message
                )
                VALUES (%s, %s, %s, NULL, 'ai_generator_request', 'started', %s)
            """, (
                onboarding_request["user_id"],
                onboarding_request.get("client_id"),
                onboarding_request["request_id"],
                "AI scraper generator started"
            ))

            conn.commit()

            return jsonify({
                "ok": True,
                "message": "AI scraper generation started",
                "payload": payload
            })

        cur.execute("""
            INSERT INTO scraper_logs (
                user_id,
                client_id,
                request_id,
                url,
                step,
                status,
                message
            )
            VALUES (%s, %s, %s, NULL, 'ai_generator_request', 'failed', %s)
        """, (
            onboarding_request["user_id"],
            onboarding_request.get("client_id"),
            onboarding_request["request_id"],
            f"AI generator returned status {response.status_code}: {response.text}"
        ))

        conn.commit()

        return jsonify({
            "ok": False,
            "error": f"AI generator returned status {response.status_code}",
            "details": response.text
        }), 500

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()


@auth_bp.route("/api/admin/scrapers/<int:scraper_id>/approve", methods=["POST"])
def approve_scraper(scraper_id):
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 403

    conn = get_db_transaction()
    cur = None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            UPDATE scraper_registry
            SET status = 'approved',
                approved_by = %s,
                approved_at = NOW()
            WHERE id = %s
            RETURNING client_id
        """, (
            session["user_id"],
            scraper_id
        ))

        row = cur.fetchone()

        if not row:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Scraper not found"
            }), 404

        client_id = row["client_id"]

        if not client_id:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Scraper is not attached to a client yet. Approve the user first."
            }), 400

        cur.execute("""
            UPDATE clients c
            SET spiders_to_run = sub.spiders,
                updated_at = NOW()
            FROM (
                SELECT
                    client_id,
                    array_agg(spider_name ORDER BY spider_name) AS spiders
                FROM scraper_registry
                WHERE client_id = %s
                  AND status = 'approved'
                GROUP BY client_id
            ) sub
            WHERE c.id = sub.client_id
        """, (client_id,))

        conn.commit()

        return jsonify({
            "ok": True,
            "message": "Scraper approved and synchronized with clients.spiders_to_run"
        })

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        conn.close()


@auth_bp.route("/api/admin/scrapers", methods=["GET"])
def list_scrapers():
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 403

    client_id = request.args.get("client_id", type=int)
    request_id = request.args.get("request_id", type=int)
    status_filter = request.args.get("status")

    where_clauses = ["1=1"]
    params = []

    if client_id:
        where_clauses.append("sr.client_id = %s")
        params.append(client_id)

    if request_id:
        where_clauses.append("sr.request_id = %s")
        params.append(request_id)
        
    if status_filter:
        where_clauses.append("sr.status = %s")
        params.append(status_filter)

    where_sql = " AND ".join(where_clauses)

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"""
            SELECT
                sr.id,
                sr.client_id,
                sr.request_id,
                c.name AS client_name,
                c.slug AS client_slug,
                sr.store_slug,
                sr.competitor_url,
                sr.competitor_name,
                sr.spider_name,
                sr.spider_module,
                sr.spider_path,
                sr.output_table,
                sr.status,
                sr.last_error,
                sr.generated_at::TEXT,
                sr.approved_at::TEXT,
                (
                    SELECT status 
                    FROM pipeline_task_runs ptr 
                    WHERE ptr.client_id = sr.client_id 
                      AND ptr.task_id IN (sr.spider_name, 'scraper_' || sr.spider_name, 'scrape_' || sr.spider_name)
                    ORDER BY ptr.started_at DESC LIMIT 1
                ) as last_run_status,
                (
                    SELECT started_at::TEXT 
                    FROM pipeline_task_runs ptr 
                    WHERE ptr.client_id = sr.client_id 
                      AND ptr.task_id IN (sr.spider_name, 'scraper_' || sr.spider_name, 'scrape_' || sr.spider_name)
                    ORDER BY ptr.started_at DESC LIMIT 1
                ) as last_run_time,
                COALESCE(c.store_prefix, c.slug) AS client_prefix,
                c.is_active AS client_is_active
            FROM scraper_registry sr
            LEFT JOIN clients c ON c.id = sr.client_id
            WHERE {where_sql}
            ORDER BY sr.generated_at DESC
        """, params)

        scrapers = cur.fetchall()

        cur.close()
        conn.close()

        for scraper in scrapers:
            scraper["run_state"] = spider_run_state(
                scraper["spider_name"],
                scraper.pop("client_prefix"),
                bool(scraper.pop("client_is_active")),
            )

        return jsonify({
            "ok": True,
            "data": scrapers
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/admin/scrapers/<int:scraper_id>/runs", methods=["GET"])
def scraper_runs(scraper_id):
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT client_id, spider_name 
            FROM scraper_registry 
            WHERE id = %s
        """, (scraper_id,))
        scraper = cur.fetchone()

        if not scraper or not scraper['client_id']:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "Scraper not found or not assigned to client"}), 404

        spider_name = scraper['spider_name']
        cur.execute("""
            SELECT 
                id, 
                pipeline_run_id,
                status, 
                started_at::TEXT, 
                finished_at::TEXT, 
                log_excerpt, 
                error_msg
            FROM pipeline_task_runs
            WHERE client_id = %s 
              AND task_id IN (%s, %s, %s)
            ORDER BY started_at DESC
            LIMIT 50
        """, (scraper['client_id'], spider_name, f"scraper_{spider_name}", f"scrape_{spider_name}"))

        runs = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({"ok": True, "runs": runs})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500





# ============================================================
# Subscription
# ============================================================

@auth_bp.route("/api/subscription/change", methods=["POST"])
def api_subscription_change():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json() or {}
    package = data.get("package")
    if not package:
        return jsonify({"ok": False, "error": "Package not provided"}), 400

    conn = get_db_transaction()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s
            WHERE id = %s
        """, (package, user_id))
        
        conn.commit()
        return jsonify({
            "ok": True,
            "message": "Subscription plan changed successfully.",
            "package": package
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@auth_bp.route("/api/subscription/cancel", methods=["POST"])
def api_subscription_cancel():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    conn = get_db_transaction()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or row[0] != "active":
            conn.rollback()
            return jsonify({"ok": False, "error": "Nie można anulować subskrypcji przy obecnym statusie konta"}), 409

        cur.execute("""
            UPDATE users 
            SET status = 'awaiting_payment'
            WHERE id = %s
        """, (user_id,))
        
        cur.execute("""
            UPDATE onboarding_requests 
            SET status = 'awaiting_payment'
            WHERE user_id = %s
        """, (user_id,))

        conn.commit()
        session["status"] = "awaiting_payment"

        return jsonify({
            "ok": True,
            "message": "Subscription cancelled."
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@auth_bp.route("/api/admin/clients/<int:client_id>/matching-thresholds", methods=["POST"])
@admin_required
def api_update_client_matching_thresholds(client_id):
    if not session.get("is_admin"):
        return jsonify({
            "ok": False,
            "error": "Brak uprawnień administratora"
        }), 403

    data = request.get_json(silent=True) or {}

    try:
        match_name_threshold = int(data.get("match_name_threshold", 70))
        match_color_threshold = int(data.get("match_color_threshold", 0))
        match_maker_threshold = int(data.get("match_maker_threshold", 60))
    except Exception:
        return jsonify({
            "ok": False,
            "error": "Progi muszą być liczbami"
        }), 400

    thresholds = [
        match_name_threshold,
        match_color_threshold,
        match_maker_threshold,
    ]

    if any(value < 0 or value > 100 for value in thresholds):
        return jsonify({
            "ok": False,
            "error": "Progi muszą być w zakresie 0-100"
        }), 400

    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            UPDATE clients
            SET
                match_name_threshold = %s,
                match_color_threshold = %s,
                match_maker_threshold = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING
                id,
                name,
                slug,
                match_name_threshold,
                match_color_threshold,
                match_maker_threshold
        """, (
            match_name_threshold,
            match_color_threshold,
            match_maker_threshold,
            client_id,
        ))

        client = cur.fetchone()

        if not client:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "Nie znaleziono klienta"
            }), 404

        conn.commit()

        return jsonify({
            "ok": True,
            "client": dict(client)
        })

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ============================================================
# Admin - error logs
# ============================================================

@auth_bp.route("/api/admin/error_logs", methods=["GET"])
@admin_required
def get_error_logs():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    date_before = request.args.get("date_before", "").strip()
    date_after = request.args.get("date_after", "").strip()
    error_type = request.args.get("error_type", "").strip()
    is_reviewed = request.args.get("is_reviewed", "").strip()

    offset = (page - 1) * per_page

    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        where_clauses = ["1=1"]
        params = []

        if search:
            where_clauses.append("(error_code ILIKE %s OR message ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if category:
            where_clauses.append("category = %s")
            params.append(category)

        if error_type:
            where_clauses.append("error_type = %s")
            params.append(error_type)

        if is_reviewed in ["true", "false"]:
            where_clauses.append("is_reviewed = %s")
            params.append(is_reviewed == "true")

        if date_after:
            where_clauses.append("created_at >= %s")
            params.append(date_after)

        if date_before:
            where_clauses.append("created_at <= %s")
            params.append(date_before + " 23:59:59")

        where_str = " AND ".join(where_clauses)

        cur.execute(
            f"SELECT COUNT(id) as total FROM error_logs WHERE {where_str}",
            params
        )

        total_items = cur.fetchone()["total"]

        query = f"""
            SELECT
                id,
                category,
                error_code,
                error_type,
                is_reviewed,
                message,
                TO_CHAR(created_at, 'DD.MM.YYYY') AS created_at_str,
                TO_CHAR(resolved_at, 'DD.MM.YYYY') AS resolved_at_str
            FROM error_logs
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """

        cur.execute(query, params + [per_page, offset])

        logs = cur.fetchall()

        cur.close()
        conn.close()

        import math

        return jsonify({
            "ok": True,
            "data": logs,
            "pagination": {
                "total_items": total_items,
                "current_page": page,
                "per_page": per_page,
                "total_pages": math.ceil(total_items / per_page) if total_items > 0 else 1
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@auth_bp.route("/api/admin/error_logs/<int:log_id>/review", methods=["POST"])
@admin_required
def review_error_log(log_id):
    data = request.get_json() or {}

    is_reviewed = data.get("is_reviewed")
    resolved_at = data.get("resolved_at")

    try:
        conn = get_db()
        cur = conn.cursor()

        if resolved_at is not None:
            cur.execute("""
                UPDATE error_logs
                SET resolved_at = %s
                WHERE id = %s
            """, (resolved_at, log_id))

        if is_reviewed is not None:
            if is_reviewed:
                cur.execute("""
                    UPDATE error_logs
                    SET is_reviewed = TRUE
                    WHERE id = %s
                """, (log_id,))
            else:
                cur.execute("""
                    UPDATE error_logs
                    SET is_reviewed = FALSE,
                        resolved_at = NULL
                    WHERE id = %s
                """, (log_id,))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Status zaktualizowany"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

# ----------------------------------------------------------------------
# GROQ API USAGE
# ----------------------------------------------------------------------
@auth_bp.route("/api/admin/api-usage", methods=["GET"])
@admin_required
def get_api_usage():
    conn = get_db()
    if not conn:
        return jsonify({"error": "Błąd połączenia z bazą"}), 500
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT key_name, total_tokens, is_exhausted, last_used, exhausted_at
            FROM api_keys_usage
            ORDER BY key_name ASC
        """)
        keys = cur.fetchall()
        
        # Konwersja dat do stringa
        for key in keys:
            if key['last_used']:
                key['last_used'] = key['last_used'].isoformat()
            if key['exhausted_at']:
                key['exhausted_at'] = key['exhausted_at'].isoformat()

        return jsonify({
            "ok": True,
            "keys": keys
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@auth_bp.route("/api/admin/pipeline-clients", methods=["GET"])
@admin_required
def get_pipeline_clients():
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                c.id AS client_id,
                c.name AS client_name,
                c.slug AS client_slug,
                COALESCE(c.store_prefix, c.slug) AS store_prefix,

                COALESCE(
                    ARRAY_LENGTH(c.spiders_to_run, 1),
                    0
                ) AS scrapers_count,

                COALESCE(
                    c.spiders_to_run,
                    ARRAY[]::TEXT[]
                ) AS scrapers,

                last_run.id AS last_pipeline_run_id,
                last_run.status AS last_status,
                last_run.started_at AS last_started_at,
                last_run.finished_at AS last_finished_at,
                last_run.error_msg AS last_error_msg

            FROM clients c

            LEFT JOIN LATERAL (
                SELECT
                    pr.id,
                    pr.status,
                    pr.started_at,
                    pr.finished_at,
                    pr.error_msg
                FROM pipeline_runs pr
                WHERE pr.client_id = c.id
                ORDER BY pr.started_at DESC, pr.id DESC
                LIMIT 1
            ) last_run ON TRUE

            WHERE c.is_active = TRUE
            ORDER BY c.name ASC
        """)

        rows = cur.fetchall()
        result = []

        for row in rows:
            item = dict(row)

            started_at = item.get("last_started_at")
            finished_at = item.get("last_finished_at")

            duration_seconds = None

            if started_at and finished_at:
                duration_seconds = int(
                    (finished_at - started_at).total_seconds()
                )

            item["last_started_at"] = _utc_iso(
                started_at
            )

            item["last_finished_at"] = _utc_iso(
                finished_at
            )

            item["duration_seconds"] = duration_seconds

            prefix = item.pop("store_prefix")
            item["scraper_states"] = [
                {"name": name, "state": spider_run_state(name, prefix)}
                for name in item.get("scrapers") or []
            ]
            item["active_scrapers_count"] = sum(
                1 for scraper in item["scraper_states"] if scraper["state"] == ACTIVE
            )

            schedule_cron = os.environ.get(
                "PIPELINE_SCHEDULE_CRON",
                "0 9 * * *",
            )

            item["schedule_cron"] = schedule_cron
            item["schedule_label"] = _cron_as_polish_time(
                schedule_cron
            )

            result.append(item)

        return jsonify({
            "ok": True,
            "data": result,
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()

@auth_bp.route(
    "/api/admin/clients/<int:client_id>/pipeline-runs",
    methods=["GET"],
)
@admin_required
def get_client_pipeline_runs(client_id):
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                id,
                name
            FROM clients
            WHERE id = %s
            LIMIT 1
        """, (client_id,))

        client = cur.fetchone()

        if not client:
            return jsonify({
                "ok": False,
                "error": "Nie znaleziono klienta.",
            }), 404

        cur.execute("""
            SELECT
                pr.id AS pipeline_run_id,
                pr.status,
                pr.started_at,
                pr.finished_at,
                pr.error_msg,

                COUNT(ptr.id)::INTEGER AS tasks_count,

                COUNT(ptr.id) FILTER (
                    WHERE ptr.status = 'success'
                )::INTEGER AS success_tasks,

                COUNT(ptr.id) FILTER (
                    WHERE ptr.status = 'failed'
                )::INTEGER AS failed_tasks,

                COUNT(ptr.id) FILTER (
                    WHERE ptr.status = 'running'
                )::INTEGER AS running_tasks

            FROM pipeline_runs pr

            LEFT JOIN pipeline_task_runs ptr
                ON ptr.pipeline_run_id = pr.id

            WHERE pr.client_id = %s

            GROUP BY
                pr.id,
                pr.status,
                pr.started_at,
                pr.finished_at,
                pr.error_msg

            ORDER BY
                pr.started_at DESC,
                pr.id DESC

            LIMIT 50
        """, (client_id,))

        rows = cur.fetchall()
        runs = []

        for row in rows:
            item = dict(row)

            started_at = item.get("started_at")
            finished_at = item.get("finished_at")

            duration_seconds = None

            if started_at and finished_at:
                duration_seconds = int(
                    (finished_at - started_at).total_seconds()
                )

            item["started_at"] = _utc_iso(started_at)
            item["finished_at"] = _utc_iso(finished_at)
            item["duration_seconds"] = duration_seconds

            runs.append(item)

        return jsonify({
            "ok": True,
            "client": {
                "id": client["id"],
                "name": client["name"],
            },
            "runs": runs,
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()

@auth_bp.route(
    "/api/admin/pipeline_runs/<int:pipeline_run_id>/tasks",
    methods=["GET"],
)
@admin_required
def get_pipeline_run_tasks(pipeline_run_id):
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                id,
                client_id,
                pipeline_run_id,
                task_id,
                status,
                started_at,
                finished_at,
                COALESCE(log_excerpt, '') AS log_excerpt,
                COALESCE(error_msg, '') AS error_msg
            FROM pipeline_task_runs
            WHERE pipeline_run_id = %s
            ORDER BY
                started_at ASC NULLS LAST,
                id ASC
        """, (pipeline_run_id,))

        rows = cur.fetchall()
        tasks = []

        for row in rows:
            item = dict(row)

            item["started_at"] = _utc_iso(
                item.get("started_at")
            )

            item["finished_at"] = _utc_iso(
                item.get("finished_at")
            )

            tasks.append(item)

        return jsonify({
            "ok": True,
            "pipeline_run_id": pipeline_run_id,
            "tasks": tasks,
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()

@auth_bp.route(
    "/api/admin/pipeline-runs/<int:pipeline_run_id>/delete",
    methods=["POST"],
)
@admin_required
def delete_pipeline_run(pipeline_run_id):
    conn = None
    cur = None

    try:
        conn = get_db_transaction()
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                id,
                status
            FROM pipeline_runs
            WHERE id = %s
            LIMIT 1
        """, (pipeline_run_id,))

        pipeline_run = cur.fetchone()

        if not pipeline_run:
            conn.rollback()

            return jsonify({
                "ok": False,
                "error": "Nie znaleziono przebiegu.",
            }), 404

        if pipeline_run["status"] == "running":
            conn.rollback()

            return jsonify({
                "ok": False,
                "error": (
                    "Nie można usunąć przebiegu, "
                    "który nadal trwa."
                ),
            }), 409

        cur.execute("""
            DELETE FROM pipeline_task_runs
            WHERE pipeline_run_id = %s
        """, (pipeline_run_id,))

        cur.execute("""
            DELETE FROM pipeline_runs
            WHERE id = %s
        """, (pipeline_run_id,))

        conn.commit()

        return jsonify({
            "ok": True,
            "message": "Przebieg został usunięty.",
        })

    except Exception as e:
        if conn:
            conn.rollback()

        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()

