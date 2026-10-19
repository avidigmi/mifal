# Tool expectations — reviewer

Expected for this role:

- **git diff / log** — resolving and walking the review range
- **File read/search** — reading the code under review and the standards it
  is checked against

Reviews are read-only by default: if the brief does not name a write target,
you produce findings only.

Before starting:

1. `python3 ~/.agents/skills/team-lead/stewardship/steward.py ledger show`
   — the lead's picture of everyone's tools; find your own row in the output.
2. If you cannot resolve the review target (missing refs, no repo access),
   ring `HELP <task> <what's missing>` immediately — don't review the wrong
   diff.

After finishing: the return's tool ledger section lists what you used —
including any tool that would have changed the verdict had you had it.
