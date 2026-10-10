"""Which dataset the current request reads (M06).

The repository reads every list/dict through `current()`. By default that is the in-memory fictional
`mock_data` module. With DATA_BACKEND=supabase the request middleware installs a dataset built from
`public.planning_snapshot()` for the signed-in user (RLS-filtered), so both backends share all page logic.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.data import mock_data

_current: ContextVar[Any] = ContextVar("dataset", default=None)
_access_token: ContextVar[str | None] = ContextVar("access_token", default=None)


def current():
    """The dataset for this request (mock_data module or a Supabase-built namespace)."""
    return _current.get() or mock_data


def use(dataset, access_token: str | None = None):
    """Install a dataset (and the user's token for writes). Returns reset tokens."""
    return _current.set(dataset), _access_token.set(access_token)


def reset(tokens) -> None:
    ds_token, at_token = tokens
    _current.reset(ds_token)
    _access_token.reset(at_token)


def access_token() -> str | None:
    return _access_token.get()


def is_supabase() -> bool:
    return getattr(current(), "SOURCE", "mock") == "supabase"
