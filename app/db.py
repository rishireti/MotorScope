"""SQLite storage layer for users, sessions, and single-use tokens."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

USERS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name      TEXT    NOT NULL,
    password_hash     TEXT,
    email_verified    INTEGER NOT NULL DEFAULT 0,
    auth_provider     TEXT    NOT NULL DEFAULT 'password',
    google_subject    TEXT    UNIQUE,
    google_linked_at  TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    last_login_at     TEXT
)
"""

USER_COLUMNS: tuple[str, ...] = (
    "id",
    "email",
    "display_name",
    "password_hash",
    "email_verified",
    "auth_provider",
    "google_subject",
    "google_linked_at",
    "created_at",
    "updated_at",
    "last_login_at",
)

SCHEMA = f"""
{USERS_TABLE.format(name="users")};

CREATE TABLE IF NOT EXISTS auth_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    purpose      TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   INTEGER NOT NULL,
    used_at      TEXT
);

CREATE TABLE IF NOT EXISTS login_attempts (
    identifier   TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mobility_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recorded_on  TEXT    NOT NULL,
    walking_speed           REAL,
    step_length             REAL,
    walking_asymmetry       REAL,
    double_support_time     REAL,
    cardio_fitness          REAL,
    ground_contact_time     REAL,
    running_stride_length   REAL,
    six_minute_walk         REAL,
    stair_speed_up          REAL,
    stair_speed_down        REAL,
    vertical_oscillation    REAL,
    walking_steadiness      REAL,
    score                   REAL,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id, purpose);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_expiry ON auth_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_mobility_user ON mobility_readings(user_id, recorded_on DESC);
"""

# Columns added after the first release. Existing local databases keep working.
MIGRATIONS = (
    ("users", "auth_provider", "TEXT NOT NULL DEFAULT 'password'"),
    ("users", "google_subject", "TEXT"),
    ("users", "google_linked_at", "TEXT"),
)

PURPOSE_VERIFY = "email_verification"
PURPOSE_RESET = "password_reset"

# Columns of mobility_readings that hold a metric reading, in display order.
MOBILITY_COLUMNS: tuple[str, ...] = (
    "walking_speed",
    "step_length",
    "walking_asymmetry",
    "double_support_time",
    "cardio_fitness",
    "ground_contact_time",
    "running_stride_length",
    "six_minute_walk",
    "stair_speed_up",
    "stair_speed_down",
    "vertical_oscillation",
    "walking_steadiness",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_date() -> str:
    """Today's date in the user's own timezone.

    Server bookkeeping uses UTC, but a date input for a measurement should default to
    the calendar day the user is actually on, which can be a day ahead or behind UTC.
    """
    return datetime.now().date().isoformat()


def timestamp() -> str:
    return utcnow().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = settings.database_path
    if path.is_dir():
        raise RuntimeError(
            f"DATABASE_PATH points at a folder, not a file: {path}. "
            "Clear the DATABASE_PATH line in .env to use the default data/motorscope.db."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Could not open the MotorScope database at {path}: {exc}. "
            "Check that DATABASE_PATH in .env points somewhere writable."
        ) from exc
    return connection


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(SCHEMA)
        _apply_migrations(connection)


def _apply_migrations(connection: sqlite3.Connection) -> None:
    """Add columns missing from databases created before they existed."""
    for table, column, definition in MIGRATIONS:
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    _relax_password_hash(connection)


def _relax_password_hash(connection: sqlite3.Connection) -> bool:
    """Make `users.password_hash` nullable on databases that predate Google sign-in.

    Those databases were created with `password_hash TEXT NOT NULL`, and SQLite cannot
    relax a NOT NULL constraint with ALTER TABLE. Google-only accounts have no password
    at all, so the table is rebuilt and the existing rows copied across.
    """
    info = {row["name"]: row for row in connection.execute("PRAGMA table_info(users)")}
    if not info:
        return False

    password_hash = info.get("password_hash")
    if password_hash is None or not password_hash["notnull"]:
        return False

    shared = [name for name in USER_COLUMNS if name in info]
    column_list = ", ".join(shared)

    # PRAGMA foreign_keys is a no-op inside a transaction, and the DROP below needs it
    # off so the auth_tokens rows that point at users survive the rebuild.
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute(USERS_TABLE.format(name="users_rebuild"))
        connection.execute(
            f"INSERT INTO users_rebuild ({column_list}) SELECT {column_list} FROM users"
        )
        connection.execute("DROP TABLE users")
        connection.execute("ALTER TABLE users_rebuild RENAME TO users")
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.commit()

    return True


def normalize_email(email: str) -> str:
    return email.strip().lower()


# --------------------------------------------------------------------------- users


def create_user(
    email: str,
    display_name: str,
    password_hash: str | None,
    *,
    email_verified: bool = False,
    auth_provider: str = "password",
    google_subject: str | None = None,
) -> int | None:
    """Insert a user. Returns None when the email is already registered."""
    normalized = normalize_email(email)
    now = timestamp()
    with get_connection() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (email, display_name, password_hash, email_verified,
                                   auth_provider, google_subject, google_linked_at,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    display_name.strip(),
                    password_hash,
                    int(email_verified),
                    auth_provider,
                    google_subject,
                    now if google_subject else None,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cursor.lastrowid)


def get_user_by_google_subject(subject: str) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM users WHERE google_subject = ?", (subject,)
        ).fetchone()


