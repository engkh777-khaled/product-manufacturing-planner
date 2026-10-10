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


@pytest.fixture
def user_id(db):
    """Look up a demo user's id (skips when the demo users have not been created)."""
    def _uid(email: str) -> str:
        db.execute("select id from auth.users where email = %s", (email,))
        row = db.fetchone()
        if row is None:
            pytest.skip("demo users missing — run: python -m scripts.seed_demo_users")
        return str(row[0])
    return _uid


@pytest.fixture
def act_as(db, user_id):
    """Switch the test transaction to the `authenticated` role with a demo user's JWT claims."""
    import json

    def _act(email: str | None = None, sub: str | None = None) -> None:
        db.execute("reset role")
        claims = json.dumps({"sub": sub or user_id(email), "role": "authenticated", "email": email or "nobody@example.com"})
        db.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        db.execute("set local role authenticated")
    return _act
