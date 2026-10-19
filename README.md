# Mifal

Mifal is an agentic engineering factory you install into the coding agents you already use. It gives Claude Code, Codex, Droid, Pi and OpenCode one shared methodology (Matt Pocock's skills, run as an ordered workflow with evidence at every gate), a team lead that can coordinate several agents through [Herdr](https://herdr.dev), and a contract validator that checks the briefs, receipts and phase records the agents write.

Everything installs into your home folder, every write is previewed first and recorded, and `mifal uninstall` puts your files back.

## Quickstart

You need macOS, git and python3 3.9 or later (`xcode-select --install` provides both). The installer installs Herdr if it is missing, asking before it runs anything from the network.

```sh
git clone https://github.com/romiluz13/mifal.git
cd mifal
./install.sh --dry-run
./install.sh
```

`./install.sh --dry-run` lists every file, link and Marked block the install would write, and changes nothing. The install puts the `mifal` command in `~/.local/bin`; if that folder is not on your PATH, the installer prints the line to add to `~/.zprofile`. Then check the install:

```sh
mifal doctor
```

The doctor is read-only: it writes nothing, uses no network, runs none of your agents and opens no settings or credential file. Each failure it reports comes with its fix.

## First steps

Start a session in any of the agents Mifal wired, in a project folder, and ask for engineering work as you normally would. The Guide makes the agent read the engineering workflow first: it scopes the request, asks whether you want the full workflow or a cut, and works phase by phase, recording evidence. To coordinate several agents, start the session inside Herdr (`herdr`) and ask for a team; the team lead skill takes it from there.

## What gets written

Mifal writes only into your home folder, and only where the dry run says. On a machine with all five agents detected:

<!-- writes:begin -->
| Where | What Mifal writes |
|---|---|
| `~/.agents/mifal/` | The engine: the `mifal` command and its library, the experimental Herdr adapter and tools, the Guide (`AGENTS.md`) and the Manifest; 44 files |
| `~/.agents/skills/` | 39 skills, 241 files |
| `~/.local/bin/mifal` | A link to the engine's `mifal` command |
| `~/.claude/CLAUDE.md` | A Marked block that imports the Guide |
| `~/.claude/skills/` | 39 links, one per skill, into `~/.agents/skills/` |
| `~/.codex/AGENTS.md` | A Marked block holding the Guide |
| `~/.factory/AGENTS.md` | A Marked block holding the Guide |
| `~/.pi/agent/AGENTS.md` | A Marked block holding the Guide |
| `~/.config/opencode/AGENTS.md` | A Marked block holding the Guide |
<!-- writes:end -->

An agent is wired when its program is on your PATH or its home folder exists; `./install.sh --harness codex --harness pi` wires exactly the ones you name. Folders Mifal creates are recorded and removed on uninstall once they are empty.

Rules files are never replaced. Mifal adds only its own Marked block, and leaves the rest of the file as you wrote it. When an agent reads a file other than the one Mifal would write (an `AGENTS.override.md`, or OpenCode or Pi reading a `CLAUDE.md`), Mifal skips that agent and tells you which file it reads now, rather than hiding your file.

An existing skill or file in Mifal's way is moved to `~/.agents/mifal/backup/` and restored on uninstall. A file you edit after the install is yours: a reinstall stops before overwriting it, and `--overwrite-edits` saves a copy to `~/.agents/mifal-saved/` first.

## Update and uninstall

```sh
mifal update
mifal uninstall --dry-run
mifal uninstall
```

`mifal update` fetches in the clone you installed from, shows the incoming commits and the exact writes, and asks before it changes anything (`--yes` applies without asking). It installs exactly the revision you saw, and refuses a clone with uncommitted changes or one that is not a fast-forward of its upstream. An interrupted install or update is rolled back the next time you run `mifal` or the installer.

`mifal uninstall` removes every file and link Mifal wrote and every Marked block it added, restores what it moved aside, and keeps anything you changed since, saying so. Herdr stays installed after uninstall, because other tools may use it. If Mifal installed it with Homebrew, remove it with:

```sh
brew uninstall herdr
```

If Mifal installed it with Herdr's own script, uninstall prints the command for that route.

## What is checked, and what relies on the agent

Checked by a program, every time it runs:

- the installer, `mifal update` and `mifal uninstall`: what they write, what they keep and how they roll back, with the Manifest as the record;
- `mifal doctor`: the install's files, links and blocks, the validator's own selfcheck, the Ask Matt pin and Herdr;
- the contract validator, `~/.agents/skills/team-lead/scripts/validate_contracts.py`: a brief, a receipt or a phase record passes or is refused, when the agent or the lead runs it.

Relying on the agent following the Guide: that it reads the engineering workflow, loads the skill for each phase, runs the validator at each gate, asks you before cutting a step and reports only what its evidence shows. The validator can refuse a record; it cannot make an agent write one. Treat a session's claims as claims until you see the evidence they cite.

## Support

This matrix is provisional. A cell is filled only from a recorded live check: a first session that quotes the Guide and lists Mifal's skills, and a lead that dispatches a task to that agent through Herdr and receives its reply.

| Platform | Claude Code | Codex | Droid | Pi | OpenCode |
|---|---|---|---|---|---|
| macOS | untested | untested | untested | untested | untested |

Linux and Windows are not supported in this release. Droid's rules file location is documented by Factory, not yet confirmed live.

## Maintaining

[`MAINTAINING.md`](MAINTAINING.md) says how upstream skill, Herdr and agent changes get into Mifal, how Mifal's changes go back upstream, and how a release reaches `mifal update`.

## Credits

Mifal ships the work of others under their licences, recorded per file in [`third_party/provenance.json`](third_party/provenance.json) with the licence texts in [`third_party/licenses/`](third_party/licenses) and summarised in [`NOTICE`](NOTICE): Matt Pocock's skills, DDD, Superpowers, agent-browser, Anthropic's frontend-design, Herdr's skill, Impeccable and LottieFiles motion-design. Mifal itself is MIT ([`LICENSE`](LICENSE)).
