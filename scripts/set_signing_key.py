"""Create the schedule-validation signing key (M12, D-036) and store it in .env and in private.app_config.

    python -m scripts.set_signing_key            # reuse SCHEDULE_SIGNING_KEY from .env, or create one
    python -m scripts.set_signing_key --rotate   # create a new key (existing validated drafts stay as they are)

The key is never printed. Needs DATABASE_URL (owner connection) in .env.
"""

from __future__ import annotations

import argparse
import pathlib
import secrets

import psycopg

from app.config import Settings

ENV = pathlib.Path(".env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    key = None if args.rotate or not settings.schedule_signing_key else settings.schedule_signing_key.get_secret_value()
    if not key:
        key = secrets.token_hex(32)
        lines = [l for l in ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("SCHEDULE_SIGNING_KEY=")]
        lines.append(f"SCHEDULE_SIGNING_KEY={key}")
        ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("New signing key written to .env")
    with psycopg.connect(settings.database_url.get_secret_value(), sslmode="require") as conn:
        conn.execute("""insert into private.app_config (key, value) values ('schedule_signing_key', %s)
                        on conflict (key) do update set value = excluded.value, updated_at = now()""", (key,))
    print("Signing key stored in private.app_config (value not shown). Restart the app to load it.")


if __name__ == "__main__":
    main()
