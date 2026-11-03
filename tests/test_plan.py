"""Planning gate: a lesson must have a spine before it has ink."""

import asyncio

import pytest

from scribe.mcp import mcp
from scribe.session import Plan, Registry


def call(name, args=None):
    r = asyncio.run(mcp.call_tool(name, args or {}))
    content = r[0][0] if isinstance(r, tuple) else r.content[0]
    return getattr(content, "text", content)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from scribe import mcp as mcp_mod
    from scribe import session as session_mod

    registry = Registry()
    monkeypatch.setattr(mcp_mod, "REGISTRY", registry)
    monkeypatch.setattr(session_mod, "REGISTRY", registry, raising=False)
    yield registry


PLAN = {
    "idea": "A load balancer gives many servers one address.",
    "hook": "Which IP does your phone actually type in?",
    "beats": ["one server, many clients", "it buckles", "add servers, but which IP?"],
    "payoff": "The user only ever talks to one name.",
}


def test_draw_is_refused_until_a_plan_exists(_fresh):
    call("scribe_open", {"topic": "t", "open_browser": False})
    out = call("scribe_draw", {"ops": 'box 2,2 6,3 "too soon"'})
    assert "No plan yet" in out
    assert _fresh.current.ops == [], "nothing may be drawn before planning"


def test_draw_works_once_planned(_fresh):
    call("scribe_open", {"topic": "t", "open_browser": False})
    call("scribe_plan", PLAN)
    out = call("scribe_draw", {"ops": 'text 2,2 "the hook"'})
    assert "No plan yet" not in out
    assert len(_fresh.current.ops) == 1


def test_plan_returns_a_layout_budget(_fresh):
    call("scribe_open", {"topic": "t", "open_browser": False})
    out = call("scribe_plan", PLAN)
    assert "budget" in out
    assert "payoff" in out
    for beat in PLAN["beats"]:
        assert beat in out


def test_plan_rejects_an_empty_arc(_fresh):
    call("scribe_open", {"topic": "t", "open_browser": False})
    out = call("scribe_plan", {**PLAN, "beats": []})
    assert "at least one beat" in out
    assert _fresh.current.plan is None


def test_status_shows_the_plan(_fresh):
    call("scribe_open", {"topic": "t", "open_browser": False})
    assert "No plan yet" in call("scribe_status")
    call("scribe_plan", {**PLAN, "misconception": "that clients know each server"})
    out = call("scribe_status")
    assert PLAN["hook"] in out
    assert "that clients know each server" in out


def test_budget_reserves_room_for_the_payoff():
    plan = Plan(idea="i", hook="h", beats=["a", "b"], payoff="the point")
    budget = plan.budget(64, 36)
    assert "title" in budget and "payoff" in budget
    assert "y 32-36" in budget  # bottom band held back for the ending
