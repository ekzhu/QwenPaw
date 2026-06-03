# Design: `qwenpaw` Terminal Chat UI (CLI TUI)

**Status:** Implemented (M1 + most of M2); HTTP transport (M3) deferred
**Author:** (design)
**Date:** 2026-06-02
**Goal:** Let a user run `qwenpaw` from the command line and get an interactive,
streaming terminal chat experience with their QwenPaw agent — no browser, no
separate server step.

---

## Implementation status

Shipped under `src/qwenpaw/cli/tui/` and wired into `src/qwenpaw/cli/main.py`:

- **Embedded ACP transport** (`transport/acp.py`) — spawns `qwenpaw acp`, drives
  it as an ACP client; streaming text/thinking/tools, permission round-trip,
  interrupt (`cancel`), model switching, process-tree cleanup. Verified
  end-to-end against a fake ACP agent.
- **Normalizer** (`normalize.py`) — ACP `session_update` → `TuiEvent`.
- **Textual app + widgets** (`app.py`, `widgets/`) — status bar, streaming
  markdown transcript, collapsible tool panels, dimmed thinking lane, typed
  permission modal, push-message + error lanes.
- **Entry points** (`launch.py`, `main.py`) — bare `qwenpaw` launches the TUI;
  `qwenpaw chat` (interactive), `qwenpaw chat -p` (one-shot), `--agent`,
  `--no-tui`; plural `qwenpaw chats` (CRUD) preserved.
- **Tests** (`tests/cli/tui/`) — 18 tests: normalizer units, ACP transport
  integration (fake agent subprocess), Textual `Pilot` UI smoke tests, CLI
  wiring. `textual>=4.0` added to `pyproject.toml`.

**Deferred:** HTTP/SSE transport (`--remote`, §4.2) raises a clear "not
implemented" error for now; live plan rendering is a simple inline summary; the
Tier-3 server-initiated ACP extensions (§4.3) are consumed if present
(`ext_notification` → push lane) but the server-side emitters are future work.

---

## 1. TL;DR / Recommendation

**Build a native Textual TUI, shipped inside QwenPaw, that drives the agent as an
ACP client over the *already-existing* `qwenpaw acp` server.**

- Bare `qwenpaw` (no subcommand) launches the TUI.
- The TUI spawns `qwenpaw acp` as a child process and speaks **ACP (Agent Client
  Protocol, JSON-RPC 2.0 over stdio)** to it — the same protocol Zed already
  uses to drive QwenPaw.
- We reuse **100% of the agent backend** (Workspace, MCP tools, memory,
  sub-agents, permissions, model switching, sessions) because the ACP server
  already exposes all of it. We write only a thin terminal *client*.
- A second transport — connect to a running HTTP server over SSE — is added as
  an opt-in (`qwenpaw --remote URL`) for cloud/shared deployments, sharing the
  same UI layer.

We do **not** adopt Toad, OpenCode, Codex, or Crush wholesale (see §3 for the
full comparison). We *do* adopt their best idea — ACP as the agent/UI boundary —
which QwenPaw already implements on the agent side.

---

## 2. Why this is the right shape (the key facts)

Two facts from the current codebase decide the design:

1. **QwenPaw already speaks ACP as an agent.** `qwenpaw acp`
   (`src/qwenpaw/cli/acp_cmd.py`) starts `QwenPawACPAgent`
   (`src/qwenpaw/agents/acp/server.py:327`) over stdio. It boots the **full
   Workspace** and already streams:
   - assistant text deltas + thinking blocks (`_msg_to_updates`,
     `server.py:140`),
   - tool-call start / progress / completion events,
   - permission requests,
   - session create / load / resume / list / close,
   - model switching and token-usage metadata.

   This is the exact event surface a chat TUI needs. The hard part is **done**.

2. **There is no terminal chat today, and the client half of ACP already exists
   in-repo.** The CLI has only batch CRUD (`qwenpaw chats list/get/...`,
   `src/qwenpaw/cli/chats_cmd.py`) and config wizards built on `questionary`.
   But `src/qwenpaw/agents/acp/client.py` already implements an ACP *client*
   (`ACPHostedClient`) using `acp.contrib.session_state.SessionAccumulator` and
   `ToolCallView`. So the client-side stream-accumulation logic we need is
   already written and tested for the "QwenPaw delegated to by another agent"
   path — we reuse those same helpers.

