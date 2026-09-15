"""
Database layer — SQLite for local development, PostgreSQL in production.

Why both: the free hosting tier that keeps patient data in the EU has an
*ephemeral* filesystem (the container is wiped every time it sleeps), so a
local SQLite file cannot survive there. Production therefore points at a
managed EU Postgres via DATABASE_URL, while `./start.sh` and the test suite
keep using a plain SQLite file with zero setup.

To avoid maintaining two dialects, this module adapts psycopg to the *sqlite3*
API that app.py is already written against:

    * ``conn.execute(sql, params)`` returning a cursor
    * ``?`` placeholders
    * rows addressable by index, slice AND column name (like ``sqlite3.Row``)

so every SQL statement in app.py is byte-identical on both engines.

Selection: DATABASE_URL set to a postgres:// URL -> PostgreSQL, else SQLite
at DB_PATH.
"""

import os
import re
import sqlite3

DB_PATH      = os.environ.get("DB_PATH", "stepcounter.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES  = DATABASE_URL.startswith(("postgres://", "postgresql://"))


# ----------------------------------------------------------------------------
# Row: sqlite3.Row-compatible row for psycopg
# ----------------------------------------------------------------------------
class Row(tuple):
    """A psycopg row that behaves like ``sqlite3.Row``.

    app.py reads rows three different ways — ``r[0]`` (get_settings),
    ``r[1:]`` (get_all_patient_configs) and ``r["device_id"]`` (evaluate) — so
    plain tuples and dicts both break. Subclassing tuple keeps index/slice
    access free and adds name lookup on top.
    """

    def __new__(cls, values, columns):
        row = super().__new__(cls, values)
        row._columns = columns
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._columns.index(key))
        return tuple.__getitem__(self, key)      # int or slice

    def keys(self):
        return list(self._columns)


def _row_factory(cursor):
    """psycopg row-factory protocol: cursor -> (values -> Row)."""
    columns = [d.name for d in (cursor.description or [])]

    def make_row(values):
        return Row(values, columns)

    return make_row


# ----------------------------------------------------------------------------
# SQL dialect translation
# ----------------------------------------------------------------------------
def _to_postgres(sql):
    """Rewrite sqlite3-style ``?`` placeholders to psycopg's ``%s``.

    Safe here because no statement in app.py contains a literal '?' or '%'
    (a literal '%' would need doubling for psycopg). Keep it that way — put
    wildcards in the *parameter*, e.g. execute("... LIKE ?", (f"%{x}%",)).
    """
    return sql.replace("?", "%s")


def _ddl_to_postgres(ddl):
    """Translate the SQLite schema below into PostgreSQL."""
    ddl = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                 "BIGSERIAL PRIMARY KEY", ddl, flags=re.I)
    ddl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", ddl, flags=re.I)
    return ddl


