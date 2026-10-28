"""Unsupported Windows/Copilot CLI single-agent installation, separate from install.sh."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ENGINE = Path(".agents") / "mifal"
POOL = Path(".agents") / "skills"
INSTRUCTIONS = Path(".copilot") / "instructions" / "mifal.instructions.md"
MANIFEST = ENGINE / "windows-copilot-manifest.json"
LOCK = ENGINE / "windows-copilot.lock"
RUNNER = ENGINE / "windows_copilot.py"
FORMAT = 1


class Refused(ValueError):
    pass


def digest(body):
    return hashlib.sha256(body).hexdigest()


def linked(path):
    return os.path.lexists(path) and (
        path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    )


def safe(home, relative):
    path = home / relative
    if (not relative.parts or relative.is_absolute() or relative.drive or ".." in relative.parts
            or any(":" in part or part.endswith((".", " ")) for part in relative.parts)):
        raise Refused(f"Invalid managed path: {relative}")
    if linked(home):
        raise Refused(f"Linked home is not supported: {home}")
    current = home
    for part in relative.parts:
        current /= part
        if linked(current):
            raise Refused(f"Linked path is not supported: {current}")
    return path


def regular(path):
    if not path.is_file():
        raise Refused(f"Expected a regular file: {path}")
    return path.read_bytes()


def source_bytes(path):
    body = regular(path)
    if b"\0" in body:
        return body
    if path.suffix in ("", ".md", ".py", ".json", ".yaml", ".yml", ".sh", ".txt", ".typed"):
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            return body  # Invalid-encoding test fixtures are opaque payloads, not text to rewrite.
        return body.replace(b"\r\n", b"\n")
    return body


def compatibility():
    return """
## Windows/Copilot compatibility (unsupported adaptation)

This is a local, unsupported, single-agent adaptation, not upstream Windows
or GitHub Copilot support. Apply the following compatibility rules to the Guide
above; the user's request and host safety rules always take priority.

- Use the native skill tool when available, otherwise read the complete skill
  from the user's `.agents/skills/<name>/SKILL.md`. Resolve `~` to the actual
  home directory; use Windows paths and `python` rather than `python3` on Windows.
- Herdr, its socket adapter, pane operations and team execution are unavailable
  in this adaptation. The bundled `herdr` and `team-lead` material is reference
  content, not installed agents or verified team support. Do not start a team,
  recruit workers, install dependencies, or change hooks to satisfy a skill.
  Run solo-compatible phases yourself; label self-review honestly and stop
  for a required independent reviewer instead of pretending one ran.
- Shell snippets and platform-specific tools in bundled skills are not ported.
  Skip an unavailable operation explicitly; a required gate remains incomplete.
- The original Guide's `mifal update`, `mifal doctor` and `mifal uninstall`
  commands do not apply here. Run `python` on the installed
  `.agents/mifal/windows_copilot.py` with `doctor`, or `uninstall --dry-run`
  followed by `uninstall`. Updates are manual: uninstall unchanged managed
  files, review the new source, preview it, then install it.
- The native-compatible contract validator is
  `.agents/skills/team-lead/scripts/validate_contracts.py`; the review snapshot
  tool is `.agents/mifal/tools/review-snapshot.py`, both under the user's home.
  Validator evidence must be on local disk, not a UNC/device/alternate-stream
  path or reparse point. Installed text uses LF; evidence hashes remain exact.
  A validator pass checks records, not that tests or skill invocations happened.
- Copilot CLI personal skills and modular instructions are the integration
  target. Installation and CLI discovery do not prove desktop-app discovery,
  skill-body loading, agent invocation or workflow compliance. Start a fresh
  conversation after installation; verify desktop discovery separately.
