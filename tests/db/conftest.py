"""Database tests run against the Supabase project in DATABASE_URL (from .env).

They are skipped when DATABASE_URL is not set (e.g. in CI). Every test runs inside a transaction
that is rolled back, so the dev database is never modified.
"""

import pytest

psycopg = pytest.importorskip("psycopg")

from app.config import Settings  # noqa: E402

_settings = Settings()
DSN = _settings.database_url.get_secret_value() if _settings.database_url else None


@pytest.fixture(scope="session")
def db_conn():
    if not DSN:
        pytest.skip("DATABASE_URL not set — see development-khaled/backend-roadmap.md (M04)")
    try:
        conn = psycopg.connect(DSN, autocommit=False, connect_timeout=20, sslmode="require")
    except psycopg.OperationalError as exc:
        # Report only the server's first message line — never the connection string (it holds the password).
        pytest.fail(f"Cannot connect to DATABASE_URL: {str(exc).splitlines()[0]}", pytrace=False)
    with conn:
        yield conn


@pytest.fixture
def db(db_conn):
    """A cursor inside a transaction that is always rolled back."""
    with db_conn.cursor() as cur:
        try:
            yield cur
        finally:
            db_conn.rollback()