# ----------------------------------------------------------------------------
# Connection wrapper
# ----------------------------------------------------------------------------
class _PostgresConnection:
    """Exposes the subset of the sqlite3 Connection API that app.py uses."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        # `params or None` matters: psycopg only scans for '%' placeholders when
        # parameters are supplied, so passing None for a parameterless statement
        # keeps a literal '%' (e.g. in a LIKE pattern) from being misread.
        cursor.execute(_to_postgres(sql), params or None)
        return cursor

    def executescript(self, sql):
        # psycopg runs a multi-statement string in one go when no parameters
        # are bound, which is what the schema DDL needs.
        self._conn.cursor().execute(sql)
        return self

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def connect():
    """Open a connection to whichever engine is configured."""
    if IS_POSTGRES:
        import psycopg                      # imported lazily: not needed for local SQLite
        return _PostgresConnection(
            psycopg.connect(DATABASE_URL, row_factory=_row_factory, connect_timeout=15,
                            # psycopg auto-prepares a statement after 5 uses, which a
                            # transaction-mode pooler (Neon's "-pooler" endpoint,
                            # PgBouncer) can reject. Our query volume gains nothing
                            # from prepared statements, so disable them and let either
                            # the direct or the pooled connection string work.
                            prepare_threshold=None)
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
# Written in SQLite dialect; _ddl_to_postgres() adapts it. ON CONFLICT is used
# rather than INSERT OR IGNORE because both engines understand it.
SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT    NOT NULL,
    captured_at BIGINT  NOT NULL,
    total_steps INTEGER NOT NULL,
    minute_log  TEXT    NOT NULL,
    received_at BIGINT  NOT NULL
);
-- The (device_id, captured_at) index is created in init_schema() instead of
-- here, because it must be UNIQUE and existing rows have to be deduplicated
-- before the constraint can be applied.

CREATE TABLE IF NOT EXISTS alert_state (
    device_id   TEXT   NOT NULL,
    kind        TEXT   NOT NULL,
    notified_at BIGINT NOT NULL,
    PRIMARY KEY (device_id, kind)
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     BIGINT NOT NULL,
    actor  TEXT   NOT NULL,
    action TEXT   NOT NULL,
    target TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS patient_config (
    device_id   TEXT PRIMARY KEY,
    min_steps   INTEGER NOT NULL DEFAULT 1000,
    min_cadence REAL    NOT NULL DEFAULT 0,
    min_minutes INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_alias (
    device_id TEXT PRIMARY KEY,
    label     TEXT NOT NULL
);
"""

# Older deployments had a patient_config with only min_steps.
_MIGRATIONS = [("min_cadence", "REAL NOT NULL DEFAULT 0"),
               ("min_minutes", "INTEGER NOT NULL DEFAULT 0")]

# Enforces one reading per device per capture time, making ingest idempotent.
UNIQUE_READING_INDEX = "uq_readings_device_capture"


def _index_exists(conn, name):
    if IS_POSTGRES:
        row = conn.execute("SELECT 1 FROM pg_class WHERE relkind = 'i'"
                           " AND relname = ?", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index'"
                           " AND name = ?", (name,)).fetchone()
    return row is not None


def init_schema(seed_settings=None):
    """Create tables if absent, apply migrations, seed default settings."""
    conn = connect()
    try:
        if IS_POSTGRES:
            conn.executescript(_ddl_to_postgres(SCHEMA))
            for column, decl in _MIGRATIONS:
                conn.execute("ALTER TABLE patient_config ADD COLUMN IF NOT EXISTS "
                             f"{column} {_ddl_to_postgres(decl)}")
        else:
            conn.executescript(SCHEMA)
            existing = {r[1] for r in
                        conn.execute("PRAGMA table_info(patient_config)").fetchall()}
            for column, decl in _MIGRATIONS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE patient_config ADD COLUMN {column} {decl}")

        # One reading per device per capture time. Without this a client that
        # retried after a timeout -- the server having already committed the
        # row -- stored the day twice, which showed the patient twice on the
        # dashboard and drew a phantom bar on their chart.
        #
        # Guarded so it runs once rather than on every boot: CREATE/DROP INDEX
        # take an exclusive lock on the table, which would otherwise wait
        # behind any in-flight read every time a worker starts.
        if not _index_exists(conn, UNIQUE_READING_INDEX):
            # Deduplicate first (keeping the earliest row) or the constraint
            # cannot be applied to a database that already has duplicates.
            conn.execute("DELETE FROM readings WHERE id NOT IN"
                         " (SELECT MIN(id) FROM readings GROUP BY device_id, captured_at)")
            conn.execute(f"CREATE UNIQUE INDEX {UNIQUE_READING_INDEX}"
                         " ON readings (device_id, captured_at)")
            # Superseded by the unique index, which serves the same lookups.
            conn.execute("DROP INDEX IF EXISTS idx_readings_device")

        for key, value in (seed_settings or {}).items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                         " ON CONFLICT(key) DO NOTHING", (key, value))
        conn.commit()
    finally:
        conn.close()
