# -*- coding: utf-8 -*-
"""Pilot-driven smoke tests for the Textual app with a fake transport."""

from __future__ import annotations

import asyncio

import pytest

from qwenpaw.cli.tui.app import QwenPawTuiApp
from qwenpaw.cli.tui.events import (
    Connected,
    PermissionOption,
    PermissionRequest,
    TextDelta,
    ToolCall,
    TurnEnded,
)
from qwenpaw.cli.tui.widgets import (
    AssistantMessage,
    PermissionModal,
    ToolPanel,
    UserMessage,
)


class FakeTransport:
    """In-process transport that scripts a canned turn for the UI."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[str] = []
        self.interrupted = False
        self.resolved: list[tuple[str, str | None]] = []
        self.closed = False
        self._permission_mode = "none"

    async def start(self) -> Connected:
        return Connected(
            session_id="sess-abc", agent="default", model="qwen-max"
        )

    async def send(self, text: str) -> None:
        self.sent.append(text)
        if "permission" in text:
            await self._queue.put(
                PermissionRequest(
                    request_id="r1",
                    title="dangerous_tool",
                    options=[
                        PermissionOption("allow", "Allow", "allow_once"),
                        PermissionOption("deny", "Deny", "reject_once"),
                    ],
                )
            )
            return
        for chunk in ("Hello ", "there"):
            await self._queue.put(TextDelta(chunk))
        await self._queue.put(
            ToolCall(
                "t1",
                "read_file",
                kind="read",
                status="completed",
                output="data",
            )
        )
        await self._queue.put(TurnEnded(stop_reason="end_turn"))

    async def interrupt(self) -> None:
        self.interrupted = True
        await self._queue.put(TurnEnded(stop_reason="cancelled"))

    def events(self):
        async def _gen():
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item

        return _gen()

    async def resolve_permission(self, request_id, option_id):
        self.resolved.append((request_id, option_id))
        await self._queue.put(TextDelta(f"[{option_id}]"))
        await self._queue.put(TurnEnded())

    async def set_model(self, model_id):  # pragma: no cover
        pass

    async def close(self):
        self.closed = True
        await self._queue.put(None)


@pytest.mark.asyncio
async def test_basic_turn_renders():
    transport = FakeTransport()
    app = QwenPawTuiApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        # status bar picked up the Connected event
        assert "qwen-max" in app.query_one("StatusBar").summary

        prompt = app.query_one("#prompt")
        prompt.value = "hi"
        await pilot.press("enter")
        # let the scripted events drain
        for _ in range(10):
            await pilot.pause()
            if not app._busy:
                break

        assert transport.sent == ["hi"]
        assert any(isinstance(w, UserMessage) for w in app.query(UserMessage))
        assistant = app.query(AssistantMessage).first()
        assert assistant.text == "Hello there"
        tools = list(app.query(ToolPanel))
        assert tools and tools[0]._status == "completed"


@pytest.mark.asyncio
async def test_permission_modal_resolves():
    transport = FakeTransport()
    app = QwenPawTuiApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        prompt.value = "do permission thing"
        await pilot.press("enter")

        # The modal should appear; pick the first (Allow) button.
        for _ in range(10):
            await pilot.pause()
            if isinstance(app.screen, PermissionModal):
                break
        assert isinstance(app.screen, PermissionModal)
        await pilot.press("enter")  # default-focused first button = Allow
        for _ in range(10):
            await pilot.pause()
            if transport.resolved:
                break

        assert transport.resolved == [("r1", "allow")]


@pytest.mark.asyncio
async def test_interrupt_action():
    transport = FakeTransport()
    app = QwenPawTuiApp(transport)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt")
        prompt.value = (
            "permission please"  # leaves it busy awaiting permission
        )
        await pilot.press("enter")
        await pilot.pause()
        # dismiss modal if present so escape hits the app
        if isinstance(app.screen, PermissionModal):
            app.screen.dismiss(None)
            await pilot.pause()
        app._busy = True
        await app.action_interrupt()
        assert transport.interrupted
