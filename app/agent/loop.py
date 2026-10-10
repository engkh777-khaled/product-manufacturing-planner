"""Tool-calling loop for the AI planning assistant (spec §11, M11).

The model never sees the database or the API key's owner: it only gets the system rules (spec §11.3), the
conversation context (user, roles, selected order and version, planning date) and the tool schemas. Every tool
runs server-side as the signed-in user (see tools.run_tool). Conversations are kept in memory per user.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from markupsafe import Markup

from app.agent.tools import TOOLS, ToolContext, run_tool
from app.config import get_settings
from app.data import repository as repo
from app.data.dataset import current as ds

log = logging.getLogger("app.agent")
MAX_RESULT_CHARS = 20_000
MAX_TURNS_KEPT = 12

RULES = """You are an AI production planning assistant for a valve manufacturing and assembly factory.

Your role is to help planners understand orders, materials, capacity, scheduling results, and delivery risks.

Rules:
1. Never invent inventory quantities, operation durations, supplier dates, machine capacity, or production status.
2. Use authorized application tools for authoritative calculations.
3. Identify missing, stale, or inconsistent data.
4. Never schedule an operation before required materials and predecessor operations are ready.
5. Never assign operations beyond resource capacity or outside permitted calendars.
6. Distinguish verified facts, planning assumptions, and recommendations.
7. Explain delivery risks using the actual constraints found.
8. Use simulation tools for proposed changes.
9. Never place a purchase order or release production automatically in the MVP.
10. Never approve your own plan.
11. Do not commit a delivery date to a customer.
12. Obtain explicit human authorization before consequential changes.
13. If the planning solver cannot find a solution, report the solver outcome accurately.
14. Prefer traceable, explainable recommendations over unsupported claims."""

STYLE = """How to work in this application:
- Every number, date and status in your answer must come from a tool result in this conversation. If a tool did not
  give it, say you do not know or call the tool. Quote tool names you relied on.
- Start with the direct answer in one or two sentences, then sections "Verified facts", "Assumptions" (only if any)
  and "Recommendations". Keep it concise; use short bullet lists.
- Saving a plan or submitting it for approval: call save_proposed_plan / submit_plan_for_approval. They only create
  a confirmation card — say clearly that nothing happens until the user presses Confirm.
- You cannot approve plans, release production, place or change purchase orders, or promise dates to customers.
  If asked, refuse briefly and point to the human role that can (approver, purchasing, sales).
