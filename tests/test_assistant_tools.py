"""M11 test gate: assistant tools (schemas, validation, authorization, results on the recorded plant data), the
human-confirmation gate for consequential actions, refusals, and the tool-calling loop with a mocked LLM.
The real AI service is never called (conftest sets ANTHROPIC_API_KEY="")."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent import loop as agent
from app.agent import tools as T
from app.config import get_settings
from app.data import dataset, supabase_dataset, writes
from app.main import app
from app.models import CurrentUser

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))
PLANNER = CurrentUser(id="u-planner", email="planner@example.com", name="Sarah Chen", role="Planner", roles=["planner"])
STORES = CurrentUser(id="u-stores", email="stores@example.com", name="Priya Nair", role="Stores", roles=["stores"])
APPROVER = CurrentUser(id="u-appr", email="approver@example.com", name="Ana", role="Approver", roles=["approver"])


@pytest.fixture
def db_dataset(monkeypatch):
    built = supabase_dataset.build_dataset(copy.deepcopy(SNAPSHOT))
    monkeypatch.setattr(dataset, "mock_data", built)
    T._plan_cache.clear()
    return built


def call(name, args, user=PLANNER):
    return T.run_tool(T.ToolContext(user=user, token="tok", conversation_id="c1"), name, args)


# ------------------------------------------------------------------ schemas & registry

def test_every_spec_tool_exists_with_documented_schemas():
    assert set(T.TOOLS) == {"get_order_details", "validate_order_readiness", "calculate_material_requirements",
                            "check_material_availability", "get_production_capacity", "get_schedule_status",
                            "generate_production_schedule", "simulate_order_change", "explain_delivery_risk",
                            "save_proposed_plan", "submit_plan_for_approval"}
    for tool in T.TOOLS.values():
        spec = tool.api_spec()
        assert spec["description"] and spec["input_schema"]["type"] == "object"
        assert "$ref" not in json.dumps(spec) and "$defs" not in json.dumps(spec)     # self-contained for the API
        assert tool.output_schema["type"] == "object" and tool.output_schema["properties"]


def test_no_tool_can_approve_release_purchase_or_commit():
    for name in T.TOOLS:
        assert not any(verb in name for verb in T.FORBIDDEN_VERBS), name
    assert {n for n, t in T.TOOLS.items() if t.consequential} == {"save_proposed_plan", "submit_plan_for_approval"}


# ------------------------------------------------------------------ validation & authorization

def test_invalid_input_is_rejected_with_a_clear_message(db_dataset):
    result, err = call("get_order_details", {"order_number": "1005; drop table"})
    assert err and "Invalid input for get_order_details: order_number" in result["error"]
    result, err = call("simulate_order_change", {"changes": [{"kind": "teleport", "params": {}}]})
    assert err and "changes.0.kind" in result["error"]
    result, err = call("approve_plan", {"version": "4.4"})
    assert err and "Unknown tool 'approve_plan'" in result["error"]


@pytest.mark.parametrize("name,args", [("generate_production_schedule", {}),
                                       ("simulate_order_change", {"changes": [{"kind": "priority", "params": {"order_number": "SO-1012", "priority": "Critical"}}]}),
                                       ("save_proposed_plan", {}), ("submit_plan_for_approval", {"version": "4.3"})])
def test_planning_tools_require_the_planner_role(db_dataset, name, args):
    for user in (STORES, APPROVER):
        result, err = call(name, args, user)
        assert err and result["error"].startswith(f"Not authorized: {name} requires one of the roles ['admin', 'planner']")


def test_read_tools_are_open_to_every_signed_in_role(db_dataset):
    result, err = call("get_order_details", {"order_number": "SO-1005"}, STORES)
    assert not err and result["order_number"] == "SO-1005"


def test_unexpected_failures_do_not_leak_details(db_dataset, monkeypatch):
    monkeypatch.setattr(T.repo, "get_order", lambda n: 1 / 0)
    result, err = call("get_order_details", {"order_number": "SO-1005"})
    assert err and result["error"] == "get_order_details failed unexpectedly (ZeroDivisionError). Report the failure; do not guess."


# ------------------------------------------------------------------ tool results come from the data

def test_order_details_and_readiness(db_dataset):
    details, _ = call("get_order_details", {"order_number": "SO-1005"})
    assert details["priority"] == "Critical" and details["requested_delivery_date"] == "2025-11-30"
    assert any(s["component"].startswith("Seat Ring") and s["note"] == "Short 3" for s in details["material_staging"])
    ready, _ = call("validate_order_readiness", {"order_number": "SO-1005"})
    assert ready["production_ready"] is False
    assert {"line": "SO-1005/1", "item": "SR-ST6-10", "shortage": 3} in ready["material_shortages"]
    draft, _ = call("validate_order_readiness", {"order_number": "SO-1021"})
    assert draft["engineering_and_resource_issues"][0]["kind"] == "missing_bom"      # acceptance test 8
    missing, err = call("get_order_details", {"order_number": "SO-9999"})
    assert err and "SO-9999 not found" in missing["error"]


def test_material_tools(db_dataset):
    mrp, _ = call("calculate_material_requirements", {"order_number": "SO-1012"})
    studs = next(r for r in mrp["requirements"] if r["item"] == "ST-IN718-M24")
    assert (studs["status"], studs["operation"], studs["ready_date"], studs["supply"]) == ("late", "OP-050", "2025-11-25", ["PO-8815/1"])
    avail, _ = call("check_material_availability", {"item_code": "SR-ST6-10"})
    assert sum(s["quality_hold"] for s in avail["stock"]) == 12 and avail["time_phased"]
    assert {d["line"] for d in avail["demand"]} >= {"SO-1005/1", "SO-1005/2", "SO-1022/1"}


def test_capacity_and_schedule_status(db_dataset):
    cap, _ = call("get_production_capacity", {"work_center_code": "WC-40"})
    assert [m["code"] for m in cap["work_centers"][0]["machines"]] == ["ND-001", "TE-001", "TE-004"]
    assert any(d["reason"] == "Pump overhaul" for d in cap["downtime"])
    status, _ = call("get_schedule_status", {})
    assert status["baseline"] == "4.2" and {v["version"] for v in status["versions"]} >= {"4.2", "4.3"}
    assert status["pending_approvals"][0]["request"] == "APR-1042"


def test_generate_schedule_is_read_only_and_validated(db_dataset, monkeypatch):
    monkeypatch.setattr(writes, "record_schedule_run", lambda *a, **k: pytest.fail("must not save"))
    result, err = call("generate_production_schedule", {"order_numbers": ["SO-1012"]})
    assert not err and result["saved"] is False and result["validation"]["passed"] is True
    assert result["lines"][0]["line"] == "SO-1012/1" and result["lines"][0]["status"] == "on_time"


def test_simulation_answers_what_if_without_saving(db_dataset, monkeypatch):
    monkeypatch.setattr(writes, "record_schedule_run", lambda *a, **k: pytest.fail("must not save"))
    result, err = call("simulate_order_change", {"changes": [{"kind": "supplier_delay",
                                                              "params": {"po_number": "PO-8821", "days": 5}}]})
    assert not err and result["saved"] is False and result["what_if_only"] is True
    assert [l["key"] for l in result["delivery_changes"]] == ["SO-1005/2"]
    bad, err = call("simulate_order_change", {"changes": [{"kind": "supplier_delay", "params": {"po_number": "PO-1", "days": 5}}]})
    assert err and "PO-1 not found" in bad["error"]


def test_delivery_risk_uses_explicit_rules(db_dataset):
    result, err = call("explain_delivery_risk", {"order_number": "SO-1005"})
    assert not err
    risk = {l["line"]: (l["risk"], l["risk_rule"]) for l in result["lines"]}
    assert risk["SO-1005/1"] == ("High", "unresolved material shortage")
    assert risk["SO-1005/2"][0] in ("Low", "Medium")
    assert "not statistical probabilities" in result["note"]
    assert any(m["item"] == "SR-ST6-10" for m in result["material_problems"])


def test_tools_say_when_live_data_is_needed():
    result, err = call("generate_production_schedule", {})                 # demo data: no snapshot
    assert err and "DATA_BACKEND=supabase" in result["error"]


# ------------------------------------------------------------------ human confirmation for consequential actions

def test_save_plan_needs_confirmation_and_is_idempotent(db_dataset, monkeypatch):
    saved = []
    monkeypatch.setattr(writes, "record_schedule_run",
                        lambda body, token=None: saved.append(body) or writes.WriteResult(True, "Run #1500: saved as Draft v4.6", {}))
    result, err = call("save_proposed_plan", {"label": "recover SO-1005"})
    assert not err and result["status"] == "confirmation_required" and not saved   # nothing happened yet
    again, _ = call("save_proposed_plan", {"label": "recover SO-1005"})
    assert again["action_id"] == result["action_id"]                                 # no duplicate proposals
    with pytest.raises(T.ToolError):
        T.execute_action(result["action_id"], STORES)                                # only the proposer's user
    done = T.execute_action(result["action_id"], PLANNER)
    assert done.status == "done" and len(saved) == 1
    assert saved[0]["version"]["label"] == "Assistant proposal — recover SO-1005"
    assert T.execute_action(result["action_id"], PLANNER).status == "done" and len(saved) == 1   # idempotent


def test_submit_refuses_what_cannot_be_submitted(db_dataset):
    baseline, err = call("submit_plan_for_approval", {"version": "4.2"})
    assert err and "only Draft versions can be submitted" in baseline["error"]
    proposed, err = call("submit_plan_for_approval", {"version": "4.3"})
    assert err and "Proposed" in proposed["error"]


def test_submit_confirmed_by_the_human_calls_the_workflow(db_dataset, monkeypatch):
    v = db_dataset.SCHEDULE_VERSIONS["v4.3"]
    monkeypatch.setattr(v, "status", "Draft")
    calls = []
    monkeypatch.setattr(writes, "submit_plan", lambda number, comments="": calls.append((number, comments))
                        or writes.WriteResult(True, "v4.3 submitted as APR-2100", "APR-2100"))
    proposal, _ = call("submit_plan_for_approval", {"version": "v4.3", "comments": "please review"})
    assert proposal["status"] == "confirmation_required" and not calls
    T.dismiss_action(proposal["action_id"], PLANNER)
    assert T.execute_action(proposal["action_id"], PLANNER).status == "dismissed" and not calls
    proposal2, _ = call("submit_plan_for_approval", {"version": "v4.3", "comments": "now"})
    assert T.execute_action(proposal2["action_id"], PLANNER).result == "v4.3 submitted as APR-2100"
    assert calls == [("4.3", "now")]


# ------------------------------------------------------------------ the loop with a mocked LLM

class FakeClient:
    """Returns scripted responses and records what the app sent (the real API is never called)."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


