# -*- coding: utf-8 -*-
"""Unit tests for the ACP-update → TuiEvent normalizer."""

from __future__ import annotations

from acp import (
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_agent_thought,
    update_tool_call,
)
from acp.schema import AgentPlanUpdate, PlanEntry, UsageUpdate

from qwenpaw.cli.tui import events as E
from qwenpaw.cli.tui.normalize import normalize_update


def test_message_chunk_is_text_delta():
    out = normalize_update(update_agent_message(text_block("hi")))
    assert out == [E.TextDelta("hi")]


def test_empty_message_chunk_is_dropped():
    assert normalize_update(update_agent_message(text_block(""))) == []


def test_thought_chunk():
    out = normalize_update(update_agent_thought(text_block("ponder")))
    assert out == [E.ThoughtDelta("ponder")]


def test_tool_call_start_and_update():
    start = normalize_update(
        start_tool_call("t1", "grep", kind="search", status="in_progress")
    )
    assert start == [
        E.ToolCall(
            tool_call_id="t1",
            title="grep",
            kind="search",
            status="in_progress",
        )
    ]

    done = normalize_update(
        update_tool_call(
            "t1",
            status="completed",
            content=[tool_content(text_block("3 hits"))],
        )
    )
    assert len(done) == 1
    ev = done[0]
    assert ev.tool_call_id == "t1"
    assert ev.status == "completed"
    assert ev.output == "3 hits"


def test_plan_update():
    upd = AgentPlanUpdate(
        session_update="plan",
        entries=[
            PlanEntry(content="step 1", status="completed", priority="high"),
            PlanEntry(content="step 2", status="pending", priority="low"),
        ],
    )
    out = normalize_update(upd)
    assert len(out) == 1
    plan = out[0]
    assert isinstance(plan, E.PlanUpdate)
    assert [e.content for e in plan.entries] == ["step 1", "step 2"]
    assert plan.entries[0].status == "completed"


def test_usage_update():
    out = normalize_update(
        UsageUpdate(session_update="usage_update", used=1200, size=8000)
    )
    assert out == [E.Usage(used=1200, size=8000)]


def test_unknown_update_is_empty():
    class Weird:
        session_update = "something_new"

    assert normalize_update(Weird()) == []
