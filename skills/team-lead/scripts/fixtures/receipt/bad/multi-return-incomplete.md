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

## Stop rule

Stop after two failed approaches and write up what you learned.

## Return

Append each attempt's return below, then ring the doorbell.

## Return — attempt 7.1

Closes: 7.1

### Result

Token refresh raced test teardown; fixed by awaiting the refresh in
`afterEach`. Commit c3d4e5f.

### Evidence

- `.team/tasks/7/repro.txt` — failing log captured before the fix

## Return — attempt 7.2

Closes: 7.2

### Result

Re-verified against the CI environment with `CI=1`. Commit e6f7a8b.

### Claims

- ci-soak-green: passed (rev e6f7a8b, 50/50 CI runs)
