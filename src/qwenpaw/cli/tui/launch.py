# -*- coding: utf-8 -*-
"""Entry points for the QwenPaw TUI.

``run_tui`` is called both by the bare ``qwenpaw`` command (no subcommand) and
by ``qwenpaw chat``. With ``--prompt/-p`` it runs a single headless turn and
prints the answer; otherwise it launches the full Textual app.

Textual is imported lazily inside these functions so that other CLI commands
never pay its import cost.
"""

from __future__ import annotations

import asyncio
import sys

import click


def _make_transport(*, agent: str | None, remote: str | None):
    """Build the transport for the requested mode.

    The HTTP/SSE transport (``--remote``) is a planned follow-up (design §4.2);
    until it lands we fail clearly rather than silently using ACP.
    """
    if remote:
        raise click.ClickException(
            "remote/HTTP transport is not implemented yet; run without "
            "--remote to use the embedded agent."
        )
    from .transport.acp import AcpTransport

    return AcpTransport(agent=agent)


def run_tui(
    ctx: click.Context | None = None,
    *,
    agent: str | None = None,
    prompt: str | None = None,
    remote: str | None = None,
) -> None:
    """Launch the interactive TUI, or run a one-shot turn with ``prompt``."""
    transport = _make_transport(agent=agent, remote=remote)

    if prompt is not None:
        exit_code = asyncio.run(_run_oneshot(transport, prompt))
        if exit_code:
            sys.exit(exit_code)
        return

    from .app import QwenPawTuiApp

    QwenPawTuiApp(transport, agent=agent or "default").run()


async def _run_oneshot(transport, prompt: str) -> int:
    """Send one prompt, stream the answer to stdout, return an exit code."""
    from .events import TextDelta, TransportError, TurnEnded

    rc = 0
    try:
        await transport.start()
        await transport.send(prompt)
        async for event in transport.events():
            if isinstance(event, TextDelta):
                sys.stdout.write(event.text)
                sys.stdout.flush()
            elif isinstance(event, TransportError):
                sys.stderr.write(f"\nerror: {event.message}\n")
                rc = 1
            elif isinstance(event, TurnEnded):
                break
        sys.stdout.write("\n")
    finally:
        await transport.close()
    return rc


@click.command("chat")
@click.option(
    "--agent", default=None, help="Agent ID to chat with (defaults to active)."
)
@click.option(
    "-p",
    "--prompt",
    default=None,
    help="Run a single turn non-interactively and print the answer.",
)
@click.option(
    "--remote",
    default=None,
    help="Attach to a running 'qwenpaw app' server (HTTP/SSE) instead of "
    "spawning a local agent.",
)
@click.pass_context
def chat_cmd(
    ctx: click.Context,
    agent: str | None,
    prompt: str | None,
    remote: str | None,
) -> None:
    """Open an interactive terminal chat with your QwenPaw agent."""
    run_tui(ctx, agent=agent, prompt=prompt, remote=remote)
