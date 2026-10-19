# The Guide reaches each Harness by that Harness's own mechanism

Claude Code gets a Marked block in `~/.claude/CLAUDE.md` that imports the installed Guide, because Claude resolves `@` imports in user memory and an import stays current without rewriting. Codex, Droid, Pi and OpenCode get the Guide text itself in a Marked block in their global AGENTS.md, because none of them documents imports. We rejected settings or hook edits (a SessionStart hook) for v1: they touch files that hold credentials and differ per Harness, while rules files are the documented, user-visible place for always-on instructions.

Consequence: Codex, Droid, Pi and OpenCode carry a copy of the Guide, so the doctor compares each block's recorded digest with the installed Guide.
