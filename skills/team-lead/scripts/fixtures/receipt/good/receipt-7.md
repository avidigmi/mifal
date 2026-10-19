## Return — attempt 7.1

Closes: 7

### Result

Token refresh raced test teardown; fixed by awaiting the refresh in
`afterEach`. Final commit c3d4e5f on `main`.

### Claims

- failure-reproduced: passed (rev a1b2c3d, log .team/tasks/7/repro.txt)
- fix-verified: passed (rev c3d4e5f, 50/50 runs, .team/tasks/7/soak.txt)
- suite-green: passed (rev c3d4e5f, CI run 881)

### Evidence

- `.team/tasks/7/repro.txt` — failing log captured before the fix
- `.team/tasks/7/soak.txt` — 50-run soak output
- commit c3d4e5f

### Sidecars

- .team/tasks/7/repro.txt
- .team/tasks/7/soak.txt

### Limits

Ordering-only flakes not exercised; no load test run. No cleanup owed.

### What I wish the brief had said

That the soak runner needs `CI=1` to match the failing environment.

### Lesson proposed

When a flake is timing-shaped, capture a soak log before fixing.
