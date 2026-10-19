# Doorbell instructions — reviewer

The doorbell is one line, both a pane message and a durable record. Full
contract: `references/contracts/doorbell.md`.

```
<TYPE> <task> <payload>
```

Reviewer-typical rings:

| When | Ring |
|------|------|
| Review filed | `DONE 7 .team/tasks/7.md` |
| Target unclear / can't access | `HELP 7 cannot resolve merge-base for review range` |
| Finding needs the human's call | `HELD 7 finding touches reserved-approval area` |
| Budget spent before full coverage | `STOPPED 7 review budget spent, remainder listed in return` |

Rules that matter most:

- **Findings live in the return file, never in the ring.** `DONE` carries
  only the path.
- **Ring the attempt you are closing** on re-reviews: `DONE 7.2 ...`.
- **A clean review rings DONE too** — "no findings, coverage stated" is a
  complete receipt.

Check any line before ringing:

```sh
python3 ~/.agents/skills/team-lead/scripts/validate_contracts.py doorbell --parse \
    "DONE 7 .team/tasks/7.md"
```
