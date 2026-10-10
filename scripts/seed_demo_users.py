"""Create (or update) one fictional demo user per spec §5 role and link them to the seed data.

    python -m scripts.seed_demo_users

- Connects with DATABASE_URL from .env (server-side, bypasses RLS).
- All demo users share DEMO_USER_PASSWORD from .env. If it is missing, a strong random password is
  generated and appended to .env. The password is never printed and never sent to the database:
  only its bcrypt hash is stored (the same format Supabase Auth uses).
- Idempotent: safe to re-run after `supabase db reset --linked`.
"""

from __future__ import annotations

import json
import secrets
import string
import sys
from pathlib import Path

import bcrypt
import psycopg

from app.config import Settings

ENV_FILE = Path(".env")

# (email, full name, job title, roles) — all fictional
DEMO_USERS = [
    ("planner@example.com", "Sarah Chen", "Sr. Production Planner", ["planner"]),
    ("supervisor@example.com", "Diego Alvarez", "Production Supervisor", ["supervisor"]),
    ("stores@example.com", "Priya Nair", "Stores Lead", ["stores"]),
    ("purchasing@example.com", "Tom Becker", "Purchasing Specialist", ["purchasing"]),
    ("engineering@example.com", "Erik Lindqvist", "Chief Engineer", ["engineering"]),
    ("approver@example.com", "Marcus Vance", "Plant Operations Director", ["approver"]),
    ("admin@example.com", "Avery Admin", "System Administrator", ["admin"]),
]

UPSERT_USER = """
with existing as (select id from auth.users where email = %(email)s),
inserted as (
  insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
    raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
    confirmation_token, recovery_token, email_change_token_new, email_change,
    email_change_token_current, phone_change, phone_change_token, reauthentication_token)
  select '00000000-0000-0000-0000-000000000000', gen_random_uuid(), 'authenticated', 'authenticated',
         %(email)s, %(hash)s, now(),
         '{"provider":"email","providers":["email"]}', %(meta)s::jsonb, now(), now(),
         '', '', '', '', '', '', '', ''
  where not exists (select 1 from existing)
  returning id
)
select id from inserted union all select id from existing
"""

UPSERT_IDENTITY = """
insert into auth.identities (id, user_id, provider_id, provider, identity_data, last_sign_in_at, created_at, updated_at)
values (gen_random_uuid(), %(uid)s, %(uid)s::text, 'email',
        jsonb_build_object('sub', %(uid)s::text, 'email', %(email)s::text, 'email_verified', true), now(), now(), now())
on conflict (provider_id, provider) do nothing
"""

LINK_SEED_DATA = """
update public.bom_headers set approved_by = %(engineering)s where status in ('Approved', 'Superseded');
update public.routing_headers set approved_by = %(engineering)s where status = 'Approved';
update public.sales_orders set created_by = %(planner)s where created_by is null;
update public.planning_runs set created_by = %(planner)s where created_by is null;
update public.schedule_versions set created_by = %(planner)s where created_by is null;
update public.schedule_versions set approved_by = %(approver)s where status = 'Approved' and approved_by is null;
update public.approval_requests set requested_by = %(planner)s, approver_id = %(approver)s where request_number = 'APR-1042';
update public.machine_downtime set created_by = %(supervisor)s where created_by is null;
"""


def demo_password() -> str:
    settings = Settings()
    if settings.demo_user_password:
        return settings.demo_user_password.get_secret_value()
    alphabet = string.ascii_letters + string.digits
    password = "Demo-" + "".join(secrets.choice(alphabet) for _ in range(20))
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n# Shared password for the fictional demo users (scripts/seed_demo_users.py)\n"
                f"DEMO_USER_PASSWORD={password}\n")
    print("Generated a new DEMO_USER_PASSWORD and saved it to .env (not printed).")
    return password


def main() -> int:
    settings = Settings()
    if not settings.database_url:
        print("DATABASE_URL is not set in .env — see development-khaled/backend-roadmap.md", file=sys.stderr)
        return 1
    password = demo_password()
    # Supabase Auth (Go bcrypt) uses the $2a$ prefix; the algorithm is identical to $2b$.
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode().replace("$2b$", "$2a$", 1)

    ids: dict[str, str] = {}
    with psycopg.connect(settings.database_url.get_secret_value(), sslmode="require", connect_timeout=20) as conn:
        with conn.cursor() as cur:
            for email, full_name, job_title, roles in DEMO_USERS:
                meta = json.dumps({"full_name": full_name, "job_title": job_title, "fictional": True})
                uid = cur.execute(UPSERT_USER, {"email": email, "hash": hashed, "meta": meta}).fetchone()[0]
                cur.execute("update auth.users set encrypted_password = %s, raw_user_meta_data = %s::jsonb, "
                            "updated_at = now() where id = %s", (hashed, meta, uid))
                cur.execute(UPSERT_IDENTITY, {"uid": uid, "email": email})
                cur.execute("insert into public.profiles (id, full_name, job_title) values (%s, %s, %s) "
                            "on conflict (id) do update set full_name = excluded.full_name, job_title = excluded.job_title",
                            (uid, full_name, job_title))
                cur.execute("delete from public.user_roles where user_id = %s and not (role = any(%s))", (uid, roles))
                for role in roles:
                    cur.execute("insert into public.user_roles (user_id, role) values (%s, %s) on conflict do nothing",
                                (uid, role))
                ids[roles[0]] = uid
                print(f"  {email:<26} {', '.join(roles)}")
            for statement in filter(None, (s.strip() for s in LINK_SEED_DATA.split(";"))):
                cur.execute(statement, ids)
    print(f"{len(DEMO_USERS)} demo users ready. Sign in at /login with the password from .env (DEMO_USER_PASSWORD).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
