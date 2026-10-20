# What Mifal checks

Which promises a program enforces, and which depend on the agent. The [README](../README.md) has the overview.

## What is checked, and what relies on the agent

Checked by a program, every time it runs:

- the installer, `mifal update` and `mifal uninstall`: what they write, what they keep and how they roll back, with the Manifest as the record;
- `mifal doctor`: the install's files, links and blocks, the validator's own selfcheck, the Ask Matt pin, Herdr, and the agent-browser CLI (which Mifal does not install);
- the contract validator, `~/.agents/skills/team-lead/scripts/validate_contracts.py`: a brief, a receipt or a phase record passes or is refused, when the agent or the lead runs it.

Relying on the agent following the Guide: that it reads the engineering workflow, loads the skill for each phase, runs the validator at each gate, asks you before cutting a step and reports only what its evidence shows. The validator can refuse a record; it cannot make an agent write one. Treat a session's claims as claims until you see the evidence they cite.
