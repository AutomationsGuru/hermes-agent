# Hermes Agent — Fork-Local Overlay (LOCAL_AGENTS.md)

Generated: 2026-06-10 (Wave 5 of DOX distribution; fork-local overlay only)

This is the **fork-local overlay** for the local Hermes Agent checkout at `C:/Users/RDP/.hermes/hermes-agent/`. It is **not** upstream content. It governs fork-local decisions, fork-local plans, and fork-local experiments layered on top of the upstream `NousResearch/hermes-agent` source.

The upstream `AGENTS.md` (58 KB, 1172 lines) is the binding contract for the **codebase**. This file is the binding contract for **the fork** — what Matthew's local checkout adds over upstream, what the dirty working tree is doing, and what the local conventions are.

## Upstream vs fork-local (read this first)

| Concern | Owner | Document |
|---|---|---|
| Codebase structure, run_agent.py, cli.py, hermes_state.py, hermes_cli/, agent/, tests/, etc. | Upstream (NousResearch) | `AGENTS.md` (root) |
| Project structure conventions, TypeScript style, AIAgent class, file dependency chain | Upstream (NousResearch) | `AGENTS.md` (root) |
| Fork-local in-flight work (session-stability improvement, audit_empty_sessions.py, etc.) | This file (fork-local) | `LOCAL_AGENTS.md` (this file) |
| Local plans, future plans, fork-local skill choices | This file (fork-local) | `LOCAL_AGENTS.md` + `plans/AGENTS.md` + `.plans/AGENTS.md` |
| Fork-local rules about what may / may not be committed | This file (fork-local) | `LOCAL_AGENTS.md` (this file) |
| Upstream pull workflow, conflict resolution, dirty-tree rules | This file (fork-local) | `LOCAL_AGENTS.md` (this file) |

If a topic is in the upstream root `AGENTS.md`, defer to that document. If a topic is fork-local, it lives here. There is no third tier.

## Working tree state (updated 2026-06-10, session-stability continuation)

The in-flight **session-stability improvement** work was checkpointed to
branch **`session-stability-improvement`** (commit `c9f49b694`, based on
`main` `c6de2bec9`) — the original 25-file change set (core flush/projection
fixes, cross-process file locks per the multi-agent race audit, the plan
doc, `scripts/audit_empty_sessions.py`, tests) is COMMITTED there. The
branch is checked out with a **continuation layer uncommitted on top**:

- `agent/conversation_compression.py` — rotation-block failure-window
  hardening (cursor/flag/parent-pointer set immediately after the id swap)
- `run_agent.py` — flush cursor-beyond-list warning + clamp
- `scripts/agent_logs_bridge.py` (untracked) — Mission Control
  `agent-logs.db` writer; deployed live 2026-06-10 (128 rows, MC source
  card flipped to available)
- `tests/scripts/test_agent_logs_bridge.py` (untracked)
- `tests/test_session_stability_compression.py` — rotation-window + plugin
  (hermes-lcm shaped) engine regression tests added
- `tests/run_agent/test_compression_boundary_hook.py`,
  `tests/hermes_cli/test_config.py` — Windows test-hygiene/flake fixes
- `tests/hermes_cli/test_web_server_session_search.py` — empty-tip search
  divergence regression test
- `website/docs/` (developer-guide + reference) — docs updated to reflect
  the boundary-persistence, lineage-tip, and Windows log-rotation behavior
- `plans/session-stability-improvement-2026-06-10.md` — addendum + status

