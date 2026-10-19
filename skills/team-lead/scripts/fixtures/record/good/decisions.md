# Decisions

## D1 — receipts over transport return codes

- Decision: delivery of a brief is proven by the receipt on disk
  (`Closes:` id, claims, sidecars), never by a send's return code.
- Why: a timeout once masked a delivered return; we double-dispatched
  task 5.
- Evidence: task-5 record; ADR-0002.
- Challenged by: kai — "checking files is slower than a return code";
  settled by the lead's 1 s reconcile floor.
- Reopen if: the channel gains signed, durable delivery acknowledgments.