def link_google_account(user_id: int, subject: str, display_name: str) -> bool:
    """Attach Google to a local account. Returns False if the subject is taken."""
    with get_connection() as connection:
        try:
            connection.execute(
                """
                UPDATE users
                   SET google_subject = ?, google_linked_at = ?,
                       auth_provider = 'google', updated_at = ?
                 WHERE id = ?
                """,
                (subject, timestamp(), timestamp(), user_id),
            )
        except sqlite3.IntegrityError:
            return False
        return True


def unlink_google_account(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
               SET google_subject = NULL, google_linked_at = NULL,
                   auth_provider = 'password', updated_at = ?
             WHERE id = ?
            """,
            (timestamp(), user_id),
        )


def has_password(user_id: int) -> bool:
    row = get_user_by_id(user_id)
    return bool(row is not None and row["password_hash"])


def get_user_by_email(email: str) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (normalize_email(email),),
        ).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def mark_email_verified(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
               SET email_verified = 1, updated_at = ?
             WHERE id = ?
            """,
            (timestamp(), user_id),
        )


def update_password(user_id: int, password_hash: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
               SET password_hash = ?, updated_at = ?
             WHERE id = ?
            """,
            (password_hash, timestamp(), user_id),
        )


def record_login(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (timestamp(), user_id),
        )


def count_users() -> int:
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return int(row["total"])


# --------------------------------------------------------------------------- tokens


def store_token(user_id: int, token_hash: str, purpose: str, ttl_seconds: int) -> None:
    """Persist a token digest, invalidating any older unused token of the same kind."""
    now = utcnow()
    expires_at = int(now.timestamp()) + ttl_seconds
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE auth_tokens
               SET used_at = ?
             WHERE user_id = ? AND purpose = ? AND used_at IS NULL
            """,
            (timestamp(), user_id, purpose),
        )
        connection.execute(
            """
            INSERT INTO auth_tokens (user_id, token_hash, purpose, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, token_hash, purpose, now.isoformat(timespec="seconds"), expires_at),
        )


def consume_token(token: str, purpose: str) -> sqlite3.Row | None:
    """Validate a single-use token and mark it used in one atomic step."""
    now = utcnow()
    with get_connection() as connection:
        try:
            row = connection.execute(
                """
                SELECT * FROM auth_tokens
                 WHERE token_hash = ? AND purpose = ? AND used_at IS NULL
                """,
                (token, purpose),
            ).fetchone()
        except sqlite3.OperationalError:
            return None

        if row is None:
            return None
        if int(row["expires_at"]) < int(now.timestamp()):
            return None

        cursor = connection.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (now.isoformat(timespec="seconds"), int(row["id"])),
        )
        if cursor.rowcount != 1:
            return None
        return row


def purge_expired_tokens() -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM auth_tokens WHERE expires_at < ?",
            (int(utcnow().timestamp()) - 86_400,),
        )
        return cursor.rowcount


# ------------------------------------------------------------------ login throttling


def login_lock_state(identifier: str) -> tuple[int, int]:
    """Return (consecutive_failures, locked_until_epoch)."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT failures, locked_until FROM login_attempts WHERE identifier = ?",
            (identifier.lower(),),
        ).fetchone()
    if row is None:
        return 0, 0
    return int(row["failures"]), int(row["locked_until"])


def record_login_failure(identifier: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO login_attempts (identifier, failures, locked_until)
            VALUES (?, 1, 0)
            ON CONFLICT(identifier) DO UPDATE SET failures = failures + 1
            """,
            (identifier.lower(),),
        )


def apply_lockout(identifier: str) -> None:
    locked_until = int(utcnow().timestamp()) + settings.lockout_seconds
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO login_attempts (identifier, failures, locked_until)
            VALUES (?, ?, ?)
            ON CONFLICT(identifier) DO UPDATE
                SET locked_until = MAX(login_attempts.locked_until, excluded.locked_until)
            """,
            (identifier.lower(), settings.max_login_attempts, locked_until),
        )


def clear_login_failures(identifier: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE identifier = ?", (identifier.lower(),)
        )


def save_mobility_reading(
    user_id: int, recorded_on: str, values: dict[str, float], score: float | None
) -> int:
    """Store one set of metric readings and its composite score."""
    columns = ", ".join(MOBILITY_COLUMNS)
    placeholders = ", ".join("?" for _ in MOBILITY_COLUMNS)

    sql = (
        f"INSERT INTO mobility_readings (user_id, {columns}, score, recorded_on, created_at) "
        f"VALUES (?, {placeholders}, ?, ?, ?)"
    )
    with get_connection() as connection:
        cursor = connection.execute(
            sql,
            (
                user_id,
                *(values.get(name) for name in MOBILITY_COLUMNS),
                score,
                recorded_on,
                timestamp(),
            ),
        )
    return int(cursor.lastrowid or 0)


def get_mobility_readings(user_id: int, limit: int = 30) -> list[sqlite3.Row]:
    """Most recent readings for a user, newest first."""
    with get_connection() as connection:
        return list(
            connection.execute(
                "SELECT * FROM mobility_readings WHERE user_id = ? "
                "ORDER BY recorded_on DESC, id DESC LIMIT ?",
                (user_id, limit),
            )
        )


def get_latest_mobility_reading(user_id: int) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM mobility_readings WHERE user_id = ? "
            "ORDER BY recorded_on DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
