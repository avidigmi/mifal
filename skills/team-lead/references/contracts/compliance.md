# The tiered compliance contract

The lead checks AGENTS.md compliance in tiers, matched to what each rule
actually protects (spec 0001, ticket 07). This contract fixes the shapes: the
rule classification, the per-task compliance report, the dispatch-gate
decision, and where results land in the [team record](team-record.md). The
checker is `scripts/check_compliance.py` (Python, stdlib only); worked
fixtures live in `scripts/fixtures/compliance/`.

## Tiers

| Tier | Protects | Enforcement |
|------|----------|-------------|
| 0 | safety hard lines (destructive actions, credentials/secrets, publish/approval reservations) | **enforced** — a violation blocks dispatch |
| 1 | project conventions | **checked** per task, reported in the task's compliance report |
| 2 | style preferences | **advised**, never blocking |

## Classification

AGENTS.md rules are prose, so classification is heuristic — and honest about
it:

- Rule statements are list items outside code fences. Rules written as prose
  (not bullets) are invisible to extraction — keep enforceable rules in list
  items.
- Shipped default patterns live in `stewardship/compliance-tiers.json`; a
  project overrides tiers with `.compliance-overrides.json` at its root:
  `{"overrides": [{"match": "<regex>", "tier": 1, "why": "..."}]}`.
- Uncertainty always resolves to the MORE conservative tier and says so: a
  rule matching several tiers takes the lowest number with a note; a rule
  matching nothing defaults to tier 1 (checked, reported) with
  `confidence: low`. No rule is ever lowered silently.
- Heuristic false positives happen (a tier-0 secrets pattern will catch
  "passwordless sudo"). They fail safe — the conservative direction — and the
  project resolves them with an override, whose `why` is recorded in every
  report.
- Resolution is visible: every rule entry carries `tier_source`
  (`patterns` | `fallback` | `override`), and an overridden rule also carries
  the `heuristic_tier` it would have gotten. Several overrides matching one
  rule all apply — the rule takes the most conservative of their tiers and
  the note names them. An override matching no rule is reported in
  `tier_config.unmatched_overrides` and warned about on stderr (a typo'd
  override must not vanish silently).
- A rule's history key is a hash of its normalized text: rewording a rule
  resets its history (the reworded rule is a new rule to the record).

## Checking

Detectors arm from rule text and run against task evidence — a JSON object of
what the lead knows: `task`, `attempt`, `member`, `files_changed`,
`commands`/`planned_commands`, `commit_messages`, `claims`
(`{"claim", "status"}`), `approved_by`. The shipped detectors:

- **forbidden-path** — negated rule naming path tokens (`.env`, `*.pem`);
  fires when a changed path matches.
- **forbidden-command** — rule names a dangerous command family (`rm -rf`,
  `push --force`, `reset --hard`, `drop table`, `git clean`). Matching is
  token-based, verb + flag independently, so argument order cannot evade it:
  `git push origin main --force`, `git push origin -f`, and
  `git reset HEAD~1 --hard` all match. The safe `--force-with-lease` does NOT
  match unless the rule names it; `rm -r dir` without a force flag does not
  match.
- **approval-required** — publish/deploy/release rule reserving approval.
  Publish-ish commands include `npm/pnpm/yarn/bun [run] publish`, `deploy*`,
  `release*`, `docker push`, `git tag`, `git push ... --tags`, plus the
  rule's own backticked command examples; fires when one ran with
  `approved_by` empty. A plain `git push` of commits is deliberately NOT
  publish-ish (only tag pushes are).
- **check-before-commit** — "run tests before committing" style rule; fires
  when work was committed with no passing claim covering the named check.
- **commit-convention** — Conventional Commits rule; fires on a
  non-conforming commit message.

Absent vs empty evidence: a field that is PRESENT but empty was checked and
found clean; a field whose key is ABSENT was never checked. An armed detector
whose input fields are all absent does not run — the rule is listed as
`uncheckable` with the detector and missing fields named, instead of silently
passing.

Tier-0 findings are `violation`s; tier-1 findings are `issue`s; tier-2 rules
are advice and never produce findings. A tier-0/1 rule no detector can check
is listed as `uncheckable` — the lead verifies it by reading evidence — never
silently dropped.

## The dispatch gate

`gate_dispatch(project, evidence)` — library function and CLI
(`check_compliance.py gate PROJECT --evidence FILE`), JSON in/out so the
socket adapter and the lead loop can call it:

```json
{"kind": "dispatch-gate", "decision": "refuse",
 "reasons": ["tier-0 violation of '<rule>' (r1): <detail>"], "report": {...}}
```

`decision` is `refuse` (CLI exit 1) when any tier-0 violation is present, and
also when the evidence carries no checkable field at all (`files_changed`,
`commands`, `planned_commands`, `commit_messages`, `claims` all absent) —
nothing was actually checked, so dispatch does not proceed on a silent clean.
Otherwise `allow` (exit 0). Tier-0 rules that went unverified (no detector,
or their evidence fields absent) are appended to `reasons` as non-blocking
`note:` entries, so a caller reading only decision+reasons still sees what
was not verified. Unreadable or invalid evidence is a usage error (exit 2,
actionable message on stderr) — never a traceback, and never an exit-1 that a
JSON-parsing caller could mistake for a refusal. A member in tier-0 violation
receives no brief until the violation is resolved. Wiring the gate into
dispatch itself is the lead loop's job (ticket 06).

## Landing in the team record

One writer (the lead), per the record's standing rules:

- `tasks/<id>/compliance-<attempt|timestamp>.json` — the full report for a
  task, a sidecar of `tasks/<id>.md` per the record shapes. Task and attempt
  ids are validated (`[A-Za-z0-9._-]`, no `..`) before becoming path
  components — evidence can never write outside the record root.
- `compliance-log.jsonl` at the record root — append-only history: one `run`
  entry per check, one `finding` entry per violation/issue, each keyed by the
  rule's stable text hash. `check_compliance.py history --record DIR` groups
  findings by rule; a rule is `REPEATED` when its findings span two or more
  distinct tasks (one task tripping two detectors is a single bad run, not a
  pattern). Malformed log lines are skipped and reported as `skipped: N`,
  never fatal.

```sh
check_compliance.py rules PROJECT [--json]
check_compliance.py check PROJECT --evidence FILE [--json]   # exit 1 on any finding
check_compliance.py gate PROJECT --evidence FILE             # exit 1 refuses dispatch
check_compliance.py record PROJECT --evidence FILE --record DIR  # exit 1 on tier-0 violations
check_compliance.py history --record DIR [--rule PATTERN] [--member NAME] [--json]
check_compliance.py selfcheck
```

`check` exits 1 when the report holds any finding (violation or issue);
`record` exits 1 on tier-0 violations only; both exit 2 on usage errors
(missing AGENTS.md, unreadable/invalid evidence, bad override file, unsafe
task/attempt id).
