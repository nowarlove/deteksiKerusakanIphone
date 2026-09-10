"""Buat atau reset akun admin MySQL dari CLI, bukan dari endpoint publik."""

import argparse
import os
import sys

from werkzeug.security import generate_password_hash

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT_DIR, "app")
for path in (ROOT_DIR, APP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.env_loader import load_env_file
from core.repositories.mysql_repository import MySQLRepository

load_env_file()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--role", choices=("admin", "expert"), default="admin")
    args = parser.parse_args()
    import getpass
    password = getpass.getpass("Password baru: ")
    confirmation = getpass.getpass("Ulangi password: ")
    if password != confirmation or len(password) < 10:
        raise SystemExit("Password harus sama dan minimal 10 karakter.")
    repository = MySQLRepository()
    if not repository.configured:
        raise SystemExit("Konfigurasi MySQL/PyMySQL belum siap.")
    try:
        connection = repository.connect()
        connection.close()
        existing = repository.get_admin_by_username(args.username)
    except Exception as exc:
        code = exc.args[0] if getattr(exc, "args", None) else type(exc).__name__
        raise SystemExit(
            f"MySQL tidak dapat diakses (error {code}). Pastikan user sudah dibuat, "
            "GRANT berhasil, dan MYSQL_PASSWORD di .env sama dengan password user MySQL."
        ) from None
    password_hash = generate_password_hash(password, method="scrypt")
    if existing:
        repository._execute(
            "update admin_users set password_hash=%s,role=%s,is_active=1,failed_login_count=0,locked_until=null where id=%s",
            (password_hash, args.role, existing["id"]),
        )
        print(f"Akun {args.username} diperbarui.")
    else:
        repository.create_admin(args.username, password_hash, args.role)
        print(f"Akun {args.username} dibuat.")


if __name__ == "__main__":
    main()
