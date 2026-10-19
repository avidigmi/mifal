"""The README's "What gets written" table, built from a dry run and the install it previews.

    python3 tests/helpers/readme_writes.py   rewrites the table in README.md from a fresh disposable HOME
"""
import re
import sys
from pathlib import Path

BEGIN, END = "<!-- writes:begin -->", "<!-- writes:end -->"
RULES = (
    (".claude/CLAUDE.md", "A Marked block that imports the Guide"),
    (".codex/AGENTS.md", "A Marked block holding the Guide"),
    (".factory/AGENTS.md", "A Marked block holding the Guide"),
    (".pi/agent/AGENTS.md", "A Marked block holding the Guide"),
    (".config/opencode/AGENTS.md", "A Marked block holding the Guide"),
)


def table(home: Path, paths) -> str:
    """Markdown rows for the HOME-relative paths a dry run planned, read from the HOME it then installed."""
    files = {p for p in paths if (home / p).is_file() and not (home / p).is_symlink()}
    engine = sorted(p for p in files if p.startswith(".agents/mifal/"))
    pool = sorted(p for p in files if p.startswith(".agents/skills/"))
    skills = {p.split("/")[2] for p in pool}
    links = sorted(p for p in paths if p.startswith(".claude/skills/") and (home / p).is_symlink())
    rows = [
        ("~/.agents/mifal/", f"The engine: the `mifal` command and its library, the Herdr adapter and tools, the "
                             f"Guide (`AGENTS.md`) and the Manifest; {len(engine)} files"),
        ("~/.agents/skills/", f"{len(skills)} skills, {len(pool)} files"),
    ]
    if ".local/bin/mifal" in paths:
        rows.append(("~/.local/bin/mifal", "A link to the engine's `mifal` command"))
    for rel, what in RULES:
        if rel in paths:
            rows.append(("~/" + rel, what))
            if rel == ".claude/CLAUDE.md" and links:
                rows.append(("~/.claude/skills/", f"{len(links)} links, one per skill, into `~/.agents/skills/`"))
    return "\n".join(["| Where | What Mifal writes |", "|---|---|"] + [f"| `{w}` | {what} |" for w, what in rows])


SECTION = re.compile(re.escape(BEGIN) + r"\n(.*?)" + re.escape(END), re.S)


def section(readme: str) -> str:
    found = SECTION.search(readme)
    return found.group(1).rstrip("\n") if found else ""


def replaced(readme: str, table_text: str) -> str:
    if not SECTION.search(readme):
        raise SystemExit("README.md has no writes markers")
    return SECTION.sub(lambda m: f"{BEGIN}\n{table_text}\n{END}", readme, count=1)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_readme import Readme

    case = Readme("test_the_writes_table_matches_a_dry_run_with_all_five_harnesses")
    case.setUp()
    try:
        generated = case.generated_table()
    finally:
        case.doCleanups()
    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text()
    readme.write_text(replaced(text, generated))
    print("README.md writes table regenerated")


if __name__ == "__main__":
    main()