Net: the TUI is a **rendering layer over an event stream we already produce and
already know how to consume.** Estimated new code is a few hundred lines of
Textual UI plus a transport shim, not a new agent runtime.

---

## 3. Alternatives considered (research summary)

The question explicitly asked whether we can reuse an existing frontend (pi.dev,
OpenCode, Codex, …). Findings:

| Option | Lang / License | Can it front *our* agent? | Verdict |
|---|---|---|---|
| **ACP standard** (Zed) | spec | — it's the boundary, not a UI | **Adopt the protocol** (already implemented agent-side) |
| **Toad** (Will McGugan) | Python/Textual, **AGPL-3.0** | Yes — it's a standalone ACP *client* TUI; could drive `qwenpaw acp` today | **Reference / optional power-user target, not bundled** (AGPL + unbranded + coding-centric UX) |
| **OpenCode** | TS server + Go TUI, MIT | Only by re-implementing its OpenAPI server contract; its TUI is *not* an ACP client | Reject — high coupling |
| **Codex CLI** | Rust, Apache-2.0 | Model-swappable, but *agent loop is its own*; expects to *be* the agent | Reject as a frontend |
| **Crush** (Charm) | Go, FSL-MIT | Same as Codex — provider-swappable, agent-monolithic; not an ACP client | Reject as a frontend |
| **Aider / Gemini CLI / Claude Code** | mixed | Whole apps; no embeddable TUI; reusable only *as ACP agents* | Reject |
| **pi.dev** | — | It's an *agent* (a peer/competitor), exposed via `pi-acp`; not a frontend | Reject; good ref architecture |
| **Hermes Agent** (Nous Research) | Python backend + **TS/React/Ink** TUI, **MIT** | Yes, but only over **its own bespoke ~17-event JSON-RPC/stdio dialect**, and it drags in a **Node.js runtime** | **Reject the code; borrow the UX & validate the architecture** (see §3.1) |

### 3.1 Hermes Agent — the closest architectural sibling

Of everything surveyed, **Hermes Agent** (`github.com/NousResearch/hermes-agent`,
MIT) is the most instructive because its architecture is *the same shape as ours*
— and its differences tell us exactly why to keep ACP rather than copy Hermes.

- **What it is:** a self-hosted, persistent *general-purpose* agent/orchestrator
  (memory, auto-skills, subagents, scheduling, Telegram/Discord/Slack/CLI
  gateways). That product shape is much closer to QwenPaw than the coding agents
  (Codex/Crush/OpenCode) are.
- **Its TUI (`hermes --tui`):** **TypeScript + React + Ink** (a vendored Ink fork
  `packages/hermes-ink/`), run as a **Node.js ≥20 subprocess** launched from the
  Python CLI. There's also a legacy Python `prompt_toolkit` CLI.
- **Its transport:** the Ink frontend is a thin client that spawns the Python
  backend and they exchange **newline-delimited JSON-RPC over stdio**, with a
  documented ~17-event surface: `message.start/delta/complete`,
  `thinking.delta`, `reasoning.delta`, `tool.start/progress/complete`,
  `approval.request`, `sudo.request`, `secret.request`, `clarify.request`,
  `session.info`, `background.complete`, `error`, etc. The README states any
  backend implementing this surface can replace the Python gateway without
  touching the TS client.

**This is a strong independent confirmation of our chosen architecture:** a thin
terminal client talking JSON-RPC-over-stdio to a swappable agent backend, with
streaming/thinking/tool/permission as distinct events, is precisely what we are
building. Hermes proves the pattern works for a *personal-assistant-shaped*
agent, not just coding agents.

**Why we still don't reuse Hermes's TUI code:**
1. **Wrong runtime.** It's TS/React/Ink on Node. Bundling it makes the
   otherwise pure-Python QwenPaw ship and manage a Node ≥20 toolchain and a
   second process language. That's the single biggest operational cost and it
   buys us nothing the Python/Textual path doesn't.
