# Tool expectations — generic member

The generic role expects no specific tools beyond the fleet baseline (file
read/write, shell). Your brief names any tool the task needs.

Before starting a task:

1. Check the lead's picture of everyone's tools:
   `python3 ~/.agents/skills/team-lead/stewardship/steward.py ledger show`
   — find your own row in the output.
2. If the brief needs a tool the ledger doesn't show for you, verify whether
   you actually have it. If yes, tell the lead so the ledger is corrected; if
   no, ring `HELP <task> missing tool <name>`.

After finishing a task, the return's tool ledger section lists what you used,
what you considered and rejected, and why — the lead folds it into the
stewardship registries.
