"""Check that the AI planning assistant can reach its model (M11).

    python -m scripts.check_assistant

Reads ANTHROPIC_API_KEY and ASSISTANT_MODEL from .env, sends one tiny request (a few tokens) and explains any
problem in plain words. The key is never printed.
"""

from __future__ import annotations

import sys

from app.config import Settings

HINTS = {
    "AuthenticationError": "the API key was rejected — copy it again from console.anthropic.com (Settings → API keys)",
    "PermissionDeniedError": "the key is not allowed to use this model or workspace",
    "NotFoundError": "unknown model name — check ASSISTANT_MODEL in .env",
    "RateLimitError": "rate limit reached — wait a minute and try again",
    "APIConnectionError": "cannot reach api.anthropic.com — check the internet connection / proxy",
    "APITimeoutError": "the request timed out — try again",
    "InternalServerError": "the AI service had an error — try again shortly",
}


def check(settings: Settings, client=None) -> tuple[bool, str]:
    if not settings.assistant_enabled:
        return False, "ANTHROPIC_API_KEY is not set in .env — the assistant uses scripted demo replies."
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), timeout=30)
        reply = client.messages.create(model=settings.assistant_model, max_tokens=10,
                                       messages=[{"role": "user", "content": "Reply with the word OK."}])
    except Exception as exc:  # explain, never print the key
        name = type(exc).__name__
        detail = getattr(exc, "message", "") or str(exc)
        if "credit balance" in detail.lower():
            return False, "the account has no credit — add credit / billing in console.anthropic.com"
        return False, f"{HINTS.get(name, 'the request failed')} ({name})"
    text = "".join(getattr(b, "text", "") for b in reply.content).strip()
    return True, f"AI connection OK — model {settings.assistant_model} answered {text!r}."


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    ok, message = check(settings)
    print(message)
    if ok and settings.data_backend != "supabase":
        print("Note: DATA_BACKEND is not 'supabase' — the assistant's planning tools will only see demo data.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