2. **Bespoke protocol, not a standard.** To drive it we'd write a QwenPaw
   "gateway" that re-emits our agent events in Hermes's private ~17-event
   dialect. We already emit a *standard* (ACP) that the agent server speaks
   today — adapting to Hermes's dialect is strictly more work for a non-standard
   target. Hermes does **not** speak ACP for its TUI (ACP server is only a
   proposed issue; an ACP *client* lives on a branch for backend orchestration,
   unrelated to the TUI transport).
3. **Not a packaged component.** `ui-tui/` is self-contained and MIT, but the
   widgets aren't published as a reusable library; they're coupled to Hermes's
   command/event vocabulary.

**What we *do* take from Hermes — UX patterns** (folded into §5):
- **Alternate-screen differential rendering** with an *instant first frame*
  (paint the header/banner before the app finishes wiring up) — no flicker
  while streaming, clean scrollback on exit. Textual gives us this natively.
- **Three distinct stream channels**, not one blob: visible **message** deltas
  vs. **thinking** vs. **reasoning/tool output**, each styled differently. We
  map ACP's `AgentMessageChunk` / `AgentThoughtChunk` / tool updates onto the
  same three lanes.
- **Permissions as modal overlays**, with *typed* requests (approve / sudo /
  secret / clarify) rather than one generic yes/no inline prompt.
- **Floating slash-command autocomplete** with descriptions, and a categorized
  arrow-navigable `/help` overlay.
- **Live session switcher** (`Ctrl+X` / `/sessions`) and a **`/agents` subagent
  tree** with kill/pause — directly relevant since QwenPaw has `spawn_subagent`.
- **`/details` toggle** to expand/collapse tool-call verbosity.

These are now reflected in §5.2–§5.4 (typed permission modal, three stream
lanes, instant-first-frame, subagent tree, `/details`).

### 3.2 Textual (Python) vs TypeScript/Ink — the build-language tradeoff

Hermes makes the choice concrete: its TUI is **TypeScript + React + Ink** on
Node; ours will be **Textual** on Python. Both can produce an excellent
streaming chat TUI, so the decision turns on *fit with QwenPaw*, not raw UI
capability.