"""


def payload(source):
    if not (source / "install.sh").is_file():
        raise Refused("Install must run from the adaptation's source checkout.")
    files = {}
    for folder in ("skills", "guide", "third_party"):
        root = source / folder
        if not root.is_dir():
            raise Refused(f"Missing source folder: {root}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(source)
            safe(source, relative)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_file():
                target = POOL / path.relative_to(root) if folder == "skills" else ENGINE / relative
                files[target] = source_bytes(path)
            elif not path.is_dir():
                raise Refused(f"Unsupported source entry: {path}")
    for name in ("VERSION", "LICENSE", "NOTICE", "tools/review-snapshot.py"):
        path = Path(name)
        files[ENGINE / path] = source_bytes(safe(source, path))
    files[RUNNER] = source_bytes(safe(source, Path("maint/windows_copilot.py")))
    guide = files[ENGINE / "guide" / "AGENTS.md"].decode("utf-8")
    files[INSTRUCTIONS] = ('---\napplyTo: "**"\n---\n\n' + guide + compatibility()).encode("utf-8")
    return files


def allowed(relative):
    return (
        relative == INSTRUCTIONS
        or (len(relative.parts) > len(POOL.parts) and relative.parts[:2] == POOL.parts)
        or (len(relative.parts) > len(ENGINE.parts) and relative.parts[:2] == ENGINE.parts
            and relative not in (MANIFEST, LOCK))
    )


def load(home):
    path = safe(home, MANIFEST)
    if not path.exists():
        raise Refused(f"No Windows/Copilot manifest at {path}")
    data = json.loads(regular(path))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise Refused("Unrecognized Windows/Copilot manifest format.")
    if data.get("home") != str(home) or data.get("state") not in ("installing", "installed"):
        raise Refused("Manifest belongs to another home, or has an invalid state.")
    entries, dirs = data.get("files"), data.get("directories")
    if not isinstance(entries, dict) or not entries or not isinstance(dirs, list):
        raise Refused("Manifest has no usable file/directory records.")
    for name, sha in entries.items():
        if not isinstance(name, str):
            raise Refused("Invalid manifest filename.")
        relative = Path(name)
        if not allowed(relative) or not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha):
            raise Refused(f"Invalid manifest entry: {name}")
        safe(home, relative)
    if not all(isinstance(name, str) for name in dirs) or len(set(dirs)) != len(dirs):
        raise Refused("Duplicate directory records.")
    parents = {str(p) for name in entries for p in Path(name).parents if p != Path(".")}
    for name in dirs:
        if not isinstance(name, str) or name not in parents:
            raise Refused("Invalid directory record.")
        safe(home, Path(name))
    return data


def inspect(home, data):
    problems = []
    for name, sha in data["files"].items():
        path = safe(home, Path(name))
        if not path.is_file() or digest(path.read_bytes()) != sha:
            problems.append(path)
    return problems


def write_new(path, body):
    with path.open("xb") as stream:
        stream.write(body)


def save(home, data):
    path = safe(home, MANIFEST)
    temporary = path.with_suffix(".tmp")
    write_new(temporary, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def acquire(home):
    path = safe(home, LOCK)
    try:
        write_new(path, str(os.getpid()).encode("ascii"))
    except FileExistsError:
        raise Refused(f"Install/uninstall lock exists: {path}; confirm its process has stopped before removing it.")
    return path


def install(home, source, dry_run, out=print):
    files = payload(source)
    for relative in files:
        safe(home, relative)
    manifest = safe(home, MANIFEST)
    if manifest.exists():
        data = load(home)
        expected = {str(path): digest(body) for path, body in files.items()}
        if data["state"] != "installed" or inspect(home, data):
            raise Refused("Incomplete or edited install. Run doctor, then preview uninstall; nothing was overwritten.")
        if data["files"] != expected:
            raise Refused("Installed payload differs. Preview uninstall before installing a new revision.")
        out("Already installed; all managed files match. Nothing written.")
        return 0
    if safe(home, ENGINE).exists():
        raise Refused(f"Existing engine is not this adaptation's: {home / ENGINE}")
    # Refuse the whole skill folder, including additions an existing user owns.
    for name in sorted({p.parts[2] for p in files if p.parts[:2] == POOL.parts}):
        if safe(home, POOL / name).exists():
            raise Refused(f"Existing skill folder: {home / POOL / name}")
        alternate = safe(home, Path(".copilot") / "skills" / name)
        if alternate.exists():
            raise Refused(f"Duplicate personal skill: {alternate}")
    if safe(home, INSTRUCTIONS).exists():
        raise Refused(f"Existing instruction file: {home / INSTRUCTIONS}")
    dirs = {parent for relative in files for parent in relative.parents if parent != Path(".")}
    missing = sorted((p for p in dirs if not safe(home, p).exists()), key=lambda p: (len(p.parts), str(p)))
    for relative in dirs:
        path = safe(home, relative)
        if path.exists() and not path.is_dir():
            raise Refused(f"Expected a directory: {path}")
    for relative in missing:
        out(f"mkdir {home / relative}")
    for relative in files:
        out(f"write {home / relative}")
    out(f"write {manifest}")
    if dry_run:
        out(f"Dry run: {len(files)} files plus manifest; nothing written.")
        return 0
    data = {
        "format": FORMAT, "state": "installing", "home": str(home),
        "source": str(source), "files": {str(p): digest(b) for p, b in files.items()},
        "directories": [str(p) for p in missing],
    }
    (home / ENGINE).mkdir(parents=True, exist_ok=False)
    lock = acquire(home)
    try:
        save(home, data)
        for relative in missing:
            safe(home, relative).mkdir(parents=True, exist_ok=True)
        for relative, body in files.items():
            write_new(safe(home, relative), body)
        data["state"] = "installed"
        save(home, data)
    finally:
        lock.unlink()
    out("Installed unsupported Windows/Copilot single-agent adaptation.")
    return 0


def doctor(home, out=print):
    data = load(home)
    problems = inspect(home, data)
    if data["state"] != "installed":
        out("FAIL: interrupted install; preview uninstall before retrying.")
    for path in problems:
        out(f"FAIL: missing or changed managed file: {path}")
    if not problems and data["state"] == "installed":
        count = len({Path(p).parts[2] for p in data["files"] if Path(p).parts[:2] == POOL.parts})
        out(f"PASS: {len(data['files'])} managed files match; {count} bundled skills and Guide present.")
    out("WARN: unsupported adaptation; Herdr, panes, team execution and platform shell snippets are not supported.")
    out("WARN: file checks do not prove CLI/desktop discovery, skill invocation or workflow compliance.")
    return int(bool(problems) or data["state"] != "installed")


def uninstall(home, dry_run, out=print):
    data = load(home)
    removable, kept = [], []
    for name, sha in data["files"].items():
        path = safe(home, Path(name))
        if not path.exists():
            continue
        if path.is_file() and digest(path.read_bytes()) == sha:
            removable.append(path)
        else:
            kept.append(path)
            out(f"KEEP changed path: {path}")
    runner = home / RUNNER
    if kept and runner in removable:
        removable.remove(runner)
        out(f"KEEP recovery command: {runner}")
    removable.sort(key=lambda path: path == runner)
    for path in removable:
        out(f"remove {path}")
    if dry_run:
        out(f"Dry run: {len(removable)} files removable; {len(kept)} edited paths kept; nothing written.")
        return int(bool(kept))
    lock = acquire(home)
    try:
        for path in removable:
            # Recheck just before removing; another process may have edited after preview.
            if not path.is_file() or digest(path.read_bytes()) != data["files"][str(path.relative_to(home))]:
                raise Refused(f"Path changed during uninstall: {path}; manifest retained.")
            path.unlink()
        if kept:
            out("Edited paths and manifest retained. Move edits aside yourself, then rerun uninstall.")
            return 1
        safe(home, MANIFEST).unlink()
    finally:
        lock.unlink()
    for name in sorted(data["directories"], key=lambda n: len(Path(n).parts), reverse=True):
        path = safe(home, Path(name))
        if path.is_dir():
            if any(path.iterdir()):
                out(f"KEEP nonempty directory: {path}")
            else:
                path.rmdir()
    out("Removed unchanged managed files. User additions were not removed.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "doctor", "uninstall"))
    parser.add_argument("--dry-run", action="store_true", help="preview install/uninstall without writes")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 9):
        parser.error("Python 3.9 or later is required.")
    if sys.platform != "win32":
        parser.error("This adaptation entry point is for Windows only; use install.sh on macOS.")
    home = Path.home().absolute()
    custom = os.environ.get("COPILOT_HOME")
    if custom and Path(custom).absolute() != home / ".copilot":
        parser.error("A custom COPILOT_HOME is not supported; no configuration was changed.")
    try:
        if args.command == "install":
            return install(home, Path(__file__).resolve().parents[1], args.dry_run)
        if args.command == "doctor":
            return doctor(home)
        return uninstall(home, args.dry_run)
    except (OSError, ValueError) as error:
        print(f"mifal-windows: {error}", file=sys.stderr)
        print("No user file was overwritten. If a manifest exists, run doctor and preview uninstall.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
