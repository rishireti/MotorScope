"""Verifies an existing pre-Google database gains the new columns without data loss.

Usage:  .venv\\Scripts\\python.exe -m tests.test_migration
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "motorscope_migration_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ["SECRET_KEY"] = "migration-test-key"
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import db  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' -> ' + extra) if extra and not condition else ''}")
    if not condition:
        failures.append(label)


LEGACY_SCHEMA = """
CREATE TABLE users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name      TEXT    NOT NULL,
    password_hash     TEXT    NOT NULL,
    email_verified    INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    last_login_at     TEXT
);
CREATE TABLE auth_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    purpose      TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   INTEGER NOT NULL,
    used_at      TEXT
);
CREATE TABLE login_attempts (
    identifier   TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER NOT NULL DEFAULT 0
);
"""


def main() -> int:
    print("\nBuilding a database in the pre-Google format")
    connection = sqlite3.connect(TEMP_DB)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        """
        INSERT INTO users (email, display_name, password_hash, email_verified,
                           created_at, updated_at, last_login_at)
        VALUES ('legacy@example.com', 'Legacy User', 'pbkdf2_sha256$1$aa$bb', 1,
                '2026-01-01T00:00:00', '2026-01-01T00:00:00', '2026-01-02T09:30:00')
        """
    )
    connection.commit()
    connection.close()
    check("legacy user written", True)

    print("\nRunning the app's init against it")
    db.init_db()
    probe = sqlite3.connect(TEMP_DB)
    columns = {row[1] for row in probe.execute("PRAGMA table_info(users)")}
    probe.close()
    check("auth_provider added", "auth_provider" in columns)
    check("google_subject added", "google_subject" in columns)
    check("google_linked_at added", "google_linked_at" in columns)

    print("\nExisting data survived")
    user = db.get_user_by_email("legacy@example.com")
    check("user still readable", user is not None)
    check("email intact", user["email"] == "legacy@example.com")
    check("password hash intact", user["password_hash"] == "pbkdf2_sha256$1$aa$bb")
    check("verified flag intact", user["email_verified"] == 1)
    check("provider defaulted", user["auth_provider"] == "password")
    check("google subject empty", user["google_subject"] is None)
    check("last login intact", user["last_login_at"] == "2026-01-02T09:30:00")

    print("\nMigration is idempotent")
    db.init_db()
    db.init_db()
    count = sqlite3.connect(TEMP_DB).execute("SELECT COUNT(*) FROM users").fetchone()[0]
    check("still one user after re-running", count == 1)

    print("\nExisting account can be linked to Google")
    from app import google_auth  # noqa: E402

    profile = google_auth.GoogleProfile(
        subject="legacy-google-sub",
        email="legacy@example.com",
        display_name="Legacy User",
        email_verified=True,
    )
    from app.routers.auth import _resolve_google_user  # noqa: E402

    row, created, problem = _resolve_google_user(profile)
    check("no problem reported", problem is None)
    check("not treated as new", created is False)
    check("linked to existing row", row["id"] == user["id"])
    check("subject stored", db.get_user_by_email("legacy@example.com")["google_subject"] == "legacy-google-sub")
    check("password untouched", db.get_user_by_email("legacy@example.com")["password_hash"] == "pbkdf2_sha256$1$aa$bb")

    print("\nGoogle-only account works on the legacy schema")
    probe = sqlite3.connect(TEMP_DB)
    password_notnull = [
        row for row in probe.execute("PRAGMA table_info(users)") if row[1] == "password_hash"
    ][0][3]
    probe.close()
    check("password_hash is now nullable", password_notnull == 0)

    google_only = google_auth.GoogleProfile(
        subject="brand-new-google-sub",
        email="googleonly@example.com",
        display_name="Google Only",
        email_verified=True,
    )
    row, created, problem = _resolve_google_user(google_only)
    check("passwordless account created", created is True and row is not None)
    check("no problem reported", problem is None)
    check("password is null", row["password_hash"] is None)
    check("provider is google", row["auth_provider"] == "google")
    check("email recorded", row["email"] == "googleonly@example.com")

    print("\nMobility table created for legacy databases too")
    probe = sqlite3.connect(TEMP_DB)
    columns = {row[1] for row in probe.execute("PRAGMA table_info(mobility_readings)")}
    probe.close()
    check("mobility_readings exists", "walking_speed" in columns and "score" in columns)

    print("\nLegacy schema is left consistent")
    probe = sqlite3.connect(TEMP_DB)
    orphans = probe.execute(
        "SELECT COUNT(*) FROM auth_tokens WHERE user_id NOT IN (SELECT id FROM users)"
    ).fetchone()[0]
    probe.close()
    check("no orphaned auth tokens", orphans == 0)

    new_id = db.save_mobility_reading(user["id"], "2026-05-01", {"walking_speed": 1.2}, 55.0)
    check("legacy user can save a reading", new_id > 0)

    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("Migration checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
