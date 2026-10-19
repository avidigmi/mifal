# Onboarding — generic member (fallback)

**You are on the generic fallback: your role has no shipped folder.** The
lead has been flagged (see `RESOLUTION.md`) to either create a role folder
for you or confirm this generic fit. Everything below is the fleet-wide floor
— it is safe to start from.

Paths below are relative to the team-lead skill directory
(`~/.agents/skills/team-lead/`).

## Read first, in order

1. `references/contracts/README.md` — the shared vocabulary seam and the
   standing rules (one writer per artifact; the file is authoritative)
2. `references/contracts/doorbell.md` — how to signal: `DONE / HELP / HELD /
   STOPPED <task> <payload>`
3. `references/contracts/brief.md` — the shape of the task file you execute
4. `references/contracts/receipt.md` — the shape of the return you append
5. `GLOSSARY.md` — the fleet's fixed terms

## First moves

1. Read your brief file (the lead points you at it). It names your task id,
   latitude, done-means, stop rule, and return path.
2. Check your tools against `tools.md`; if something the brief needs is
   missing, ring `HELP <task> missing tool <name>` before starting.
3. Do the work inside your latitude. When in doubt about scope, ring `HELP` —
   asking costs nothing.
4. Write the return file **first** (per the receipt contract), then ring the
   doorbell once. See `DOORBELL.md`.

## Self-report

After your first task, file a capability brief with the lead using
`stewardship/briefs/TEMPLATE.md` — it is how the lead assigns you work you
are demonstrably good at (`steward.py who`).
