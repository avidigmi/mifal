#!/usr/bin/env python3
"""Resolve a member's effective onboarding: shipped role defaults merged with
project overrides, with the resolution made visible.

Spec 0001, ticket 05. Stdlib only. Role folders live in roles/ (shipped
defaults); a project overrides any part under <project>/.team/roles/<role>/
(role.json for field-level overrides; ONBOARDING.md / DOORBELL.md / tools.md /
brief-template.md as whole-file replacements). Override wins where they
conflict, and RESOLUTION.md names the source of every field and every file.

Usage:
  resolve_role.py ROLE [--project DIR] [--member NAME] [--task N]
                  [--title TEXT] [--return PATH] [--out DIR]

Exit codes: 0 resolved (including unknown-role fallback), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from string import Template

SKILL_ROOT = Path(__file__).resolve().parent.parent
ROLES_DIR = SKILL_ROOT / "roles"
GENERIC_ROLE = "_generic"
FOLDER_FILES = ("ONBOARDING.md", "DOORBELL.md", "tools.md", "brief-template.md")

# Bare names only: these flow into path construction (roles/<name>/) and the
# emitted brief frontmatter, so no separators, whitespace, or traversal
# tokens. Roles may start with '_' (the shipped _generic fallback); member
# names mirror steward.py's MEMBER_NAME_RE bar.
ROLE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
MEMBER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Brief-contract enums (references/contracts/brief.md): a merged role that
# would emit an invalid brief fails here, actionably, instead of later and
# confusingly in validate_contracts.py.
VALID_LATITUDES = ("look", "propose", "do-tell", "own")
VALID_KINDS = ("research", "implementation", "review", "bounded experiment")


def load_json(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {what} at {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(data, dict):
        print(f"error: {what} at {path} must be a JSON object", file=sys.stderr)
        raise SystemExit(2)
    return data


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main(argv) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("role", help="role name (folder under roles/)")
    parser.add_argument("--project", type=Path,
                        help="project root holding .team/roles/<role>/ overrides")
    parser.add_argument("--member", help="member name (defaults to the role name)")
    parser.add_argument("--task", default="1",
                        help="task id for the starter brief (default: 1)")
    parser.add_argument("--title",
                        help="starter brief title (default: 'first <role> task')")
    parser.add_argument("--return", dest="return_path",
                        help="return path for the starter brief "
                             "(default: .team/tasks/<task>.md)")
    parser.add_argument("--out", type=Path, required=True,
                        help="output directory for the effective onboarding")
    args = parser.parse_args(argv)

    if not re.match(r"^[0-9]+$", args.task):
        print(f"error: --task must be a plain number ('7'), got '{args.task}'",
              file=sys.stderr)
        return 2

    if not ROLE_NAME_RE.fullmatch(args.role):
        print(f"error: role must be a bare folder name (letters, digits, "
              f"'.', '_', '-'; a leading '_' is allowed, e.g. the shipped "
              f"_generic role), got '{args.role}'", file=sys.stderr)
        return 2

    member = args.member or args.role
    if args.member and not MEMBER_NAME_RE.fullmatch(member):
        print(f"error: --member must be a bare name (letters, digits, '.', "
              f"'_', '-'; no whitespace, slashes, or newlines), "
              f"got '{member}'", file=sys.stderr)
        return 2

    title = args.title or f"first {args.role} task"
    return_path = args.return_path or f".team/tasks/{args.task}.md"
    if len(return_path.split()) != 1:
        print(f"error: --return must be a single path token, got "
              f"'{return_path}'", file=sys.stderr)
        return 2
    if ".." in re.split(r"[/\\]", return_path):
        print(f"error: --return must be a record-internal path with no '..' "
              f"segments, got '{return_path}'", file=sys.stderr)
        return 2

    # --- locate the base folder (unknown roles fall back to _generic) -----
    unknown_role = False
    base_dir = ROLES_DIR / args.role
    # Belt and braces: a validated bare name cannot escape, but reject
    # anything resolving outside roles/ regardless.
    try:
        resolved_base = base_dir.resolve()
        resolved_root = ROLES_DIR.resolve()
        if resolved_base != resolved_root and resolved_root not in resolved_base.parents:
            print(f"error: role '{args.role}' resolves outside the roles "
                  f"directory ({resolved_root})", file=sys.stderr)
            return 2
    except OSError as e:
        print(f"error: cannot resolve role folder for '{args.role}': {e}",
              file=sys.stderr)
        return 2
    if not base_dir.is_dir():
        unknown_role = True
        base_dir = ROLES_DIR / GENERIC_ROLE
    if not base_dir.is_dir():
        print(f"error: no role folder at {base_dir}", file=sys.stderr)
        return 2

    defaults = load_json(base_dir / "role.json", "shipped role manifest")

    # --- collect project overrides ----------------------------------------
    override_dir = None
    overrides: dict = {}
    if args.project:
        candidate = args.project / ".team" / "roles" / args.role
        if candidate.is_dir():
            override_dir = candidate
            if (candidate / "role.json").is_file():
                overrides = load_json(candidate / "role.json",
                                      "project role override")

    # --- merge fields (override wins per key; wholesale replacement) ------
    fields: dict = {}
    field_sources: dict = {}
    for key, value in defaults.items():
        fields[key] = value
        field_sources[key] = f"shipped default ({rel(base_dir / 'role.json')})"
    for key, value in overrides.items():
        fields[key] = value
        field_sources[key] = (
            f"project override ({rel(override_dir / 'role.json')})"
        )

    # --- validate merged fields against the brief contract ----------------
    latitude = fields.get("defaultLatitude")
    if latitude is not None and latitude not in VALID_LATITUDES:
        print(f"error: merged defaultLatitude for role '{args.role}' is "
              f"'{latitude}', not one of: {', '.join(VALID_LATITUDES)} "
              f"(from {field_sources['defaultLatitude']})", file=sys.stderr)
        return 2
    kind = fields.get("kind")
    if kind is not None and kind not in VALID_KINDS:
        print(f"error: merged kind for role '{args.role}' is '{kind}', not "
              f"one of: {', '.join(VALID_KINDS)} "
              f"(from {field_sources['kind']})", file=sys.stderr)
        return 2

    # --- resolve files -----------------------------------------------------
    out: Path = args.out
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: cannot create output directory {out} (does --out "
              f"point at an existing file or an unwritable location?): {e}",
              file=sys.stderr)
        return 2
    file_sources: dict = {}
    for fname in FOLDER_FILES:
        src = base_dir / fname
        source = f"shipped default ({rel(src)})"
        if override_dir and (override_dir / fname).is_file():
            src = override_dir / fname
            source = f"project override ({rel(src)})"
        file_sources[fname] = source
        try:
            text = src.read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: cannot read {fname} for role '{args.role}' "
                  f"(expected at {src}): {e}", file=sys.stderr)
            return 2
        if fname == "brief-template.md":
            mapping = {
                "task": args.task,
                "owner": member,
                "latitude": fields.get("defaultLatitude", "look"),
                "kind": fields.get("kind", "research"),
                "return": return_path,
                "title": title,
                "role": args.role,
                "stop_rule": fields["stopRuleDefault"],
            }
            rendered = Template(text).safe_substitute(mapping)
            target = out / "starter-brief.md"
        else:
            rendered = text
            target = out / fname
        try:
            target.write_text(rendered, encoding="utf-8")
        except OSError as e:
            print(f"error: cannot write {target}: {e}", file=sys.stderr)
            return 2

    # --- resolution report -------------------------------------------------
    lines = []
    lines.append(f"# Onboarding resolution — {member} as {args.role}")
    lines.append("")
    if unknown_role:
        flag = (f"FLAG: unknown role '{args.role}' — resolved from the "
                f"generic fallback ({rel(base_dir)}). Lead action: create a "
                f"role folder for '{args.role}' or confirm the generic fit.")
        lines.append(f"**{flag}**")
        lines.append("")
    lines.append(f"Resolved by `scripts/resolve_role.py` from the team-lead "
                 f"skill. Override wins where shipped defaults and the project "
                 f"conflict; every source is named below.")
    lines.append("")
    lines.append("## Role fields")
    lines.append("")
    lines.append("| field | value | source |")
    lines.append("|-------|-------|--------|")
    for key in sorted(fields):
        value = json.dumps(fields[key])
        if len(value) > 72:
            value = value[:69] + "..."
        lines.append(f"| {key} | {value} | {field_sources[key]} |")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("| file | source |")
    lines.append("|------|--------|")
    for fname in FOLDER_FILES:
        emitted = "starter-brief.md" if fname == "brief-template.md" else fname
        lines.append(f"| {emitted} | {file_sources[fname]} |")
    lines.append("")
    lines.append("## Starter brief")
    lines.append("")
    lines.append(f"- task: {args.task}, owner: {member}, "
                 f"latitude: {fields.get('defaultLatitude')}, "
                 f"return: {return_path}")
    lines.append(f"- check it: `python3 {rel(SKILL_ROOT / 'scripts' / 'validate_contracts.py')}"
                 f" brief {rel(out / 'starter-brief.md')}`")
    report = "\n".join(lines) + "\n"
    try:
        (out / "RESOLUTION.md").write_text(report, encoding="utf-8")
    except OSError as e:
        print(f"error: cannot write {out / 'RESOLUTION.md'}: {e}",
              file=sys.stderr)
        return 2

    # --- stdout ------------------------------------------------------------
    if unknown_role:
        print(f"FLAG: unknown role '{args.role}' — generic fallback resolved; "
              f"flagged for the lead in RESOLUTION.md")
    overridden_fields = sorted(overrides)
    overridden_files = [f for f in FOLDER_FILES
                        if file_sources[f].startswith("project override")]
    print(f"resolved onboarding for {member} (role: {args.role}) -> {out}")
    if overridden_fields or overridden_files:
        print("project overrides applied:")
        for k in overridden_fields:
            print(f"  field: {k}")
        for f in overridden_files:
            print(f"  file:  {f}")
    else:
        print("no project overrides; shipped defaults only")
    print(f"report: {out / 'RESOLUTION.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
