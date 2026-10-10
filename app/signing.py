"""HMAC signatures for schedule validation results (M12, D-036).

The application server runs the independent validator; the database only trusts a `validation_passed` claim when
the exact payload text is signed with the server's key (also stored in `private.app_config`). Clients never see
the key, so a planner calling the RPC directly cannot mark a schedule as validated.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.config import get_settings


def signed_payload(body: dict, key: str | None = None) -> tuple[str, str | None]:
    """(payload text, hex signature or None when no key is configured)."""
    text = json.dumps(body, separators=(",", ":"), ensure_ascii=False, default=str)
    if key is None:
        secret = get_settings().schedule_signing_key
        key = secret.get_secret_value() if secret else None
    if not key:
        return text, None
    return text, hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()
