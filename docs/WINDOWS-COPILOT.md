# Unsupported Windows/Copilot adaptation

This checkout contains an experimental, local **single-agent** path for Windows
and GitHub Copilot CLI. It is not upstream Windows support or a port of Herdr.
The macOS installer and the 39 bundled skills are unchanged.

## Requirements and preview

Use Python 3.9 or later, Git, and a Copilot CLI version that discovers personal
skills in `~/.agents/skills` and user-level modular instructions in
`~/.copilot/instructions/*.instructions.md`. A relocated `COPILOT_HOME` is
refused. No dependencies are downloaded and no PATH or agent settings are changed.

From the reviewed source checkout in PowerShell:

```powershell
python maint\windows_copilot.py install --dry-run
python maint\windows_copilot.py install
python "$HOME\.agents\mifal\windows_copilot.py" doctor
```

Review the preview before applying it. Installation writes:

| Location under your home | Contents |
|---|---|
| `.agents\skills\<name>` | All 39 bundled skill directories, unchanged |
| `.agents\mifal\guide` | Original Mifal Guide |
| `.agents\mifal\tools\review-snapshot.py` | Original review identity tool |
| `.agents\mifal\third_party` | Provenance, dependency sources and licenses |
| `.agents\mifal` | VERSION, LICENSE, NOTICE, standalone lifecycle script and its manifest |
| `.copilot\instructions\mifal.instructions.md` | Guide text plus explicit Windows/solo limitations |

It does not modify personal `copilot-instructions.md`, repository rules, existing
skills, hooks, credentials, settings, or other agent installations. Existing
engine, same-name skill folders (including in `.copilot\skills`), same-name
instructions, and symbolic links or Windows reparse points along managed paths
are refused before writing. Unrelated skills and instructions remain untouched;
their semantic compatibility is not automatically checked.

## Verify discovery separately

```powershell
copilot skill list
copilot instruction list
python "$HOME\.agents\skills\team-lead\scripts\validate_contracts.py" --help
```

`doctor` hashes installed files and reports missing or modified files. It does
not start Copilot or prove discovery, invocation, independent review, test
execution, or workflow compliance. CLI discovery commands inspect the CLI's
sources, not host-projected instructions in the desktop app.

Start a fresh CLI conversation after installing instructions. Inside the CLI,
`/skills reload` and `/skills info engineering-workflow` check skill metadata.
For the desktop app, start a fresh conversation and verify it lists the skills
and can load `engineering-workflow` before claiming the app is wired. Installation
alone is not evidence that the desktop app discovered the instructions.

## Available and unavailable work

Start with `engineering-workflow`; it uses `ask-matt` to select the route, then
the applicable phase skills (for example `tdd`, `implement`, `code-review` and
`retro`). Follow the selected route rather than running all skills in a fixed
order. Use native skill loading when available; otherwise read the full skill
file. Expand the home path explicitly and use `python`, not a missing `python3`
alias. The unchanged validator remains at its original canonical pool path.

All 39 skills are copied for their references and cross-links. This does **not**
install 39 agents or make every procedure runnable on Windows. Herdr, socket
adapters, pane/team execution, shell-only helpers and other platform-specific
dependencies are unavailable. Do not execute Unix snippets blindly or install
missing tools to make a skill appear successful. Run solo-compatible phases
sequentially and label self-review as such; an actual independent-review
requirement stays blocked without an independent reviewer.

## Removal, interruption and updates

```powershell
python "$HOME\.agents\mifal\windows_copilot.py" uninstall --dry-run
python "$HOME\.agents\mifal\windows_copilot.py" uninstall
```

Removal deletes only unchanged managed files, then empty directories the
installer created. User additions survive. Modified managed paths are reported
and retained along with the manifest and lifecycle command; move those edits
aside yourself and rerun removal. There is no overwrite-edits option.

The manifest records an in-progress install before payload writes. If a write
fails, run `doctor`, preview `uninstall`, and remove the incomplete installation
before retrying. A process killed during a write can leave a partial file;
it is kept as a modified file. A stale `windows-copilot.lock` also causes refusal:
confirm the recorded process is no longer running before manually removing that
specific lock file. No automatic broad cleanup is performed.

There is no automatic updater or `mifal` command on PATH. Re-running the same
source is a read-only no-op when every installed file matches. For a different
revision, preview removal, remove unchanged files, review the new source, then
preview and install it. Do not use the macOS `mifal update` for this adaptation.

## Checks and sources

The cross-platform lifecycle regression suite uses disposable homes:

```powershell
python -B -m unittest discover -s tests -p test_windows_copilot.py -v
```

The upstream `/bin/bash tests/run.sh -q` still targets macOS and two macOS Python
installations; the focused Windows suite is not a substitute for that baseline.

Integration locations and discovery commands are documented by GitHub:

- [Copilot CLI skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)
- [Copilot CLI custom instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions)
