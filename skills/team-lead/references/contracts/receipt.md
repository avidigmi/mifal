# The receipt contract

A receipt is the member's return: the durable proof that a task came back and
what came back with it. It lives in the task file below the brief (under
`## Return`), and its results land as sidecar artifacts. Delivery is proven
by receipts, never by transport return codes — a timeout proves nothing, so
the lead reads the return file, not the transcript and not a send's exit
status. The same fields may live in an existing native task record or Pi
journal declared in the brief. For a read-only request they may remain in
the conversation, with persistence explicitly unverified. The Markdown
validator checks file representations; it does not prove native delivery.

## Shape

```
## Return — attempt 7.1

Closes: 7

### Result
### Claims
### Evidence
### Sidecars        # when artifacts were produced
### Limits
### Decision needed # when the lead must decide something
### What I wish the brief had said
### Lesson proposed # optional
```

### The `Closes:` line

Required, exactly as written: `Closes: 7` (or an attempt id, `Closes: 7.2`).
Closing ids explicitly is what lets the lead reconcile: an open attempt stays
open until its own id closes, and a late `DONE 7` can never close `7.2`.

### Required sections

- `### Result` — the finding or change, with artifact identity (commit, path,
  digest). An honest stop reports "no fix" here; that is a good receipt.
- `### Claims` — every done-means claim from the brief, one line each:
  `- <claim>: passed|failed|untested` plus the revision checked. A claim the
  receipt doesn't address is untested, never assumed.
- `### Evidence` — what was actually inspected or produced, as pointers.

### Sidecars

Results land as files next to the task file (`tasks/7/repro.txt`), listed one
path per line under `### Sidecars`. The receipt points; it doesn't paste
logs.

### Optional sections

`### Limits` (failed approaches, what's unknown, cleanup owed),
`### Decision needed` (with a recommendation),
`### What I wish the brief had said` (one line, or "nothing"),
`### Lesson proposed` ("When <trigger>, do <rule>" — the lead checks it
before it enters `lessons.md`).

## Rules

- **Receipts over return codes.** Sending, acknowledgment, completion, and
  acceptance are four different events; only the complete receipt at the
  declared surface proves delivery. A native completion signal without its
  claims and evidence remains insufficient.
- **One writer.** The owning member appends the return; the lead writes
  everything else in the record.
- **Statuses anchor to revisions.** Each claim names the revision or digest
  it was checked against, so a later change can't make old evidence look
  current.

Fixture: a well-formed receipt is
`scripts/fixtures/receipt/good/receipt-7.md`; check any return with
`validate_contracts.py receipt <path>`.
