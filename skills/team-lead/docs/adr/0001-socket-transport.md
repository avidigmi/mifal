# ADR-0001: Socket transport for the lead's control loop

Status: accepted (prototype-verified 2026-10-05, herdr 0.9.3 / protocol 22)

## Context

The team lead needs to observe the fleet (states, panes, outputs) and dispatch control
(prompts, splits, focus, reads) continuously while the human watches the same panes. Two
transports exist: the herdr CLI (process per call) and the NDJSON Unix socket.

A prototype measured both against a live 4-pane fleet:

- **Latency**: socket mean 0.4 ms (0.3–1.6 ms) vs CLI mean 78 ms (44–351 ms) — ~200x.
  The CLI cost is process spawn; any loop above ~1 Hz pays it constantly.
- **Stability**: 30-minute subscription soak — 1784 events, 59 liveness pings,
  0 reconnects, 0 errors, 0 deaths. First protocol-22 re-test of issue #3124
  (subscription starvation, closed not_planned, never confirmed fixed): not reproduced.
- **Rendering**: socket dispatch never stole focus and never disturbed pane output; the
  socket is a control channel layered over rendering, not a replacement for it.

## Decision

1. The lead's control loop runs on the **herdr socket**. The CLI is reserved for humans
   and one-off scripts (it remains the sanctioned path where no socket exists, e.g. Windows).
2. The socket **does not replace the panes**. Panes remain the human-visible theater;
   the socket is backstage. No behavior may hide, move, or re-render what the human
   watches, beyond what the human's own commands do.
3. The adapter is a **real component, not a thin wrapper**. It owns, as invariants:
   - one request per connection; subscription connections are read-only (any request on
     them kills the stream)
   - per-pane subscription fan-out (`pane.agent_status_changed` requires explicit pane ids)
   - watchdog on event silence + `events_lost` error frames → reconnect, resubscribe,
     snapshot-reconcile on separate connections
   - bootstrap order: subscribe → buffer → snapshot → apply buffer → stream (events and
     snapshots share no sequence boundary; never replay events onto a snapshot)
   - schema-validated params (herdr silently ignores unknown params and substitutes
     dangerous defaults — we hit this: a mistyped split parameter targeted the human's
     live workspace)
   - **explicit pane ids always**; a missing injected pane id is a hard error, never a
     fallback to the focused pane
   - the draft guard before typing into any pane: agent idle ≥60 s and input box empty on
     two reads ≥10 s apart, else refuse
   - receipts over waits: delivery and completion are proven by durable artifacts, never
     by socket return codes or timeouts

## Consequences

- The event loop is hybrid: events own the loop, a polling floor (snapshot reconcile on a
  timer) guards it. A dead stream degrades to polling; it never freezes.
- The adapter must be tested as a component (failure injection: slow consumer, killed
  connection, silent stream) in addition to the end-to-end fleet seam.
- Portability risk is contained by ADR-0002: everything above the adapter speaks
  vocabulary, not herdr.
