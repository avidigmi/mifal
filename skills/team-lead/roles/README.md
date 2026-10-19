# Role folders (spec 0001, ticket 05)

Onboarding by role. A role folder is the context package a new member enters
with (see [GLOSSARY](../GLOSSARY.md)): onboarding pointers, a brief template
instance, doorbell instructions, and tool expectations — all referencing the
shared vocabulary in [references/contracts](../references/contracts/), never a
per-role dialect. Shipped defaults live here; projects override any part.

## Layout

- `_generic/` — the fallback folder for unknown roles (flagged to the lead)
- `researcher/`, `implementer/`, `reviewer/` — shipped defaults for the roles
  the fleet actually staffs, grounded in the
  [skill inventory](../stewardship/generated/INVENTORY.md)
- `examples/demo-project/` — a worked project override, used by the
  acceptance demos below

Each role folder holds:

| File | Purpose |
|------|---------|
| `role.json` | Machine-readable fields the resolver merges: summary, default latitude, task kind, expected skills/tools, read-first pointers |
| `ONBOARDING.md` | Read-order and first moves for a new member |
| `DOORBELL.md` | How this role rings, per the [doorbell contract](../references/contracts/doorbell.md) |
| `tools.md` | Tool expectations; checked against the [tool ledger](../stewardship/) before briefing |
| `brief-template.md` | A starter brief instance per the [brief contract](../references/contracts/brief.md), with `$placeholders` the resolver fills |

Role folders interlock with stewardship, they don't duplicate it: the folder
says what a role *expects*; the
[skill inventory and capability briefs](../stewardship/README.md) say what a
specific member *demonstrably has*. Brief a member by fit (`steward.py who`),
then onboard them through their role folder.

## Override mechanism

A project overrides a role by placing files under
`<project>/.team/roles/<role>/`:

- `role.json` — field-level override: any key present wins over the shipped
  default (values are replaced wholesale, lists included — never deep-merged)
- `ONBOARDING.md`, `DOORBELL.md`, `tools.md`, `brief-template.md` — whole-file
  replacement: the project file wins over the shipped one

Resolution is **visible, never silent**: the resolver emits a `RESOLUTION.md`
naming the source (shipped default or project override) of every field and
every file.

## The resolver

`scripts/resolve_role.py` (Python, stdlib only) merges shipped defaults with
project overrides and emits the effective onboarding for a member:

```sh
python3 scripts/resolve_role.py researcher \
    --project /path/to/project --member kai --task 7 \
    --out /tmp/onboarding-kai
```

Output directory contents:

- `ONBOARDING.md`, `DOORBELL.md`, `tools.md` — the effective files
- `starter-brief.md` — the brief template filled for this member and task;
  passes `scripts/validate_contracts.py brief`
- `RESOLUTION.md` — the resolution report (which field/file came from where)

## Unknown-role fallback

A role with no shipped folder resolves from `_generic/`: safe generic
onboarding (read the contracts, ring the doorbell, self-report a capability
brief) plus a flag in `RESOLUTION.md` and on stdout telling the lead to
either create a proper role folder or confirm the generic fit. Unknown roles
never fail hard — a new recruit must always be able to start.

## Acceptance demos (file-level, no herdr needed)

Run from the team-lead skill directory.

```sh
# 1. Known role, defaults only: resolved onboarding + valid starter brief +
#    clean doorbell parse
python3 scripts/resolve_role.py researcher --member kai --task 7 \
    --out /tmp/role-demo/kai
python3 scripts/validate_contracts.py brief /tmp/role-demo/kai/starter-brief.md
python3 scripts/validate_contracts.py doorbell --parse "DONE 7 .team/tasks/7.md"

# 2. Project override wins, resolution visible
python3 scripts/resolve_role.py researcher \
    --project roles/examples/demo-project --member kai --task 7 \
    --out /tmp/role-demo/kai-override
cat /tmp/role-demo/kai-override/RESOLUTION.md

# 3. Unknown role falls back safely, flagged
python3 scripts/resolve_role.py astrologer --member zed --task 9 \
    --out /tmp/role-demo/zed
```

## Version control

Role folders ship in the skills repo (`skills/.git`) with the rest of the
team-lead tree; edits are commits, not silent edits. Project overrides live in
the project's own team record, versioned with it.
