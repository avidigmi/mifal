# Fleet contracts

Read when writing or checking anything the fleet shares across the transport
seam (ADR-0002): a doorbell ring, a brief, a receipt, or the team record.

The default representation above the transport seam is agent-agnostic
Markdown on disk. An existing native task record or Pi journal can hold the
same fields when the brief declares that surface; a read-only task retains
its receipt in the conversation and reports the persistence limit. These
contracts fix the shapes so any member — any model, any channel — can read and
write them, and so the lead can check them mechanically instead of by reading
transcripts. Transport specifics (pane ids, event types, socket payloads)
never appear here; they live below the seam in the adapter.

## The contracts

- [doorbell.md](doorbell.md) — `DONE / HELP / HELD / STOPPED` + task id +
  payload: one line that is both a pane message a human can watch and a
  durable record any agent can parse.
- [brief.md](brief.md) — the task file's frontmatter (stable task id, attempt
  id, owner, latitude, return path) and required sections. Formalizes the
  brief template in [communication](../communication.md); it does not replace
  the guidance there.
- [receipt.md](receipt.md) — the return: explicit `Closes:` ids, claims with
  statuses, sidecar artifacts. Delivery is proven by receipts, never by
  transport return codes.
- [team-record.md](team-record.md) — the five record shapes (state, agreement,
  people, lessons, decisions) and who may write each. Extends
  [team-memory](../team-memory.md).
- [compliance.md](compliance.md) — tiered AGENTS.md compliance: the tier
  classification, the per-task report, the dispatch-gate decision, and the
  record landing (report sidecar + append-only history).

## The validator

`scripts/validate_contracts.py` (Python, stdlib only) checks doorbell lines,
briefs, and receipts:

```sh
validate_contracts.py doorbell --parse "DONE 7 .team/tasks/7.md"
validate_contracts.py brief .team/tasks/7.md
validate_contracts.py receipt .team/tasks/7.md
validate_contracts.py brief .team/tasks/7.md --matt
validate_contracts.py receipt .team/tasks/7.md --matt
validate_contracts.py workflow .team/state.md --gate entry
validate_contracts.py selfcheck   # runs every fixture; exit 1 on surprise
```

Errors are actionable: each says what is wrong and shows the fix. Good and bad
fixtures for every shape live in `scripts/fixtures/`; the `record/` fixtures
are worked examples (the record shapes are conventions, checked by review) —
only doorbell, brief, and receipt are machine-checked.

Matt's opt-in checks use the [engineering phase record](../../../engineering-workflow/references/phase-record.md).
They check recorded metadata, read declarations, checklist shape and snapshot
identity; the lead still verifies selection and semantic compliance. General
brief/receipt behavior remains unchanged, including legacy adapter callers.
`workflow --gate review|accept` checks the corresponding boundary;
`python3 scripts/test_workflow_contracts.py` exercises its public CLI seam.

## Standing rules

- **One writer per artifact.** The lead writes state, agreement, people,
  lessons, decisions, and the brief; the owning member appends the return.
- **The declared receipt is authoritative; the signal is a hint.** Write
  the file or existing native/journal return first, signal at most once.
- **Attempt ids protect corrections.** A correction gets its own attempt id
  (`7.2`) in the brief, the doorbell, and the return, so a late `DONE 7` can
  never close attempt `7.2`.
- **Records are versioned where supported.** File-based team records live
  in the project Git tree; commit only within existing authorization. Native
  records preserve task/attempt identities. Session-only records require
  authorized persistence before a later run can rely on them.
