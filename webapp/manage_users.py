import getpass
import os
import sys

from utils import get_db, hash_password, verify_password

MIN_LENGTH = 12


SHARED_ACCOUNTS = {
    "admin": "ADMIN_PASSWORD",
    "nasz_klient": "CLIENT_PASSWORD",
}


def sync_shared_passwords_from_env():
    try:
        conn = get_db()
        cur = conn.cursor()
    except Exception as e:
        print(f"Nie udało się zsynchronizować haseł wspólnych kont: {e}")
        return

    for username, env_name in SHARED_ACCOUNTS.items():
        password = os.environ.get(env_name, "")
        if not password:
            continue

        if len(password) < MIN_LENGTH:
            print(f"{env_name} ma mniej niż {MIN_LENGTH} znaków — pomijam konto '{username}'.")
            continue

        cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
        row = cur.fetchone()

        if row and not verify_password(row[0], password)[0]:
            cur.execute(
                "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE username = %s",
                (hash_password(password), username),
            )
            print(f"Hasło '{username}' zsynchronizowane z {env_name}.")

    cur.close()
    conn.close()


def set_password(username: str) -> int:
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    if cur.fetchone() is None:
        print(f"Nie ma użytkownika '{username}'.")
        return 1

    password = getpass.getpass(f"Nowe hasło dla '{username}': ")
    if len(password) < MIN_LENGTH:
        print(f"Hasło musi mieć co najmniej {MIN_LENGTH} znaków.")
        return 1

    if getpass.getpass("Powtórz hasło: ") != password:
        print("Hasła się nie zgadzają.")
        return 1

    cur.execute(
        "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE username = %s",
        (hash_password(password), username),
    )
    cur.close()
    conn.close()

    print(f"Hasło dla '{username}' zostało zmienione.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "set-password":
        print("Użycie: python manage_users.py set-password <nazwa_uzytkownika>")
        sys.exit(2)

    sys.exit(set_password(sys.argv[2]))
