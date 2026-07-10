# Analysis: Refactoring the TUI onto an In-Process QwenPaw Runtime

**Question.** How much work is it to refactor the TUI to run directly on top of the
QwenPaw runtime in a single process, instead of spawning a `qwenpaw acp` subprocess
and driving it over ACP/stdio? What are the benefits and trade-offs, and should it
happen before the official 2.0.0 release?

**Answer in one paragraph.** The refactor is medium-sized and unusually well-prepared:
the TUI was explicitly architected (PR #5032, "Phase 1") so that "a future in-process
transport can replace the subprocess behind the same seam (one-line default flip)."
A working prototype is a few days of work for someone familiar with the code; a
mergeable, cross-platform, tested PR is roughly 1.5–3 weeks (~1.5–2.5k LOC, zero UI
changes). The dominant risks are not in the transport swap itself but in moving the
full agent runtime into the terminal process (stdout/stderr discipline, event-loop
stalls, loss of crash isolation). Given that 2.0.0 GA is imminent (beta.4 shipped
2026-07-08, `2.0.0b5` staged on main) and the transport is invisible to users and
semver, the refactor should **not** land before 2.0.0 — it is the natural "Phase 2"
immediately after GA, shipped behind an opt-in flag before flipping the default.

---

## 1. Current architecture

```
┌───────────────────────┐   ACP (JSON-RPC over stdio)  ┌─────────────────────────────┐
│ TUI process           │ ───────────────────────────► │ `qwenpaw acp` subprocess    │
│ Textual PawApp        │ ◄─────────────────────────── │ QwenPawACPAgent             │
│  └ AcpTransport       │      session_update /        │  └ Workspace (full runtime: │
│     └ normalize.py    │      request_permission      │     LLM, tools, memory,     │
│        (ACP→TuiEvent) │                              │     skills, MCP, sessions)  │
└───────────────────────┘                              └─────────────────────────────┘
```

- The UI layer (`cli/tui/app.py`, 1451 lines, plus widgets) sees **only** the
  `TuiTransport` protocol (`cli/tui/transport/base.py`, 8 methods: `start`, `send`,
  `interrupt`, `list_sessions`, `load_session`, `events`, `resolve_permission`,
  `close`) and the normalized `TuiEvent` union (`cli/tui/events.py`). No ACP types
  leak into widgets — this was a deliberate design commitment.
- `AcpTransport` (`cli/tui/transport/acp.py`, 728 lines) spawns
  `python -m qwenpaw acp --local-diagnostics` with the current interpreter and speaks
  ACP to it via the `agent-client-protocol` SDK.
- The subprocess side (`agents/acp/server.py`, 1380 lines) boots a full `Workspace`
  + `AppServiceManager` (the same lifecycle the web console uses), converts
  `workspace.stream_query()` envelopes into ACP `session_update`s
  (`_EnvelopeTracker`), bridges the ApprovalService to ACP `request_permission`
  (250 ms polling loop), reports token usage, advertises slash commands, and handles
  model switching.
- Events therefore cross **two translation layers**: envelope → ACP update objects
  (server, `server.py:141-244`) → JSON-RPC → ACP objects → `TuiEvent`
  (client, `normalize.py:217-312`).

Note that ACP is used by QwenPaw in two independent roles. The `agents/acp/service.py`
/ `client.py` / `node_runtime.py` stack is the *opposite* direction (QwenPaw hosting
external ACP agents such as Claude Code / Codex for `delegate_external_agent`) and is
untouched by this refactor. The `qwenpaw acp` server must also remain regardless, as
the documented integration point for external editors (Zed, OpenCode) — the roadmap
lists agent interoperability as a strategic area. **The refactor only changes what the
TUI connects to; it deletes no ACP code.**

## 2. What the refactor would entail

The seam already exists, so the work is: build a `LocalTransport` implementing
`TuiTransport` against an in-process `Workspace`, reusing the server's logic.

| # | Work item | Basis in current code | Est. size |
|---|-----------|----------------------|-----------|
| 1 | Extract a transport-agnostic core from `QwenPawACPAgent`: workspace/app-services bootstrap (`_ensure_app_services`, `_build_bootstrap_kwargs`, `_ensure_workspace`, `_shutdown_workspace`), envelope translation (`_EnvelopeTracker`), approval bridge, usage reporting, command advertisement, model switch | `server.py:280-500, 141-244, 813-1056, 1073-1153, 1171-1221, 1302-1363` (~550 lines move) | mostly mechanical; the ACP server becomes protocol glue over the core |
| 2 | `LocalTransport`: session registry, prompt task calling `workspace.stream_query()` directly, cancel events, warmup, close/shutdown | mirrors `transport/acp.py` minus process management; permission future/queue plumbing lifts nearly verbatim from `_TuiClient` (`acp.py:197-349`) | ~450–650 new lines |
| 3 | Event translation: keep emitting `acp.schema` update objects from the shared core and feed them through the existing `normalize_update()` — zero new translation logic, and the TUI and external ACP clients stay behaviorally identical. (The alternative — a direct envelope→`TuiEvent` translator — is ~300 new lines and a permanent divergence risk; not recommended.) | `normalize.py` unchanged | ~0 |
| 4 | Process hygiene: route runtime logging to a file (today the whole subprocess stderr is redirected to `acp.log`, `acp.py:82-102`); audit every place the runtime or its child processes (Chromium via browser-use, playwright, native libs) write to fd 1/2, since any stray write now corrupts the Textual screen | new; the riskiest item, needs macOS/Linux/**Windows** validation | ~100–200 lines + 2–4 days audit/testing |
| 5 | Event-loop strategy: start with the shared Textual loop (runtime is async throughout); keep a dedicated worker-thread loop as a fallback if UI stalls appear (adds thread-safe bridging) | new | 0 (same-loop) / ~150–300 (thread bridge) |
| 6 | Wiring: transport selection in `launch.py` behind e.g. `PAW_TRANSPORT=local\|acp`, default unchanged initially | `launch.py:131-165` | ~40 lines |
| 7 | Tests: `LocalTransport` suite against a faked workspace (mirroring `test_acp_transport.py` 385 lines + `_fake_acp_agent.py` 224 lines), bootstrap-extraction tests; all existing ACP tests stay green | new fixtures needed (a `Workspace` fake) | ~600–800 lines |
| 8 | Docs: `website/public/docs/tui.*.md` "How It Works" section currently documents the subprocess architecture explicitly | doc edit | small |

**Effort estimate.** For a maintainer familiar with the codebase: working prototype in
~2–4 days (the seam makes this genuinely cheap); production-quality PR with tests and
platform validation ~1.5–3 weeks; then one beta cycle of bake time before flipping the
default. Total touched code ~1.5–2.5k lines, **0 lines in the UI layer** (by design).

## 3. Benefits

1. **Eliminates the subprocess failure-mode class.** The current transport carries
   four separate workarounds for it: stderr-pipe deadlock when Chromium floods the
   64 KB pipe (`acp.py:82-102`), a 50 MB stdio line-limit override for big tool
   payloads like screenshots (`acp.py:64-68`), recursive process-tree kill to avoid
   orphans (`acp.py:113-131`, mirroring fix #4615), and Windows cmd.exe argument
   quoting (`launch.py:66-91`). All of these disappear.
2. **Removes protocol-impedance hacks.** ACP has no agent→client request
   cancellation, so permission expiry is enforced twice with a grace window
   (`acp.py:156-175, 276-297`); `prompt()` can resolve before the last notifications
   flush, so the transport busy-polls the queue to settle (`_settle()`,
   `acp.py:558-569`); the approval bridge polls the ApprovalService every 250 ms
   (`server.py:813-839`). In-process, permissions and turn completion become direct
   awaits.
3. **One translation layer instead of two**, and no JSON-RPC serialization on the
   streaming path — a 50 MB tool result today is serialized, written through a pipe,
   and re-parsed; in-process it is passed by reference.
4. **Faster effective startup.** Interpreter spawn plus a full second import of the
   qwenpaw runtime disappears. (Workspace boot remains, so the warmup machinery is
   still wanted — see §5.)
5. **Deeper integration becomes possible.** The transport can query the session
   store directly — e.g. fixing `/resume` listing across restarts (today the ACP
   server's `list_sessions` only returns sessions created by the current process,
   `server.py:684-703`) and transcript replay on resume (the real server never
   replays history on `load_session`, `server.py:560-579`, though the ACP contract
   and the test fake support it). Model lists, richer plan/file surfaces, etc., no
   longer need ACP protocol extensions.
6. **Simpler operations**: one process to debug, profile, and monitor; no orphaned
   backends; one log stream.

## 4. Trade-offs and risks

1. **Terminal integrity (highest risk).** Textual owns the terminal. Today *nothing*
   the runtime prints can hurt the UI because the entire backend lives behind a pipe
   with stderr redirected to a file. In-process, any direct fd write — from native
   libs (onnxruntime, transformers warnings), skills, or child processes that
   inherit fds — corrupts the display. This needs OS-level fd discipline plus an
   audit of tool child-process spawning, and it is hardest to get right on Windows.
2. **Loss of crash isolation.** A native crash or OOM in the agent runtime currently
   kills only the subprocess; the TUI survives to show "Connection closed". In a
   single process it takes the UI down and can leave the terminal in a broken state
   (Python exceptions unwind cleanly through Textual; segfaults don't).
3. **Event-loop contention.** All runtime work (schedulers, memory embedding,
   tool execution) shares the render loop unless a worker-thread loop is introduced.
   Async-but-CPU-heavy or accidentally-sync code that is invisible today becomes
   visible UI jank.
4. **Dogfooding loss for the ACP server.** Every TUI user today exercises
   `qwenpaw acp` end-to-end — the same surface external editors use, and agent
   interoperability is on the roadmap. After the flip, that daily coverage is gone;
   regressions in the editor path would surface only from external users. Sharing
   the extracted core (item 1 in §2) and keeping the ACP transport in CI mitigates
   but doesn't eliminate this.
5. **Behavior-parity migration risk.** Approvals, warmup, ephemeral sessions,
   coding-mode overlays, cancel semantics — all subtle, all carried via `_meta`
   today, and all recently patched (#5443 restored ACP commands/inline approvals;
   #5892 fixed approvals/warmup — the latest commit on main). This exact subsystem
   has needed two regression-fix PRs in two months, and it sits on the flagship
   first-run path (bare `qwenpaw` opens the TUI).
6. **Two transports to maintain** until the subprocess default is retired (the ACP
   *server* remains forever regardless, for external editors).
7. **TUI process gets heavy.** Today the TUI process imports only Textual + the ACP
   client and shows the UI while the backend boots in parallel. In-process, the
   heavy imports land in the TUI process; keeping time-to-first-frame fast requires
   deferring runtime import/boot to a background task (the existing warmup UX
   already covers the waiting state).

## 5. What the refactor does *not* change

- **Workspace boot time** — the slow part of warmup is `Workspace.start()`, not the
  process spawn; the warmup machinery stays.
- **Session sharing** with the Console/Channels — that comes from the shared
  session store, not the transport.
- **The `/resume` listing and replay gaps** — these are *missing server features*,
  not subprocess limitations. Implementing them in the ACP server
  (`list_sessions` from the persistent store; history replay on `load_session`, as
  the test fake already models) fixes them for the TUI **and** external editors
  without any refactor. Worth doing first, independently.
- **Concurrent-instance semantics** — a TUI and the web app can already run two
  `Workspace` instances against the same store; that is identical in-process.
- **Total memory footprint** — roughly a wash (today's TUI process is light; the
  heavy runtime exists once either way).

## 6. A cheap middle path (optional)

The `agent-client-protocol` SDK's `run_agent(agent, input_stream, output_stream)` and
`connect_to_agent(client, input_stream, output_stream)` accept arbitrary stream pairs.
Running `QwenPawACPAgent` as an asyncio task in the TUI process over an in-memory
duplex (~150–300 lines, transport `start()` only) would eliminate subprocess
management (spawn, kill-tree, stderr deadlock, quoting) while keeping wire-format
behavior bit-identical. However, it inherits the in-process risks (§4.1–4.3) while
retaining JSON serialization and both translation layers — so it is best treated as a
de-risking experiment or test harness, not the end state.

## 7. Timing relative to 2.0.0

Evidence that GA is imminent and the project is in stabilization mode:

- Version on main is `2.0.0b5`; tags show beta.1 (Jun 26) → beta.2 (Jul 2) →
  beta.3 (Jul 7) → beta.4 (Jul 8) — a 2–5-day cadence.
- Recent main commits are release infrastructure and stabilization: release-duty
  roster and release-verify workflows, integration-test sprints, and fixes —
  including, at HEAD, a fix to this exact subsystem
  (`0459429 fix(tui): improve approvals and warmup sessions`).

**Recommendation: do not do this refactor before 2.0.0.**

- There is **no forcing function**: the transport is an internal implementation
  detail — no CLI flag, config schema, or public API changes — so semver does not
  require it to land at a major-version boundary. The seam was explicitly built so
  the flip can happen later at near-zero switching cost ("one-line default flip").
- The **risk is concentrated in the flagship entry point** during the exact window
  when the team is hardening it. A terminal-corruption or approval regression at GA
  would be far more costly than the refactor's benefits are urgent — and none of the
  benefits in §3 are user-visible enough to justify GA risk (the user-visible gaps,
  resume listing/replay, are better fixed server-side anyway, §5).
- Pre-GA, every TUI user is a daily integration test of the ACP server the project
  wants external editors to adopt; cutting that coverage right before GA is
  strategically backwards.

**Suggested sequencing ("Phase 2", post-GA):**

1. *(Independent, any time)* Implement persistent `list_sessions` and
   `load_session` history replay in the ACP server — user-visible wins for both the
   TUI and external editors, no architectural risk.
2. Land the core extraction from `QwenPawACPAgent` (§2 item 1) — a pure,
   behavior-preserving refactor that also improves ACP-server testability.
3. Land `LocalTransport` behind `PAW_TRANSPORT=local` as experimental in early
   2.1 betas; validate fd hygiene and responsiveness across macOS/Linux/Windows.
4. Only flip the default if the experimental phase clearly earns it (see §8 —
   there is a solid architectural case for keeping the subprocess boundary
   permanently); in any case keep the other transport selectable as an escape
   hatch, and keep exercising the ACP server in CI (the existing
   `test_acp_transport.py` + fake-agent harness).

## 8. Addendum: is the subprocess architecture simply better?

Beyond timing, there is a defensible position that the process boundary should be
kept permanently. Process isolation is not one guarantee but four, and each maps to
something concrete in this codebase:

1. **Terminal ownership by construction.** A full-screen TUI is a renderer over a
   serial device; any write to fd 1/2 corrupts it. Textual intercepts Python-level
   `print`, but not C-level writes from native libraries or child processes.
   QwenPaw's runtime executes *arbitrary third-party code by design* — user skills
   are the product's core extension model — plus onnxruntime, tokenizers, mss, and
   Chromium via browser-use (whose stderr flooding is already documented in
   `transport/acp.py:82-102`). A subprocess makes display integrity unconditional:
   nothing the runtime or any future skill prints can touch the screen. In-process,
   integrity is a convention every dependency and every future contributor must
   uphold.
2. **Blast-radius control.** The realistic failure modes are not only segfaults:
   OOM (the kernel kills the biggest process — better it be the backend than the
   UI), native-extension crashes that don't unwind through Python (terminal left in
   raw mode), and *soft* failures — a wedged event loop, leaked tasks, poisoned
   process-global singletons (`ProviderManager.get_instance()`,
   `get_approval_service()`, contextvars). With a subprocess, all of these are
   recoverable by respawning the backend while the TUI, transcript, and queued
   messages survive — the Jupyter "restart kernel" UX, which the current
   architecture could offer as a `/restart` command almost for free. In-process,
   the only clean recovery from corrupted global state is exiting the app.
3. **Scheduling isolation.** Two processes give true parallelism. In-process, even
   with a dedicated worker-thread loop, runtime CPU work (tokenization, embedding
   pre/post-processing, JSON of large payloads) contends with rendering for the
   GIL; a long native call that holds the GIL stalls the UI thread outright.
4. **Inherited-context control.** The subprocess is spawned with the project
   directory as cwd for Coding Mode (`launch.py`/`AcpTransport(cwd=project_dir)`),
   its own env, its own signal disposition, its own fd table — and every tool child
   it spawns inherits that context cleanly. In-process, each of these must be
   re-implemented explicitly (a process-wide `os.chdir` in a UI process is its own
   hazard).

This is also the industry-consensus shape for "thin UI + heavy, extensible engine":
LSP servers, DAP adapters, Jupyter kernels, Chrome's renderer split — and ACP itself
exists because editors want agents out-of-process. The pattern holds wherever the
engine is native-code-heavy or runs third-party code; QwenPaw's runtime is both.

The honest cost accounting cuts the same way. The subprocess's costs are mostly
**already paid, one-time fixes** (stderr redirect, buffer limits, kill-tree, quoting
— all stable and encapsulated in ~150 lines of the transport), while the recurring
cost — protocol friction per feature — is unusually low here because the ACP server
must be maintained anyway for external editors, and the extracted-core refactor
(§2 item 1) reduces that friction without moving the runtime in-process. The
in-process design's costs run the other direction: recurring, user-facing risk
(display integrity, UI stalls, unrecoverable state) that grows with every new
dependency and skill. A fully hardened in-process design — worker-thread loop,
OS-level fd redirection, wrapped child spawning, import isolation — converges on
re-implementing half a process boundary by convention rather than construction.

In-process is the right call when the runtime is small, pure-Python, async-clean,
and entirely first-party (a REPL-style tool like aider fits). QwenPaw's runtime is
none of these. **Recommendation, refined:** treat the subprocess as the durable
architecture, and spend the effort on the boundary rather than dissolving it —
persistent `list_sessions` + replay server-side, an event-driven approval bridge to
replace the 250 ms poll, protocol-level fix for the update/response ordering that
`_settle()` papers over, and optionally a backend `/restart` command that the
boundary makes cheap. The `LocalTransport` experiment (§7 step 3) is still worth
running behind a flag — it is the only way to measure the startup and integration
wins with real numbers — but the default should move only if those numbers are
compelling enough to justify giving up guarantees 1–4.

---

*Analysis based on `agentscope-ai/QwenPaw` main @ `0459429` (2026-07-09), TUI code in
`src/qwenpaw/cli/tui/`, ACP server in `src/qwenpaw/agents/acp/`, and the Phase 1
design commitments in PR #5032.*
