# -*- coding: utf-8 -*-
"""QwenPaw terminal chat UI (TUI).

A thin Textual front-end that drives the QwenPaw agent. The default transport
spawns ``qwenpaw acp`` as a subprocess and speaks ACP (Agent Client Protocol)
to it, so the TUI reuses the full agent backend without re-implementing it.

See ``docs/design/qwenpaw-cli-tui.md`` for the design.
"""

from __future__ import annotations

__all__ = ["run_tui", "chat_cmd"]


def __getattr__(name: str):  # pragma: no cover - thin lazy re-export
    # Lazy so importing this package never pulls Textual until the TUI runs.
    if name in __all__:
        from .launch import chat_cmd, run_tui

        return {"run_tui": run_tui, "chat_cmd": chat_cmd}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
