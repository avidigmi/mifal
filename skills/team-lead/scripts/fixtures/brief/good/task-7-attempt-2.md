---
task: 7
attempt: 7.2
owner: kai
latitude: do-tell
kind: implementation
return: .team/tasks/7.md
---

# Task 7, attempt 2 — the soak ran against the wrong environment

## Why

Attempt 7.1 passed its checks but against a local runner, not CI; the flake
still blocks Friday's release.

## Outcome

The fix verified against the CI environment, not a local stand-in.

## Pointers

- Attempt 7.1's return is in `.team/tasks/7.md`; its soak log is
  `.team/tasks/7/soak.txt`.

## Done means

- ci-soak-green: 50 consecutive CI runs pass with `CI=1` set
- fix-intact: the 7.1 fix is still present at the checked revision

## Stop rule

One more failed approach and stop; write up both attempts.

## Return

Append the return for this attempt to `.team/tasks/7.md` as its own
section, then ring: `DONE 7.2 .team/tasks/7.md`.
