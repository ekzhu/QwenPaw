# -*- coding: utf-8 -*-
"""Translate ACP ``session_update`` objects into normalized ``TuiEvent``s.

Kept free of Textual and of the connection runtime so it is trivially
unit-testable (see ``tests/cli/test_tui_normalize.py``). The shapes here match
the ``acp.schema`` types verified against ``agent-client-protocol`` 0.9.x:

* ``AgentMessageChunk`` / ``AgentThoughtChunk`` carry a single content block
  whose ``.text`` is already a *delta* (the QwenPaw ACP server emits deltas).
* ``ToolCallStart`` / ``ToolCallProgress`` share ``tool_call_id`` so the UI can
  find-or-update one panel.
* ``AgentPlanUpdate`` and ``UsageUpdate`` map straight across.
"""

from __future__ import annotations

from typing import Any

from .events import (
    PlanEntry,
    PlanUpdate,
    TextDelta,
    ThoughtDelta,
    ToolCall,
    TuiEvent,
    Usage,
)


def _block_text(content: Any) -> str:
    """Pull text out of an ACP content block (object or dict)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", "") or "")
    return str(getattr(content, "text", "") or "")


def _tool_output_text(content: Any) -> str:
    """Flatten a tool-call ``content`` list into display text."""
    if not content:
        return ""
    parts: list[str] = []
    for item in content:
        inner = (
            item.get("content")
            if isinstance(item, dict)
            else getattr(item, "content", None)
        )
        text = _block_text(inner)
        if text:
            parts.append(text)
    return "\n".join(parts)


def normalize_update(update: Any) -> list[TuiEvent]:
    """Convert one ACP ``session_update`` payload into zero or more events.

    Accepts the typed ``acp.schema`` objects. Unknown updates yield ``[]`` so
    the UI degrades gracefully rather than crashing on protocol additions.
    """
    kind = getattr(update, "session_update", None)

    if kind == "agent_message_chunk":
        text = _block_text(getattr(update, "content", None))
        return [TextDelta(text)] if text else []

    if kind == "agent_thought_chunk":
        text = _block_text(getattr(update, "content", None))
        return [ThoughtDelta(text)] if text else []

    if kind in ("tool_call", "tool_call_update"):
        return [
            ToolCall(
                tool_call_id=getattr(update, "tool_call_id", ""),
                title=getattr(update, "title", None) or "tool",
                kind=getattr(update, "kind", None),
                status=getattr(update, "status", None),
                output=_tool_output_text(getattr(update, "content", None))
                or None,
            )
        ]

    if kind == "plan":
        entries = [
            PlanEntry(
                content=getattr(e, "content", "") or "",
                status=getattr(e, "status", "pending") or "pending",
                priority=getattr(e, "priority", "medium") or "medium",
            )
            for e in (getattr(update, "entries", None) or [])
        ]
        return [PlanUpdate(entries=entries)]

    if kind == "usage_update":
        return [
            Usage(
                used=int(getattr(update, "used", 0) or 0),
                size=int(getattr(update, "size", 0) or 0),
            )
        ]

    # current_mode / available_commands / config_option / session_info /
    # user_message_chunk: not surfaced in the chat transcript (yet).
    return []