| Dimension | Textual (Python) | TypeScript / Ink |
|---|---|---|
| **Runtime footprint** | ✅ One language. Pure-Python pip dep; one wheel, one runtime, one `pip install`. | ❌ Drags **Node.js ≥20 + npm build** into a Python project; UI runs as a separate Node subprocess; release/Docker/source-install all grow a JS half. |
| **Backend coupling** | ✅ Can run **in-process**, or drive `qwenpaw acp` over the **standard ACP** we already emit. | ❌ *Forces* a process boundary + serialization no matter what; Hermes needed a **bespoke ~17-event JSON-RPC dialect** — non-standard glue we'd have to adopt or reinvent. |
| **Contributor fit** | ✅ Every QwenPaw dev is already Python; one lint/test/review lane (pytest + Textual's `Pilot`). | ❌ Splits the contributor pool; adds a TS CI lane (tsc/eslint/vitest/bundling); UI bugs need a React reviewer. |
| **UI ecosystem maturity** | ⚠️ Younger, but sufficient — v4 ships **streaming markdown** for LLM token streams, async Workers, CSS-like styling. **Toad** (production ACP chat TUI) proves it. | ✅ Larger, more battle-tested component ecosystem; React's declarative/hooks model is productive for complex stateful UIs. |
| **Render polish** | ✅ Alternate-screen differential render, ~120fps, no flicker, instant-first-frame reproducible. | ✅ Parity (Ink does the same). |
| **Startup latency** | ✅ Single process; Textual import hidden behind lazy-loading so other `qwenpaw` subcommands aren't slowed. | ❌ Python CLI must spawn + hand off to Node — extra cold-start and a "Node missing/wrong version" failure mode. |

**The only real argument for TS/Ink is React's ecosystem and mental model.** It
is outweighed here because QwenPaw is a Python project that **already emits ACP**,
and the UI's complexity (streaming chat, tool panels, modals, slash palette) sits
comfortably inside what Textual v4 does well — we are not near its ceiling. The
TS path's tax (Node runtime + forced process boundary + bespoke protocol) is
exactly the cost that made us reject reusing Hermes's TUI wholesale in §3.1.

**Decision: Textual.** Stay in-language, talk ACP, keep one runtime and one
contributor pool; borrow Hermes's *UX patterns* (§3.1), not its *stack*.

**Why not just bundle Toad?** It's the closest reuse: a Textual ACP-client TUI
that already supports a dozen agents, and it *would* drive `qwenpaw acp`
unchanged. We deliberately don't bundle it because (a) **AGPL-3.0** is a
distribution liability for a permissively-licensed product, (b) it's **not
brandable** as `qwenpaw` and ships its own update/version story, and (c) its UX
is tuned for *coding* agents (diffs, file permissions), whereas QwenPaw is a
*personal assistant* (channels, schedules, memory, multi-agent). We keep Toad as
a documented "works out of the box" option for power users, and as the proof
that the ACP-client-on-Textual approach is sound — but the first-party `qwenpaw`
experience is our own thin Textual client.

**Why build vs. reuse, net:** the reusable artifacts are all either
license-encumbered (Toad), agent-monolithic (Codex/Crush/OpenCode), or whole
apps. The genuinely reusable thing is **the protocol**, and we already speak it.
Building a small Textual client gives us a branded, MIT-clean, assistant-shaped
UX for a few hundred lines, while inheriting the entire battle-tested backend.

---

## 4. Architecture

```
┌──────────────────────────── terminal ────────────────────────────┐
│  qwenpaw  (bare command)                                          │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Textual App  (qwenpaw.cli.tui)                              │ │
│  │   • ChatLog (streaming markdown)   • ToolCallPanel          │ │
│  │   • PromptInput  • StatusBar (model, session, tokens)       │ │
│  │   • PermissionModal  • SlashCommandBar                      │ │
│  └───────────────▲───────────────────────────┬─────────────────┘ │
│                  │ UI events                  │ user actions       │
│  ┌───────────────┴───────────────────────────▼─────────────────┐ │
│  │  TuiTransport  (abstract)                                    │ │
│  │   ├─ AcpTransport   ← default, self-contained                │ │
│  │   └─ HttpTransport  ← `--remote URL`, talks to running server │ │
│  └───────────────▲───────────────────────────┬─────────────────┘ │
└──────────────────┼───────────────────────────┼───────────────────┘
                   │ ACP JSON-RPC / stdio        │ HTTP + SSE
        ┌──────────▼──────────┐       ┌──────────▼──────────────┐
        │  qwenpaw acp        │       │  qwenpaw app (FastAPI)  │
        │  (subprocess)       │       │  /api/console/chat …    │
        │  QwenPawACPAgent    │       │  (already exists)       │
        │  → full Workspace   │       └─────────────────────────┘
        └─────────────────────┘
```

### 4.1 Default transport: embedded ACP (self-contained)

`AcpTransport` launches `sys.executable -m qwenpaw acp [--agent ID]` as a
subprocess and connects an ACP **client** to its stdio, using `acp`'s client
runtime (`run_client` / `ClientSideConnection`) and reusing the accumulation
helpers from `acp.contrib.session_state` exactly as `ACPHostedClient` does.

The TUI client implements the ACP `Client` interface methods:
- `session_update(...)` → translate ACP updates (`AgentMessageChunk`,
  `AgentThoughtChunk`, tool-call start/update, plan, usage) into Textual
  messages posted to the UI.
- `request_permission(...)` → open `PermissionModal`, await the user's choice,
  return the selected option id.
- `ext_method` / `ext_notification` → no-op / log.

This path needs **no running server and no config of host/port** — `qwenpaw`
just works in a fresh checkout. It's the headline UX.

### 4.2 Optional transport: HTTP/SSE (remote / shared backend)

`HttpTransport` targets an already-running `qwenpaw app` (local or cloud):
- send via `POST /api/console/chat` (`src/qwenpaw/app/routers/console.py:135`)
  with `stream:true`,
- consume the SSE stream (the same events the web console parses in
  `console/src/pages/Chat/index.tsx`),
- stop via `POST /api/console/chat/stop`,
- poll `GET /api/console/push-messages` for approvals/push, mapping approvals
  onto the same `PermissionModal`.

Auth: reuse `src/qwenpaw/cli/http.py` (`resolve_base_url`, `client`) and the
bearer-token mechanism (`QWENPAW_AUTH_ENABLED`). The `--remote` flag (or
auto-detect: if a server is already healthy at the resolved host/port, offer to
attach) selects this transport.

The two transports implement one small interface so the UI layer is
transport-agnostic:

```python
class TuiTransport(Protocol):
    async def start(self) -> SessionInfo: ...
    async def send(self, blocks: list[ContentBlock]) -> None: ...   # user turn
    async def interrupt(self) -> None: ...                          # cancel turn
    def events(self) -> AsyncIterator[TuiEvent]: ...                # normalized stream
    async def set_model(self, model_id: str) -> None: ...
    async def resolve_permission(self, request_id, option_id) -> None: ...
    async def close(self) -> None: ...
```

`TuiEvent` is a small normalized union (`TextDelta`, `ThoughtDelta`,
`ToolCallStarted/Updated`, `PermissionRequested`, `Usage`, `TurnEnded`,
`Error`) so AcpTransport and HttpTransport both feed identical UI code.

### 4.3 Protocol coverage: does ACP cover QwenPaw's primitives? Do we need extensions?

**Short answer: vanilla ACP covers ~everything; we add only two thin extensions
for *server-initiated* events.** The reason is structural: **every QwenPaw
slash/magic command is plain text routed through `prompt()`.** The ACP server's
`prompt()` → `query_handler()` → `run_command_path()`
(`src/qwenpaw/app/runner/command_dispatch.py`) is the *same* dispatcher the web
console hits. So the TUI just sends `/clear`, `/model …`, `/approval …`,
`/compact …` as a normal prompt and renders the streamed reply — **no protocol
work for the entire command surface.**

Coverage falls into four tiers:

| Tier | Primitives | Status |
|---|---|---|
| **1 — Free over vanilla ACP** | All slash commands: `/compact /new /clear /history /message /dump_history /load_history /plan /proactive /status /restart /reload-config /version /logs /stop /approval(approve\|deny\|list\|cancel) /model(list\|switch\|reset\|info) /skills /mission`, and `/<skill>` invocation | ✅ Work **today** — text through `prompt()`; reply streams back as an agent message |
| **2 — Native ACP turn primitives** | Assistant text, thinking, tool-call start/update/complete, token usage (`field_meta.usage`), interrupt (ACP `cancel`), in-turn permission (`request_permission`), **file/image/resource attachments as ACP content blocks** | ✅ Native — the TUI reads a local file and embeds it as an ACP `ResourceContentBlock`/`ImageContentBlock`; **no `/console/upload` endpoint needed** |
| **3 — Server-initiated / out-of-turn (needs work)** | (a) **Proactive/idle push** — `/proactive` makes the agent message you unprompted; (b) **live plan/todo updates** — today broadcast over web SSE only; (c) **cross-session/background approval alerts** — a cron/background task needs approval while you're in another session | ⚠️ Vanilla ACP turns are **client-initiated**, so these need a push channel (see below) |
| **4 — Not chat primitives** | Cron CRUD, channel management, mission phase dashboards | ➡️ Stay in `qwenpaw cron` / web console; TUI links out via `/web` |

**What Tier 3 actually requires — minimal, and ACP-sanctioned:**

- **(a) proactive push** and **(c) background-approval alerts** → one
  **`ext_notification`** each (e.g. `qwenpaw/push_message`,
  `qwenpaw/approval_pending`). ACP treats `_meta` / `ext_method` /
  `ext_notification` as first-class extension points, and the QwenPaw ACP server
  + `ACPHostedClient` already stub `ext_method`/`ext_notification`
  (`agents/acp/client.py:182,190`). So this is a *sanctioned extension, not a
  hack*. Server side: when the proactive/approval services have something to
  surface, emit the notification on the live ACP connection; the TUI maps it to
  a transcript line / permission modal. (The HTTP transport gets the same data
  from `GET /api/console/push-messages` it already polls — §4.2 — so the
  `TuiEvent` contract stays identical across transports.)
- **(b) plan/todo** → **no custom extension.** ACP has a **native `plan`
  session-update type**; we just *emit* it from the prompt loop by wiring
  QwenPaw's existing plan broadcast (`plan/broadcast.py`) onto it. If a client
  doesn't render plans, it degrades to text.

**One caveat:** ACP extensions are client-specific — Zed won't render
`qwenpaw/push_message`. That's fine (our TUI is the consumer), but the server
must **advertise the extension capability in `initialize`**
(`agents/acp/server.py:439`) and **degrade gracefully** for non-QwenPaw clients
(no proactive pushes if the client didn't negotiate the capability).

**Net:** no protocol fork and no fighting ACP. Tiers 1–2 (every command + every
in-turn primitive) are already covered because QwenPaw funnels commands through
the prompt path the ACP server drives. The only additive work is **two
`ext_notification` messages** for server-initiated events and **emitting ACP's
native `plan` update** — all small, additive, and behind capability negotiation.
This is folded into §6.2 item 3 (previously "likely zero changes" — now: zero for
Tiers 1–2, plus these scoped Tier-3 additions).

---

## 5. User experience

### 5.1 Launch

```
$ qwenpaw
```

- First run with no model configured → the TUI shows an inline setup card and
  hands off to the existing provider wizard (`qwenpaw models` /
  `init_cmd` helpers) rather than erroring.
- Otherwise → full-screen chat opens on the active agent's most recent session
  (or a fresh one). A one-line header shows agent name, model, and session id.

`qwenpaw` with an existing subcommand (`qwenpaw app`, `qwenpaw cron …`) keeps
today's behavior — only the **no-subcommand** case changes (today it prints
help; see `main.py:157`). `qwenpaw --help` still prints help. A `qwenpaw chat`
(singular, interactive) alias is also added so the behavior is discoverable and
scriptable, with `qwenpaw chats` (plural CRUD) untouched.

### 5.2 The screen

```
┌ QwenPaw · agent: default · qwen-max · session a1b2c3 ──────────── ⏺ ready ┐
│                                                                            │
│  you  ▸ summarize today's unread newsletters and draft a reply            │
│                                                                            │
│  qwenpaw                                                                    │
│  Here's what I found across your 3 newsletter sources…                     │
│  ┌ 🔧 read_inbox (channel=feishu) ───────────────── done 0.8s ┐           │
│  │ 12 messages, 3 unread                                       │           │
│  └─────────────────────────────────────────────────────────────┘          │
│  • **Stratechery** — …                                                     │
│  ▌ (streaming…)                                                            │
│                                                                            │
├────────────────────────────────────────────────────────────────────────── ┤
│ › type a message  (/ for commands · ⏎ send · esc interrupt · ⌃c quit)     │
└────────────────────────────────────────────────────────────────────────────┘
```

- **Instant first frame** (Hermes pattern): paint the header/banner immediately
  on launch, before the transport finishes connecting, so `qwenpaw` feels
  instant; alternate-screen differential rendering → no flicker mid-stream,
  clean scrollback on exit (native in Textual).
- **Three distinct stream lanes** (Hermes pattern), not one blob:
  **message** (visible answer, streaming markdown via Textual v4 `Markdown`),
  **thinking** (dimmed/collapsible), and **tool output** — mapped from ACP
  `AgentMessageChunk` / `AgentThoughtChunk` / tool-update events respectively.
- **Tool calls render as collapsible inline panels** (spinner → result), driven
  by ACP tool-call start/update events; `/details` toggles their verbosity.
- **Status bar**: model, session, live token usage (from the ACP usage events).

### 5.3 Slash commands (in-TUI)

The ACP server already routes `/clear`, `/compact`, etc. via the agent; the TUI
adds a client-side palette that maps to ACP/HTTP calls:

| Command | Action |
|---|---|
| `/model` | picker → `set_session_model` (ACP) |
| `/agent` | switch active agent → restart transport with `--agent` |
| `/new`, `/sessions`, `/resume` | session lifecycle (ACP `new/list/resume`); live switcher also on `Ctrl+X` (Hermes pattern) |
| `/agents` | live subagent tree (QwenPaw has `spawn_subagent`) with kill/pause (Hermes pattern) |
| `/details` | toggle tool-call verbosity (Hermes pattern) |
| `/clear`, `/compact` | forwarded to agent (already handled) |
| `/approve-all` | toggle bypass-permissions mode (ACP config option, already in server) |
| `/web` | open the web console (`qwenpaw app` + browser) for rich tasks |
| `/quit` | exit |

Slash entry shows a **floating autocomplete with descriptions**, and `/help` is a
categorized, arrow-navigable overlay (Hermes pattern).

### 5.4 Permissions & interrupts

- A tool needing approval → **modal overlay** (not an inline prompt; Hermes
  pattern) listing the agent-provided options; the choice is returned through
  `request_permission`. This is the same flow Zed gets. Where the agent
  distinguishes request *kinds* (approve / elevated-sudo / secret-input /
  clarify), the modal styles them distinctly rather than as one generic yes/no.
- `Esc` during a turn → `interrupt()` → ACP `cancel` (`server.py:724`).
- `Ctrl+C` → graceful shutdown: close session, terminate the `acp` subprocess.

### 5.5 Non-interactive / piping (bonus, cheap)

`qwenpaw chat -p "one shot prompt"` (or stdin pipe) runs a single turn through
the same transport and prints plain/markdown to stdout, no full-screen UI —
useful for scripts and matches the muscle memory of other agent CLIs.

---

## 6. Code changes required

### 6.1 New files

```
src/qwenpaw/cli/tui/
  __init__.py
  app.py            # Textual App: layout, key bindings, event pump
  widgets/
    chat_log.py     # streaming markdown transcript + message bubbles
    tool_panel.py   # collapsible tool-call widget
    prompt_input.py # multiline input, slash palette, history
    status_bar.py   # model / session / tokens
    permission_modal.py
  transport/
    base.py         # TuiTransport Protocol + TuiEvent union
    acp.py          # AcpTransport: spawn `qwenpaw acp`, ACP client glue
    http.py         # HttpTransport: POST /api/console/chat + SSE
  normalize.py      # ACP updates  → TuiEvent ; SSE events → TuiEvent
  launch.py         # entry: pick transport, preflight (model configured?)
```

`transport/acp.py` reuses `acp.contrib.session_state.SessionAccumulator` /
`ToolCallView` and mirrors `src/qwenpaw/agents/acp/client.py` rather than
reinventing accumulation. `normalize.py` for the SSE side reuses the event
shapes the console already parses.

### 6.2 Edits to existing files

1. **`src/qwenpaw/cli/main.py`**
   - Make the bare group invoke the TUI when no subcommand is given:
     in the `cli()` callback, if `ctx.invoked_subcommand is None`, call
     `from .tui.launch import run_tui; run_tui(ctx, remote=..., agent=...)`.
   - Register a lazy subcommand `chat` →
     `("qwenpaw.cli.tui.launch", "chat_cmd", ".tui")` for the explicit/`-p`
     forms. (Plural `chats` stays as-is.)
   - Add top-level options consumed by the TUI: `--remote URL` (force HTTP),
     `--agent ID`, `-p/--prompt TEXT` (one-shot), `--no-tui` (escape hatch →
     old help). Keep lazy-loading so importing Textual never slows other
     commands (Textual import lives behind the lazy `tui` module).

2. **`pyproject.toml`**
   - Add deps: `textual>=4.0` (streaming markdown), and `rich` explicitly
     (already transitively present via Textual; pin for the renderer used by
     `startup_display.py`). No backend deps change — `agent-client-protocol`,
     `httpx`, `questionary` already present.
   - Optionally a `[project.optional-dependencies] tui` extra if we want to keep
     the base install lean, with a friendly "run `pip install qwenpaw[tui]`"
     message if Textual is missing. (Recommendation: include by default —
     Textual is pure-Python and small.)

3. **`src/qwenpaw/agents/acp/server.py`** — *zero changes for Tiers 1–2; scoped
   additions for Tier 3 (see §4.3).* The client-driven flows we need
   (`list_sessions`, `resume_session`, `set_session_model`, the
   bypass-permissions config option, and **all slash commands via `prompt()`**)
   already work — they exist for Zed. Add only:
   - two **`ext_notification`** emitters (`qwenpaw/push_message`,
     `qwenpaw/approval_pending`) for proactive/idle messages and cross-session
     approval alerts, hooked to the existing proactive/approval services;
   - emit ACP's **native `plan` session-update** by wiring `plan/broadcast.py`
     into the prompt loop;
   - **advertise these as a capability in `initialize`** (`server.py:439`) so
     non-QwenPaw clients (Zed) negotiate them off and degrade gracefully.

4. **Docs/README** — add a "Use it in your terminal" section: `qwenpaw` →
   chat; document `--remote`, `-p`, and the "drive with Toad/Zed via
   `qwenpaw acp`" power-user note.

### 6.3 Tests

- `transport/acp.py`: spawn a fake ACP agent (a stub `Agent`) and assert
  TextDelta/ToolCall/Permission events normalize correctly; assert subprocess
  is reaped on close. (Mirror existing ACP tests under `tests/`.)
- `transport/http.py`: mock SSE stream → assert same `TuiEvent` sequence as ACP
  for an equivalent turn (proves UI-layer transport parity).
- `normalize.py`: table-driven unit tests for every ACP update / SSE event type.
- `launch.py`: bare `qwenpaw` with no model → routes to setup, not a crash;
  `--no-tui` prints help; `qwenpaw app` etc. unaffected (Click invocation test).
- Snapshot test of the Textual app with `pytest` + Textual's `Pilot` for key
  bindings (send, interrupt, slash palette, permission modal).

---

## 7. Rollout plan

1. **M1 — Embedded ACP MVP:** bare `qwenpaw` opens a chat that streams text +
   tool panels against `qwenpaw acp`; send / interrupt / quit. Ship behind
   `qwenpaw chat` first, then flip the bare-command default once stable.
2. **M2 — Parity features:** permission modal, model/session slash commands,
   token status bar, `/clear` `/compact`, `-p` one-shot + stdin pipe.
3. **M3 — HTTP transport:** `--remote` / auto-attach to a running server; prove
   transport parity via the shared `TuiEvent` tests.
4. **M4 — Polish:** themes, image/file attachment blocks, `/web` handoff, resume
   picker, Windows/conpty validation, docs + README, optional `[tui]` extra
   decision.

## 8. Risks & mitigations

- **Textual import cost on every `qwenpaw` call** → keep it behind the lazy
  `tui` module; non-TUI subcommands never import it.
- **Subprocess lifecycle on Windows** → use `acp`'s own client runner for
  process/transport handling; add a reaper + timeout on close; CI smoke test on
  Windows.
- **ACP gaps for a *personal-assistant* (vs coding) UX** (e.g. channel/cron
  surfaces) → those stay in the web console; the TUI links out via `/web`. The
  TUI targets the conversational core, which ACP models well.
- **Two transports drifting** → enforced by the shared `TuiEvent` contract and
  the parity test in §6.3.

---

## 9. Why this satisfies the goal

- **Reuse research done:** ACP is the one genuinely reusable asset; QwenPaw
  already implements it. Toad/OpenCode/Codex/Crush/**Hermes Agent** evaluated and
  rejected as bundled frontends, with reasons. Hermes independently validates our
  architecture (thin client ↔ stdio JSON-RPC ↔ swappable backend) — we reject its
  TS/React/Ink *code* (Node runtime + bespoke protocol) but borrow its UX
  patterns (instant first frame, three stream lanes, typed permission modals,
  subagent tree, floating slash autocomplete).
- **Full implementation approach:** native Textual ACP-client TUI + optional
  HTTP transport, exact files and edits listed (§6), grounded in real symbols
  (`QwenPawACPAgent`, `ACPHostedClient`, `/api/console/chat`, `main.py` group).
- **User experience:** `qwenpaw` → streaming chat with tool panels,
  permissions, slash commands, model/session switching, one-shot mode (§5).
```