Validation state: 342 touched-suite tests pass; the only failures are 6
pre-existing environment issues (5 POSIX-on-Windows in
`tests/test_hermes_logging.py`/`tests/hermes_cli/test_config.py`, 1
machine-specific `HERMES_HOME` path) — identical on `main`. `git merge-tree`
confirms the branch merges cleanly with `origin/main` (incl. the
`hermes_cli/auth.py` overlap with Antigravity PR #3). Landing
(commit/rebase/PR) is approval-gated on Matthew. **Do not** run
`git pull upstream main` while this work is unlanded — it will conflict.

## Fork-local contract

- **No writes into upstream-resync paths.** The following subdirectories are upstream-governed; do not add child AGENTS.md, do not add fork-local scripts, do not commit fork-local changes there:
  - `agent/`, `acp_adapter/`, `acp_registry/`, `apps/`, `assets/`, `cli.py` (root), `cron/`, `datagen-config-examples/`, `docker/`, `docs/`, `gateway/`, `hermes_cli/`, `hermes_logging.py` (root), `hermes_state.py` (root), `hermes_time.py` (root), `infographic/`, `locales/`, `nix/`, `node_modules/`, `optional-mcps/`, `optional-skills/`, `packaging/`, `plugins/`, `providers/`, `run_agent.py` (root), `scripts/` (when scripts are upstream-only; see below), `skills/` (upstream skills only; fork-local skills go in `optional-skills/` or a sibling), `tests/`, `tools/` (upstream tools only), `tui_gateway/`, `ui-tui/`, `web/`, `website/`, `__pycache__/`, `venv/`, `hermes_agent.egg-info/`, `batch_runner.py` (root), `cli-config.yaml.example` (root), `constraints-termux.txt` (root), `Dockerfile` (root), `docker-compose.yml` (root), `flake.nix` (root), `flake.lock` (root), `hermes` (root wrapper), `hermes_constants.py` (root), `hermes_bootstrap.py` (root), `hermes-already-has-routines.md` (root).
- **Fork-local surfaces** (where this fork is allowed to add or change things without conflicting with upstream):
  - `LOCAL_AGENTS.md` (this file)
  - `plans/` — in-flight planning and improvement plans
  - `.plans/` — future planning and parked ideas
  - `scripts/audit_empty_sessions.py` — fork-local audit script (committed on the session-stability branch)
  - `scripts/agent_logs_bridge.py` — fork-local bridge that materializes `~/.hermes/agent-logs.db` for Mission Control (manual run or `--no-agent` cron)
  - `tests/test_session_stability_compression.py` — fork-local test (committed on the session-stability branch)
  - `tests/scripts/test_agent_logs_bridge.py` — fork-local bridge tests
  - other untracked / fork-local files added during the session-stability work
- **When upstream updates land**, the rebase workflow is: (1) commit fork-local changes onto a fork-local branch, (2) `git fetch upstream`, (3) `git rebase upstream/main`, (4) resolve conflicts favoring the upstream root `AGENTS.md` and Python sources, (5) re-apply this overlay. Do not run a rebase while the working tree is dirty without an explicit fork-local snapshot first.

## DOX tree walker

- Upstream: read `AGENTS.md` (root) for the upstream codebase contract.
- Fork-local: read this file for the fork's contract, then `plans/AGENTS.md` for in-flight work, then `.plans/AGENTS.md` for parked ideas.
- The two layers do not nest; they coexist. Workers that touch upstream source code should walk upstream. Workers that touch fork-local surfaces should walk fork-local. Workers that touch both (e.g., a fork-local change to a Python file that upstream also tracks) should walk both and capture the cross-boundary decision in a plan or decision doc.

## Approval gates

- Pulling `upstream main` is approval-gated. Confirm the working tree is captured (commit or stash with a fork-local branch), confirm the session-stability work is at a checkpoint, and confirm any new upstream changes do not conflict with the in-flight plan.
- Pushing `origin main` is approval-gated. The fork is local-first by default.
- Running the upstream test suite in this checkout is approval-gated. The upstream test suite is calibrated for upstream's clean state; running it against a dirty fork tree will produce noise.
- `git clean -fd`, `git reset --hard`, force-checkout: all approval-gated.

## Sensitive-data rules

- The local checkout may load credentials from `~/.hermes/secrets/`, `~/.hermes/auth.json`, or `~/.hermes/mcp-tokens/`. It must not write to them.
- Test fixtures and snapshots are state-only and never vendored. Sanitize before referencing.
- No real client data, no real tenant data, no real ShareGate AppData, no real per-tenant usage in this checkout.
- The audit script `scripts/audit_empty_sessions.py` may read `~/.hermes/state.db` (sessions); it must not print full session content.
- The bridge script `scripts/agent_logs_bridge.py` reads `state.db` read-only across lanes and writes only `~/.hermes/agent-logs.db`; it exports session TITLES only — never message content.

## Cross-repo references

- Upstream: `https://github.com/NousResearch/hermes-agent`
- Local origin: `https://github.com/AutomationsGuru/hermes-agent.git`
- Local control plane: `C:/Users/RDP/Projects/agent-os-project-packages/AGENTS.md`
- DOX tree alignment audit: `C:/Users/RDP/Projects/agent-os-project-packages/docs/agent-os/dox-tree-alignment-audit-2026-06-10.md`
- The local Hermes Agent runtime contract (separate from this fork): `C:/Users/RDP/.hermes/AGENTS.md`

## Validation

After any fork-local change, verify the overlay still describes the working tree:

```bash
git status --short --untracked-files=all
```

If a fork-local file referenced in this overlay is no longer in the working tree, update this file. If a new fork-local file appears, decide whether to add it to this overlay.
