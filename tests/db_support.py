"""Lets the weather tests run on either backend (backend.db.DATABASE_BACKEND).

SQLite: every test has its own temporary file, as before. PostgreSQL: all
tests share the database at DATABASE_URL, so the weather tables are emptied
before each test; that is only done on a local server, never on Neon.
"""
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from backend import db

POSTGRES = db.DATABASE_BACKEND == "postgres"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# For tests of the SQLite file itself (its schema, migrations, query plans).
sqlite_only = unittest.skipIf(POSTGRES, "tests the SQLite file schema")


def execute_weather(db_path: Path, sql: str, params: tuple = ()) -> None:
    """Run one write statement against the weather tables and commit it."""
    conn = db._weather_connection(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def fresh_weather_db(db_path: Path) -> None:
    """init_db(), with no weather rows left from an earlier test."""
    db.init_db(db_path)
    if POSTGRES:
        host = urlsplit(db.DATABASE_URL).hostname
        if host not in LOCAL_HOSTS:
            raise RuntimeError(f"Tests only empty a local PostgreSQL database, not one on {host!r}")
        execute_weather(db_path, "TRUNCATE WeatherObservation, Station RESTART IDENTITY")
