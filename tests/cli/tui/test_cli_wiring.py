# -*- coding: utf-8 -*-
"""Verify main.py wiring: bare command launches the TUI, chat exists, etc.

``qwenpaw.cli.main`` imports ``qwenpaw.config.utils`` at module load, which
pulls the heavy backend. We stub just that symbol so the CLI graph can be
exercised without agentscope installed.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def cli(monkeypatch):
    # Stub qwenpaw.config.utils.read_last_api (heavy import otherwise).
    fake_utils = types.ModuleType("qwenpaw.config.utils")
    fake_utils.read_last_api = lambda: None
    fake_config = sys.modules.get("qwenpaw.config") or types.ModuleType(
        "qwenpaw.config"
    )
    monkeypatch.setitem(sys.modules, "qwenpaw.config", fake_config)
    monkeypatch.setitem(sys.modules, "qwenpaw.config.utils", fake_utils)

    # Fresh import of main under the stub.
    sys.modules.pop("qwenpaw.cli.main", None)
    from qwenpaw.cli import main as main_mod

    return main_mod


def _runner():
    from click.testing import CliRunner

    return CliRunner()


def test_chat_subcommand_registered(cli):
    result = _runner().invoke(cli.cli, ["--help"])
    assert result.exit_code == 0
    assert "chat" in result.output
    assert "chats" in result.output  # plural CRUD preserved


def test_bare_command_launches_tui(cli, monkeypatch):
    calls = {}

    def fake_run_tui(ctx, *, agent=None, prompt=None, remote=None):
        calls["agent"] = agent

    import qwenpaw.cli.tui.launch as launch

    monkeypatch.setattr(launch, "run_tui", fake_run_tui)

    result = _runner().invoke(cli.cli, [])
    assert result.exit_code == 0, result.output
    assert calls == {"agent": None}


def test_bare_command_with_agent(cli, monkeypatch):
    calls = {}
    import qwenpaw.cli.tui.launch as launch

    monkeypatch.setattr(
        launch,
        "run_tui",
        lambda ctx, *, agent=None, **kw: calls.update(agent=agent),
    )
    result = _runner().invoke(cli.cli, ["--agent", "writer"])
    assert result.exit_code == 0, result.output
    assert calls == {"agent": "writer"}


def test_no_tui_prints_help(cli, monkeypatch):
    called = {"n": 0}
    import qwenpaw.cli.tui.launch as launch

    monkeypatch.setattr(
        launch,
        "run_tui",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    result = _runner().invoke(cli.cli, ["--no-tui"])
    assert result.exit_code == 0
    assert called["n"] == 0
    assert "Usage:" in result.output


def test_chat_help(cli):
    result = _runner().invoke(cli.cli, ["chat", "--help"])
    assert result.exit_code == 0
    assert "--prompt" in result.output
    assert "--agent" in result.output
    assert "--remote" in result.output
