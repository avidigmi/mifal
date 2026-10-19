# Stewardship registries (spec 0001, ticket 02)

The lead's stewardship picture of what the fleet can do. Three registries, one
stdlib-only tool (`steward.py`), all plain files under version control.

## Sources of truth — reconciled, never duplicated

The generator **reads** the agency's existing configuration and reconciles
against it; it never forks it into a second hand-maintained registry:

| Source | What it contributes |
|---|---|
| `~/.agents/skills/*/SKILL.md` | What actually exists on disk (name, description) |
| `~/.agents/.skill-lock.json` (v3) | Origin, owner repo, install/update timestamps for lock-managed skills, when a lock exists |
| `stewardship/skill-scopes.json` (optional) | Mirror scopes and skill dependencies |
| `stewardship/invocation-policy.json` | Local default-auto intent (legacy allowlists remain supported) |
| Skill frontmatter and `agents/openai.yaml` | Declared per-engine invocation flags and body paths |
| `~/.agents/skills/SKILLS.md` (optional) | A catalog's curated decision + purpose text per pool skill |

## Layout

- `steward.py` — the tool (Python stdlib only)
- `overlays/skill-overlays.json` — **lead-curated** maturity/owner/notes per
  skill; the only hand-edited inventory input
- `briefs/` — member **self-reported capability briefs** (see `TEMPLATE.md`)
- `tool-ledger.json` — per-member MCPs/connectors with record + verify dates
- `generated/skill-inventory.json`, `generated/INVENTORY.md` — build output,
  committed so stewardship changes are reviewable history

## Commands

```sh
python3 stewardship/steward.py build                 # reconcile + write generated/
python3 stewardship/steward.py check                 # drift gate: HEAD blob == real tree
python3 stewardship/steward.py check --working-tree  # preparation gate: working inventory == real tree
python3 stewardship/steward.py brief add FILE        # install a member self-report
python3 stewardship/steward.py briefs                # list installed briefs
python3 stewardship/steward.py ledger add M T [--kind mcp|connector] [--note ...]
python3 stewardship/steward.py ledger verify M T     # refresh verification date
python3 stewardship/steward.py ledger show           # ledger with staleness flags
python3 stewardship/steward.py who <query...>        # skill candidates, claims and recorded tools
```

Run from the `team-lead` skill directory; all paths are absolute internally.

## Merge rules (sources stay distinguishable)

- Skill facts (origin, scopes, policy, catalog decision) come from the agency
  tree on every build. Overlay fields (`owner`, `maturity`, `notes`) win where
  set, and the applied overlay is echoed under each entry's `curated` key.
- Member briefs never touch curated fields: their claims surface under
  `selfReportedClaims` (split into `matched` vs `notInInventory`) and in
  `who` output annotated `[source: member brief]`.
- The tool ledger is lead-maintained; entries carry `recordedAt` +
  `lastVerifiedAt`, and `ledger show` marks anything older than
  `--stale-days` (default 30) as STALE.

## Stewardship workflow

1. Curate: edit `overlays/skill-overlays.json`; install briefs with
   `brief add`; record tools with `ledger add`.
2. `steward.py build`, review the diff, commit — stewardship edits are
   commits, not silent edits.
3. `steward.py check` before relying on the inventory: it compares a fresh
   reconcile against the **committed blob at HEAD** (`git show`), so a
   rebuilt-but-never-committed inventory fails the gate.
   During authorized local preparation, `check --working-tree` verifies
   the generated working copy. Passing it does not establish committed
   release freshness; the default HEAD gate remains separate. Commit only
   the intended stewardship changes when committing is authorized.
4. At entry and when scope or technology changes, inspect the complete inventory's descriptions, scopes and declared eligibility. Before briefing a task, use `who <need>` for candidate discovery (case-insensitive), then read the selected bodies. A keyword match is not demonstrated worker fit. Use `ledger show` when the task needs recorded tools.

`automaticPolicy` is local policy intent. `declaredInvocation` reports the
Claude and Codex source flags separately; absent flags use their default
eligibility. `resolvedBody` names the actual source file. `runtimeDiscovery`
stays `unverified`: this generator cannot prove a particular session sees,
loads or follows a skill. Keep methodology ownership and mirror scopes in
the selection decision. Automatic eligibility does not authorize external
actions or make every skill relevant to every task.

The vendored `stewardship/invocation_policy.py` supplies the parsing and
normalization rules for that policy file.
