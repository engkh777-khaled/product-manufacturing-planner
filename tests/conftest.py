"""Shared test setup.

App tests run with AUTH_MODE=disabled (the fictional demo planner) so they never depend on Supabase.
tests/test_auth.py switches to AUTH_MODE=supabase with a mocked Auth server; tests/db talks to the real DB.
"""

import os

os.environ["APP_ENV"] = "test"
os.environ["AUTH_MODE"] = "disabled"
os.environ["SCHEDULER_WORKER"] = "thread"  # solver jobs run in-process in tests (no worker pool)
os.environ["SCHEDULER_TIME_LIMIT_SECONDS"] = "3"
os.environ["ASSISTANT_PLAN_SECONDS"] = "3"
os.environ["ANTHROPIC_API_KEY"] = ""  # tests never call the real AI service (mocked client in test_assistant_tools)