- If a tool says the user is not authorized, tell the user which role is needed; do not try to work around it.
- Dates are plant-local (America/Chicago). All data is fictional demo data."""


def system_prompt(ctx: ToolContext) -> str:
    horizon = ds().PLANNING_HORIZON
    base = repo.baseline()
    context = [
        f"Signed-in user: {ctx.user.name} ({ctx.user.role}); application roles: {', '.join(ctx.user.roles) or 'none'}.",
        f"Planning date (as-of): {horizon[0]:%Y-%m-%d}; horizon {horizon[0]:%Y-%m-%d} to {horizon[1]:%Y-%m-%d}.",
        f"Approved baseline: v{base.version_number}.",
    ]
    if ctx.selected_order:
        context.append(f"The user is looking at sales order {ctx.selected_order}.")
    if ctx.selected_version:
        context.append(f"Selected schedule version: {ctx.selected_version}.")
    return f"{RULES}\n\n{STYLE}\n\nContext:\n- " + "\n- ".join(context)


@dataclass
class ToolCall:
    name: str
    input: dict
    ok: bool
    seconds: float
    summary: str
    action_id: str | None = None


@dataclass
class Turn:
    prompt: str
    at: datetime
    answer: str = ""
    trace: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    rounds: int = 0


@dataclass
class Conversation:
    id: str
    user_id: str
    order: str | None
    version: str | None
    messages: list[dict] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_conversations: dict[str, Conversation] = {}
_store_lock = threading.Lock()


def conversation(user_id: str, conv_id: str | None, order: str | None = None, version: str | None = None) -> Conversation:
    with _store_lock:
        conv = _conversations.get(conv_id or "")
        if conv is None or conv.user_id != user_id:
            conv = Conversation(uuid.uuid4().hex[:12], user_id, order, version)
            if len(_conversations) > 500:
                for old in list(_conversations)[:100]:
                    _conversations.pop(old, None)
            _conversations[conv.id] = conv
        if order:
            conv.order = order
        if version:
            conv.version = version
        return conv


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value())


def _block(b) -> dict:
    """SDK content block (or a plain dict in tests) → the dict sent back to the API.

    Thinking blocks must go back unchanged (text and signature) or the next request is rejected; any other block
    type is passed back exactly as received."""
    if isinstance(b, dict):
        d = dict(b)
    elif hasattr(b, "model_dump"):
        d = b.model_dump(exclude_none=True)
    else:
        d = {k: getattr(b, k) for k in ("type", "text", "id", "name", "input", "thinking", "signature", "data")
             if getattr(b, k, None) is not None}
    kind = d.get("type")
    if kind == "text":
        return {"type": "text", "text": d.get("text") or ""}
    if kind == "tool_use":
        return {"type": "tool_use", "id": d.get("id"), "name": d.get("name"), "input": d.get("input") or {}}
    if kind == "thinking":
        return {"type": "thinking", "thinking": d.get("thinking", ""), "signature": d.get("signature", "")}
    if kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": d.get("data", "")}
    return d


def _summary(result: dict, is_error: bool) -> str:
    if is_error:
        return result.get("error", "error")
    for key in ("outcome", "status", "production_ready", "mrp_outcome", "bottleneck"):
        if key in result:
            return f"{key}: {result[key]}"
    return "ok"


def _trim(messages: list[dict]) -> list[dict]:
    """Keep the conversation bounded, cutting only at a plain user prompt (never between a tool call and its result)."""
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
    if len(starts) > MAX_TURNS_KEPT:
        return messages[starts[-MAX_TURNS_KEPT]:]
    return messages


def run_turn(conv: Conversation, prompt: str, ctx: ToolContext, client=None) -> Turn:
    settings = get_settings()
    turn = Turn(prompt=prompt, at=datetime.now(timezone.utc))
    with conv.lock:
        messages = _trim(conv.messages + [{"role": "user", "content": prompt}])
        tools = [t.api_spec() for t in TOOLS.values()]
        texts: list[str] = []
        try:
            client = client or _client()
            for turn.rounds in range(1, settings.assistant_max_tool_rounds + 1):
                resp = client.messages.create(model=settings.assistant_model, max_tokens=settings.assistant_max_tokens,
                                              system=system_prompt(ctx), tools=tools, messages=messages)
                content = [_block(b) for b in resp.content]
                messages.append({"role": "assistant", "content": content})
                texts = [b["text"] for b in content if b["type"] == "text" and b["text"].strip()] or texts
                uses = [b for b in content if b["type"] == "tool_use"]
                if getattr(resp, "stop_reason", None) != "tool_use" or not uses:
                    if uses:   # e.g. cut off by max_tokens: never leave tool calls without results in the history
                        messages[-1]["content"] = [b for b in content if b["type"] != "tool_use"] or \
                            [{"type": "text", "text": "(answer cut off)"}]
                    break
                results = []
                for use in uses:
                    t0 = time.perf_counter()
                    result, is_error = run_tool(ctx, use["name"], use["input"])
                    turn.trace.append(ToolCall(use["name"], use["input"], not is_error, round(time.perf_counter() - t0, 2),
                                               _summary(result, is_error), result.get("action_id")))
                    results.append({"type": "tool_result", "tool_use_id": use["id"], "is_error": is_error,
                                    "content": json.dumps(result, default=str)[:MAX_RESULT_CHARS]})
                messages.append({"role": "user", "content": results})
            else:
                texts.append(f"(Stopped after {settings.assistant_max_tool_rounds} tool rounds — ask a narrower question.)")
                # close the dangling tool results with an assistant note so the history stays valid
                messages.append({"role": "assistant", "content": [{"type": "text", "text": texts[-1]}]})
            turn.answer = "\n\n".join(texts) or "(No answer.)"
        except Exception as exc:  # API down, bad key, rate limit… — report, keep the history consistent
            log.exception("assistant turn failed")
            turn.error = _api_error(exc)
            messages = conv.messages            # drop the failed exchange
        conv.messages = messages
        conv.turns.append(turn)
    log.info("assistant turn for %s: %d tool calls, %d rounds%s", ctx.user.email or ctx.user.id, len(turn.trace),
             turn.rounds, f", error: {turn.error}" if turn.error else "")
    return turn


def _api_error(exc: Exception) -> str:
    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return "The AI service rejected the API key (check ANTHROPIC_API_KEY in .env)."
    if "RateLimit" in name or "Overloaded" in name:
        return "The AI service is busy right now — please try again in a moment."
    if "Connection" in name or "Timeout" in name:
        return "The AI service is unreachable — check the network and try again."
    return f"The assistant failed ({name}). Nothing was changed."


# ------------------------------------------------------------------ rendering (safe, minimal markdown)

_ORDER_RE = re.compile(r"\b(SO-\d{3,6})\b")


def render(text: str) -> Markup:
    """Escape, then allow **bold**, `code`, bullet/numbered lists, ### headings and order links."""
    out, in_list = [], None
    for raw in text.splitlines():
        line = html.escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`([^`]+)`", r'<code class="text-primary">\1</code>', line)
        line = _ORDER_RE.sub(r'<a class="text-primary hover:underline" href="/orders?selected=\1">\1</a>', line)
        bullet = re.match(r"^\s*[-*•]\s+(.*)", line)
        number = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        kind = "ul" if bullet else "ol" if number else None
        if in_list and kind != in_list:
            out.append(f"</{in_list}>")
            in_list = None
        if kind:
            if not in_list:
                out.append(f'<{kind} class="{"list-disc" if kind == "ul" else "list-decimal"} pl-space-lg flex flex-col gap-px">')
                in_list = kind
            out.append(f"<li>{(bullet or number).group(1)}</li>")
        elif re.match(r"^#{1,4}\s", line):
            out.append(f'<p class="font-label-mono-sm text-label-mono-sm uppercase text-outline mt-space-sm">{line.lstrip("#").strip()}</p>')
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    return Markup("\n".join(out))
