# `plans/` — In-Flight Fork-Local Plans

Generated: 2026-06-10 (Wave 5 of DOX distribution)

This AGENTS.md governs `C:/Users/RDP/.hermes/hermes-agent/plans` and everything below it.

## Purpose

`plans/` holds the **in-flight fork-local plans** for the local Hermes Agent fork. A plan here is a scoped improvement that is currently being executed against the fork. Once a plan is complete, its outcome is captured in a decision doc (in the Agent OS control plane under `C:/Users/RDP/Projects/agent-os-project-packages/docs/decisions/`) and the plan itself may be archived or deleted per the project's hygiene policy.

## Local contract

- Each plan is a single Markdown file. File name convention: `<slug>-improvement-YYYY-MM-DD.md` or `<slug>-YYYY-MM-DD.md` for non-improvement plans.
- A plan must have:
  - **Goal** — what the plan is trying to achieve.
  - **HERE → THERE → PATH** — current state, intended state, and the path between them.
  - **Evidence gathered** — facts on the ground that justify the plan.
  - **Acceptance criteria** — what "done" looks like.
  - **Status** — `proposed`, `in-flight`, `blocked`, `complete`, or `cancelled`.
- Plans in `plans/` are working drafts. They are not durable contracts; they are not vendored upstream; they are not loaded by the runtime.
- A plan is **complete** when its acceptance criteria are met AND a decision doc capturing the outcome is written. Move the plan to a status of `complete`; the next hygiene pass may archive or delete it.

## DOX tree walker

- Parent: `../LOCAL_AGENTS.md` (the fork-local overlay).
- Children: none. Plans are flat Markdown files. If a plan grows a sub-folder of evidence, link the evidence from the plan body, do not create a sibling AGENTS.md.

## Approval gates

- Starting a new plan is approval-gated only if the plan mutates upstream-resync paths or has cross-repo impact. Plans that are fork-local-only (touching only `LOCAL_AGENTS.md`, `plans/`, `.plans/`, or other untracked fork-local files) are within standing authority.
- Cancelling a plan is approval-gated only if the plan was approved with budget or cross-team coordination. Cancelling a fork-local-only plan is within standing authority.
- Promoting a plan's outcome to a decision doc is approval-gated for plans that bind future work. Promotion is the durable record.

## Sensitive-data rules

- Plans may reference paths, function names, test names, session IDs, and DB rows. Treat the full plan as state-only and never commit, never print, never upload.
- If a plan captures quota, token, or per-tenant usage evidence, redact before referencing it elsewhere. The plan itself is not vendored.
- A plan that includes a session ID, host name, or other identifier must redact the identifier when capturing as a decision doc.

## Cross-repo references

- Fork-local overlay: `../LOCAL_AGENTS.md`
- Upstream root: `../AGENTS.md`
- Decision docs (durable outcomes): `C:/Users/RDP/Projects/agent-os-project-packages/docs/decisions/`
- Control plane: `C:/Users/RDP/Projects/agent-os-project-packages/AGENTS.md`
