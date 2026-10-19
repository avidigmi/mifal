# ADR-0002: Vocabulary transport seam (agent- and channel-agnostic records)

Status: accepted

## Context

The fleet is deliberately mixed (different agent kinds, different models), herdr is the
channel today, and the destination is a plugin-ready, remote-capable, channel-agnostic
orchestration layer. If inter-agent contracts — doorbells, briefs, receipts, capability
records, compliance reports — are written in herdr- or model-specific terms, every member
change or channel change breaks the fleet.

The team-lead skill already runs on this principle: briefs and returns are files
(`.team/tasks/7.md`, `DONE 7 .team/tasks/7.md`), the record is Markdown
(`state.md`, `agreement.md`, `people/<member>.md`, `lessons.md`), and the skill adapts to
whatever channel the team uses.

## Decision

1. **All inter-agent vocabulary is agent-agnostic Markdown on disk.** Doorbell rings,
   briefs, receipts, the skill inventory, the tool ledger, compliance reports, and
   recruitment pitches are files in the team folder, readable and writable by any model
   that can read text. No contract lives in a socket payload, a CLI flag, or one model's
   session format.
2. **The herdr socket is behind a seam.** Everything transport-specific (connection
   discipline, pane ids, event types, wait semantics) lives in the adapter and its
   schema-derived configuration. Above the seam, the lead speaks fleet vocabulary:
   members, states, briefs, doorbells, receipts.
3. **Subscribing to events is an optimization, not a contract.** A member that cannot
   subscribe (different channel, remote machine, plugin host) still participates fully
   through files plus whatever notification its channel offers. The polling floor already
   guarantees the loop works with zero events.
4. **Records are versioned** (the `~/.agents` tree is a git repository), so fleet
   composition and channel changes leave a reviewable trail.

## Consequences

- The doorbell vocabulary (DONE / HELP / HELD / STOPPED + task id + artifact path) is
  simultaneously a pane message a human can watch and a durable record any agent can
  read; state transitions observed over the socket only *annotate* it, never replace it.
- Remote machines and plugin packaging become additive work at the seam, not rewrites.
- The lead's loop code depends on file shapes, so file shapes get schema discipline
  (frontmatter fields, one writer per artifact) and tests at the fleet seam.
