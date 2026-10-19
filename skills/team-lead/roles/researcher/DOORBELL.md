# Doorbell instructions — researcher

The doorbell is one line, both a pane message and a durable record. Full
contract: `references/contracts/doorbell.md`.

```
<TYPE> <task> <payload>
```

Research-typical rings:

| When | Ring |
|------|------|
| Writeup filed | `DONE 7 .team/tasks/7.md` |
| Question is ambiguous / sources conflict | `HELP 7 sources conflict on <x>, which to trust?` |
| Finding needs the human's call | `HELD 7 needs approval to contact vendor` |
| Search strategies exhausted | `STOPPED 7 both strategies exhausted, writeup filed` |

Rules that matter most:

- **Write the return file first, ring once.** Your findings live in the file;
  `DONE` carries only the path.
- **A bounded no-result rings DONE or STOPPED with the writeup** — silence is
  the only bad outcome.
- **Ring the attempt you are closing** on corrections: `DONE 7.2 ...`.

Check any line before ringing:

```sh
python3 ~/.agents/skills/team-lead/scripts/validate_contracts.py doorbell --parse \
    "DONE 7 .team/tasks/7.md"
```
