---
task: 7
owner: kai
latitude: do-tell
kind: implementation
return: .team/tasks/7.md
---

# Task 7 — stop the login flake

## Why

The login test fails 1 run in 5 and blocks Friday's release. This is the
tiebreaker for every call not covered below.

## Outcome

The cause of the flake is found and fixed; the auth suite is green.

## Pointers

- Read the deploy section of `ops.md` before planning.
- Recent CI failure logs: `.team/tasks/7/ci-failures.txt`.

## Current state

Start from `main` at rev a1b2c3d. You own `tests/auth/`; nothing else.

## Least sure of

I suspect token-expiry timing but haven't ruled out test ordering. The one
observation that settles it: do failures cluster after long idle gaps?

## Done means

- failure-reproduced: the flake fails before the fix, log captured
- fix-verified: 50 consecutive passes after the fix
- suite-green: the auth suite passes at your final commit

## Stop rule

Stop after two failed approaches and write up what you learned. Stopping
honestly is a good outcome.

## Return

Write the return in `.team/tasks/7.md` below this brief, then ring the
doorbell: `DONE 7 .team/tasks/7.md`.
