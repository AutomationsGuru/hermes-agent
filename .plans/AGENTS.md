# `.plans/` — Parked Fork-Local Ideas

Generated: 2026-06-10 (Wave 5 of DOX distribution)

This AGENTS.md governs `C:/Users/RDP/.hermes/hermes-agent/.plans` and everything below it.

## Purpose

`.plans/` holds the **parked fork-local ideas** for the local Hermes Agent fork. A plan here is not yet in flight; it is a thought captured before commitment. Parking a plan is how the fork keeps "I should look at this later" from turning into a forgotten scrollback.

## Local contract

- Each parked plan is a single Markdown file. File name convention: `<slug>.md` (no date prefix; dates go in the body).
- A parked plan has a smaller contract than an in-flight plan:
  - **Idea** — what the idea is, in 1-3 sentences.
  - **Why now / why later** — why this is parked, not in flight.
  - **Trigger to un-park** — what condition would make this worth executing.
  - **Status** — `parked` (default), `claimed` (moved to `plans/`), `cancelled`.
- Parked plans are not durable contracts. They are not vendored. They are not loaded by the runtime. They exist so the next time the fork owner looks for "what was that thing I noted," there is a place to look.
- A parked plan is **claimed** by moving its file from `.plans/<slug>.md` to `plans/<slug>-improvement-YYYY-MM-DD.md` (or renaming in place) and starting the in-flight plan contract.

## DOX tree walker

- Parent: `../LOCAL_AGENTS.md` (the fork-local overlay).
- Children: none. Parked plans are flat Markdown files. If a parked plan grows a sub-folder of evidence, link the evidence from the plan body.

## Approval gates

- Parking a plan is within standing authority. It is a capture, not a commit.
- Claiming a parked plan is approval-gated only if the resulting in-flight plan mutates upstream-resync paths or has cross-repo impact. Fork-local-only plans are within standing authority.
- Cancelling a parked plan is within standing authority.

## Hygiene

- `.plans/` should be **sparse and current**. If a parked plan is no longer relevant, mark it `cancelled` and the next hygiene pass may delete it.
- A parked plan older than 6 months with no un-park trigger and no recent activity is a candidate for cancellation. Review annually.

## Sensitive-data rules

- Parked plans may reference paths, function names, or product ideas. Treat the full plan as state-only and never commit, never print, never upload.
- If a parked plan captures a sensitive insight (e.g., a security finding, a quota observation, a tenant-specific note), redact before referencing elsewhere. The parked plan itself is not vendored.

## Cross-repo references

- Fork-local overlay: `../LOCAL_AGENTS.md`
- In-flight plans: `../plans/AGENTS.md`
- Upstream root: `../AGENTS.md`
- Decision docs (durable outcomes): `C:/Users/RDP/Projects/agent-os-project-packages/docs/decisions/`
