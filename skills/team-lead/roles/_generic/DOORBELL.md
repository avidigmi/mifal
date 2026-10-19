# Doorbell instructions — generic member

The doorbell is one line, both a pane message and a durable record. The full
contract is `references/contracts/doorbell.md`; this is the member's cheat
sheet.

```
<TYPE> <task> <payload>
```

| When | Ring | Payload |
|------|------|---------|
| Return written | `DONE <task> <return-path>` | the artifact path, one token, nothing else |
| Blocked, lead can unblock | `HELP <task> <reason>` | one specific question |
| Blocked on the human | `HELD <task> <waits-on>` | the approval you wait on |
| Stop rule hit | `STOPPED <task> <why>` | why; the writeup is in the return file |

Rules that matter most:

- **Write the return file first, ring once.** The file is authoritative; the
  ring is a hint pointing at it.
- **Ring the attempt you are closing.** On a correction, ring the attempt id:
  `DONE 7.2 .team/tasks/7.md`.
- **No prose in a DONE.** Prose belongs in the return file.

Check any line before ringing:

```sh
python3 ~/.agents/skills/team-lead/scripts/validate_contracts.py doorbell --parse \
    "DONE 7 .team/tasks/7.md"
```