def resp(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def text(t):
    return {"type": "text", "text": t}


def use(i, name, args):
    return {"type": "tool_use", "id": i, "name": name, "input": args}


def test_loop_runs_tools_and_answers_from_their_results(db_dataset):
    client = FakeClient([
        resp(text("I will check the order and its risk."), use("t1", "get_order_details", {"order_number": "SO-1005"}),
             use("t2", "explain_delivery_risk", {"order_number": "SO-1005"}), stop="tool_use"),
        resp(text("**No** for line 1: SR-ST6-10 is short 3 (explain_delivery_risk)."))])
    conv = agent.conversation(PLANNER.id, None, "SO-1005", "v4.2")
    ctx = T.ToolContext(user=PLANNER, token="tok", conversation_id=conv.id, selected_order="SO-1005")
    turn = agent.run_turn(conv, "Can we deliver SO-1005 by 30 November?", ctx, client=client)
    assert [c.name for c in turn.trace] == ["get_order_details", "explain_delivery_risk"] and all(c.ok for c in turn.trace)
    assert turn.answer.startswith("**No** for line 1") and turn.error is None
    first, second = client.calls
    assert first["model"] == get_settings().assistant_model and len(first["tools"]) == 11
    assert "Never approve your own plan." in first["system"] and "sales order SO-1005" in first["system"]
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert json.loads(results[0]["content"])["order_number"] == "SO-1005" and results[0]["is_error"] is False
    # the conversation keeps the full exchange for the next question
    assert [m["role"] for m in conv.messages] == ["user", "assistant", "user", "assistant"]


def test_forbidden_or_unauthorized_calls_go_back_to_the_model_as_errors(db_dataset):
    client = FakeClient([
        resp(use("t1", "approve_plan", {"version": "4.3"}), use("t2", "generate_production_schedule", {}), stop="tool_use"),
        resp(text("I can't approve plans; an authorized approver must decide."))])
    conv = agent.conversation(STORES.id, None)
    turn = agent.run_turn(conv, "Approve v4.3 and re-plan", T.ToolContext(user=STORES, token="t", conversation_id=conv.id),
                          client=client)
    assert [c.ok for c in turn.trace] == [False, False]
    errors = [json.loads(r["content"])["error"] for r in client.calls[1]["messages"][-1]["content"]]
    assert errors[0].startswith("Unknown tool 'approve_plan'") and errors[1].startswith("Not authorized")
    assert "can't approve" in turn.answer


def test_loop_stops_after_the_round_limit_and_keeps_history_valid(db_dataset, monkeypatch):
    monkeypatch.setattr(get_settings(), "assistant_max_tool_rounds", 2)
    looping = [resp(use(f"t{i}", "get_schedule_status", {}), stop="tool_use") for i in range(2)]
    conv = agent.conversation(PLANNER.id, None)
    turn = agent.run_turn(conv, "status?", T.ToolContext(user=PLANNER, token="t", conversation_id=conv.id),
                          client=FakeClient(looping))
    assert "Stopped after 2 tool rounds" in turn.answer
    assert conv.messages[-1]["role"] == "assistant"                       # tool results are never left dangling


def test_api_failure_is_reported_and_nothing_is_kept(db_dataset):
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise type("AuthenticationError", (Exception,), {})("bad key")
    conv = agent.conversation(PLANNER.id, None)
    turn = agent.run_turn(conv, "hi", T.ToolContext(user=PLANNER, token="t", conversation_id=conv.id), client=Boom())
    assert turn.error == "The AI service rejected the API key (check ANTHROPIC_API_KEY in .env)." and conv.messages == []


def test_rendering_escapes_model_output():
    html = str(agent.render("**Late** <script>alert(1)</script>\n- SO-1005 line 1\n- `OP-030`"))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a class="text-primary hover:underline" href="/orders?selected=SO-1005">SO-1005</a>' in html
    assert "<b>Late</b>" in html and "<li>" in html


# ------------------------------------------------------------------ page and routes in agent mode

@pytest.fixture
def agent_mode(monkeypatch, db_dataset):
    from pydantic import SecretStr
    monkeypatch.setattr(get_settings(), "anthropic_api_key", SecretStr("sk-test-SECRET-123"))
    fake = FakeClient([resp(use("t1", "save_proposed_plan", {"label": "test"}), stop="tool_use"),
                       resp(text("I prepared it — press **Confirm** to save the draft."))])
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake


def test_agent_page_and_confirmation_flow(agent_mode, monkeypatch):
    client = TestClient(app)
    page = client.get("/assistant").text
    assert "AI assistant · claude-" in page and "sk-test-SECRET-123" not in page          # key stays server-side
    conv_id = page.split('name="conv_id" value="')[1].split('"')[0]
    reply = client.post("/assistant/message", data={"prompt": "Save the plan", "conv_id": conv_id, "order": "SO-1005"}).text
    assert "save_proposed_plan" in reply and "Your confirmation is needed" in reply and "<b>Confirm</b>" in reply
    action_id = reply.split('hx-post="/assistant/actions/')[1].split("/")[0]
    monkeypatch.setattr(writes, "record_schedule_run", lambda body, token=None: writes.WriteResult(True, "Run #1600: saved as Draft v4.7", {}))
    done = client.post(f"/assistant/actions/{action_id}/confirm").text
    assert "Action done" in done and "saved as Draft v4.7" in done
    assert "No such pending action" in client.post("/assistant/actions/nope/confirm").text


# ------------------------------------------------------------------ setup check (scripts/check_assistant.py)

def test_setup_check_explains_problems_without_showing_the_key():
    from pydantic import SecretStr

    from scripts.check_assistant import check
    no_key = Settings_for_test(anthropic_api_key=None)
    assert check(no_key)[0] is False and "not set" in check(no_key)[1]

    class Rejecting:
        def __init__(self, exc):
            self.messages, self.exc = self, exc

        def create(self, **kw):
            raise self.exc
    keyed = Settings_for_test(anthropic_api_key=SecretStr("sk-ant-test-SECRET"))
    auth = type("AuthenticationError", (Exception,), {})("invalid x-api-key")
    ok, msg = check(keyed, client=Rejecting(auth))
    assert not ok and "API key was rejected" in msg and "SECRET" not in msg
    credit = type("BadRequestError", (Exception,), {})("Your credit balance is too low to access the API")
    assert "no credit" in check(keyed, client=Rejecting(credit))[1]
    fine = FakeClient([resp(text("OK"))])
    ok, msg = check(keyed, client=fine)
    assert ok and "model claude-" in msg and fine.calls[0]["max_tokens"] == 10


def Settings_for_test(**overrides):
    from app.config import Settings
    return Settings(_env_file=None, app_env="test", auth_mode="disabled", **overrides)


def test_healthz_reports_assistant_configuration_only():
    body = TestClient(app).get("/healthz").json()
    assert body["assistant"] == {"enabled": False, "model": None}


def test_thinking_blocks_go_back_to_the_api_unchanged(db_dataset):
    """Seen live (M11): the model answers with a thinking block; the next request must carry it unchanged."""
    thinking = {"type": "thinking", "thinking": "Check the order first.", "signature": "sig-123"}
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    client = FakeClient([resp(thinking, redacted, use("t1", "get_order_details", {"order_number": "SO-1005"}), stop="tool_use"),
                         resp(text("Done."))])
    conv = agent.conversation(PLANNER.id, None)
    turn = agent.run_turn(conv, "SO-1005?", T.ToolContext(user=PLANNER, token="t", conversation_id=conv.id), client=client)
    assert turn.error is None and turn.answer == "Done."
    sent = client.calls[1]["messages"][1]["content"]
    assert sent[0] == thinking and sent[1] == redacted and sent[2]["type"] == "tool_use"
    assert client.calls[0]["max_tokens"] == get_settings().assistant_max_tokens
