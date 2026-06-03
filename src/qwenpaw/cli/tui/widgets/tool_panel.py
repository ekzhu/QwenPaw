# -*- coding: utf-8 -*-
"""Collapsible tool-call panel (merges start + update by tool_call_id)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

_STATUS_GLYPH = {
    "pending": ("◌", "#8a8a8a"),
    "in_progress": ("◍", "#6db8ff"),
    "completed": ("●", "#6dff9d"),
    "failed": ("✗", "#ff6d6d"),
}


class ToolPanel(Static):
    """One tool call. Updated in place as ACP sends progress events."""

    def __init__(
        self, tool_call_id: str, title: str, kind: str | None = None
    ) -> None:
        self._id = tool_call_id
        self._title = title or "tool"
        self._kind = kind
        self._status = "pending"
        self._output: str | None = None
        self._show_output = True
        super().__init__(self._render_text(), classes="tool")

    def update_call(
        self,
        *,
        title: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        output: str | None = None,
    ) -> None:
        if title:
            self._title = title
        if kind:
            self._kind = kind
        if status:
            self._status = status
        if output is not None:
            self._output = output
        self.update(self._render_text())

    def set_show_output(self, show: bool) -> None:
        self._show_output = show
        self.update(self._render_text())

    def _render_text(self) -> Text:
        glyph, color = _STATUS_GLYPH.get(self._status, ("◌", "#8a8a8a"))
        header = Text()
        header.append(f"{glyph} ", style=f"bold {color}")
        header.append("🔧 ", style="")
        header.append(self._title, style="bold")
        if self._kind:
            header.append(f"  ({self._kind})", style="#8a8a8a")
        header.append(f"  {self._status}", style=f"{color}")
        if self._output and self._show_output:
            snippet = self._output.strip()
            if len(snippet) > 600:
                snippet = snippet[:600] + " …"
            return Text.assemble(header, "\n", Text(snippet, style="#b0b0b0"))
        return header
