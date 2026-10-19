---
task: 7
attempt: 7.2
owner: kai
latitude: do-tell
return: .team/tasks/7.md
---

# Task 7 — stop the login flake

## Why

The login test fails 1 run in 5 and blocks Friday's release.

## Outcome

The cause of the flake is found and fixed; the auth suite is green.

## Done means

- failure-reproduced: the flake fails before the fix, log captured
- fix-verified: 50 consecutive passes after the fix
- suite-green: the auth suite passes at the final commit

## Stop rule

Stop after two failed approaches and write up what you learned.

## Return

Append each attempt's return below this brief as its own section, then
ring the doorbell: `DONE 7.2 .team/tasks/7.md`.

## Return — attempt 7.1

Closes: 7.1

### Result

Token refresh raced test teardown; fixed by awaiting the refresh in
`afterEach`. Commit c3d4e5f.

### Claims

- failure-reproduced: passed (rev a1b2c3d, log .team/tasks/7/repro.txt)
- fix-verified: passed (rev c3d4e5f, 50/50 runs, .team/tasks/7/soak.txt)

### Evidence

- `.team/tasks/7/repro.txt` — failing log captured before the fix
- `.team/tasks/7/soak.txt` — 50-run soak output
- commit c3d4e5f

## Return — attempt 7.2

Closes: 7.2

### Result

Re-verified against the CI environment with `CI=1`; the flake is gone.
Final commit e6f7a8b on `main`.

### Claims

- failure-reproduced: passed (rev a1b2c3d, log .team/tasks/7/repro.txt)
- fix-verified: passed (rev e6f7a8b, 50/50 CI runs, .team/tasks/7/ci-soak.txt)
- suite-green: passed (rev e6f7a8b, CI run 902)

### Evidence

- `.team/tasks/7/ci-soak.txt` — 50-run CI soak output
- commit e6f7a8b

### Sidecars

- .team/tasks/7/ci-soak.txt
