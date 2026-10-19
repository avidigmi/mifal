#!/usr/bin/env python3
"""Keep the vendored skills current with their upstreams, and offer Mifal's changes back.

    python3 maint/upstream.py status          what changed upstream since each recorded commit; exit 1 if any did
    python3 maint/upstream.py pull SKILL...   three-way merge the upstream head into skills/SKILL; exit 1 on a conflict
    python3 maint/upstream.py diff SKILL      Mifal's changes to SKILL against its recorded upstream, as a patch

third_party/provenance.json is the record: per skill, the upstream folder, the commit it was taken from and that
folder's tree. pull rewrites those and the list of files Mifal changed; it never commits. Upstreams are fetched with
git into ~/.cache/mifal-upstream (MIFAL_UPSTREAM_CACHE); MIFAL_UPSTREAM_BASE replaces https://github.com.
"""
import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

IGNORED = {"__pycache__", ".DS_Store"}


class Failed(Exception):
    pass


def git(git_dir, *args, check=True):
    result = subprocess.run(["git", "--git-dir", str(git_dir), *args], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    if check and result.returncode:
        raise Failed(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def fetch(repo, fetched={}):
    """The cache's bare clone of repo, fetched once per run."""
    if repo in fetched:
        return fetched[repo]
    base = os.environ.get("MIFAL_UPSTREAM_BASE", "https://github.com")
    cache = Path(os.environ.get("MIFAL_UPSTREAM_CACHE") or Path.home() / ".cache" / "mifal-upstream")
    git_dir = cache / (repo.replace("/", "__") + ".git")
    url = f"{base}/{repo}.git"
    if not git_dir.exists():
        cache.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:none", url, str(git_dir)],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise Failed(f"cannot clone {url}: {result.stderr.strip()}")
    else:
        git(git_dir, "fetch", "--quiet", "--prune", url, "+HEAD:refs/heads/mifal-upstream-head")
    if not git(git_dir, "rev-parse", "--verify", "--quiet", "refs/heads/mifal-upstream-head", check=False).stdout:
        git(git_dir, "fetch", "--quiet", url, "+HEAD:refs/heads/mifal-upstream-head")
    fetched[repo] = (git_dir, git(git_dir, "rev-parse", "refs/heads/mifal-upstream-head").stdout.strip())
    return fetched[repo]


def tree_of(git_dir, commit, path):
    result = git(git_dir, "rev-parse", "--verify", "--quiet", f"{commit}:{path}", check=False)
    return result.stdout.strip() or None


def files_at(git_dir, commit, path):
    """{relative path: bytes} of every file under path at commit."""
    out = git(git_dir, "ls-tree", "-r", "-z", f"{commit}:{path}").stdout
    files = {}
    for entry in filter(None, out.split("\0")):
        meta, rel = entry.split("\t", 1)
        kind, sha = meta.split()[1:3]
        if kind == "blob":
            files[rel] = subprocess.run(["git", "--git-dir", str(git_dir), "cat-file", "blob", sha],
                                        capture_output=True, check=True).stdout
    return files


def files_on_disk(folder: Path):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and not IGNORED & set(p.relative_to(folder).parts)}


def modified(ours, theirs):
    record = {"changed": sorted(r for r in ours if r in theirs and ours[r] != theirs[r]),
              "added": sorted(r for r in ours if r not in theirs),
              "removed": sorted(r for r in theirs if r not in ours)}
    return {k: v for k, v in record.items() if v}


def load(repo: Path):
    return json.loads((repo / "third_party" / "provenance.json").read_text(encoding="utf-8"))


def save(repo: Path, data):
    (repo / "third_party" / "provenance.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def skills(data):
    for component in data["components"]:
        for name, record in component.get("skills", {}).items():
            yield component, name, record


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def status(repo: Path) -> int:
    data, behind, shipped = load(repo), 0, {}
    for component, name, s in skills(data):
        shipped.setdefault(component["repo"], set()).add(s["upstream"])
        git_dir, head = fetch(component["repo"])
        now = tree_of(git_dir, head, s["upstream"])
        if now is None:
            behind += 1
            print(f"{name}: {s['upstream']} is gone upstream in {component['repo']}; find where it moved")
            continue
        if now == s["tree"]:
            print(f"{name}: up to date")
            continue
        behind += 1
        log = git(git_dir, "log", "--format=%h %s", f"{s['commit']}..{head}", "--", s["upstream"]).stdout.splitlines()
        print(f"{name}: behind {component['repo']} by {plural(len(log), 'commit')}")
        for line in log[:10]:
            print(f"    {line}")
        diff = git(git_dir, "diff-tree", "-r", "--name-status", s["tree"], now).stdout.splitlines()
        verbs = {"A": "added", "M": "changed", "D": "removed"}
        for line in diff:
            code, rel = line.split("\t", 1)
            print(f"    {verbs.get(code[0], code)} {rel}")
    for repo_name, paths in sorted(shipped.items()):
        git_dir, head = fetch(repo_name)
        parents = sorted({str(Path(p).parent) for p in paths})
        for parent in parents:
            listing = git(git_dir, "ls-tree", "-d", "--name-only", f"{head}:{parent}", check=False).stdout.split()
            for child in listing:
                path = f"{parent}/{child}"
                if path not in paths and tree_of(git_dir, head, f"{path}/SKILL.md"):
                    print(f"{repo_name}: not shipped: {path}")
    for component in data["components"]:
        if "skills" in component or not _commit(component.get("revision", "")):
            continue
        git_dir, head = fetch(component["repo"])
        count = int(git(git_dir, "rev-list", "--count", f"{component['revision']}..{head}").stdout)
        if count:
            behind += 1
            print(f"{component['name']}: {plural(count, 'new upstream commit')} since the adapted revision; "
                  f"read them and adapt by hand ({component.get('adaptations', 'adapted')})")
        else:
            print(f"{component['name']}: up to date")
    print(f"{plural(behind, 'item')} behind upstream." if behind else "Everything is current with upstream.")
    return 1 if behind else 0


def _commit(text):
    return len(text) == 40 and all(c in "0123456789abcdef" for c in text)


def find(data, name):
    for component, skill, s in skills(data):
        if skill == name:
            return component, s
    print(f"upstream.py: no vendored skill named {name!r} in third_party/provenance.json", file=sys.stderr)
    raise SystemExit(2)


def pull(repo: Path, names) -> int:
    data, conflicts = load(repo), 0
    for name in names:
        component, s = find(data, name)
        git_dir, head = fetch(component["repo"])
        now = tree_of(git_dir, head, s["upstream"])
        if now is None:
            raise Failed(f"{s['upstream']} is gone upstream in {component['repo']}; nothing pulled for {name}")
        if now == s["tree"]:
            print(f"{name}: up to date")
            continue
        folder = repo / "skills" / name
        base = files_at(git_dir, s["commit"], s["upstream"])
        theirs = files_at(git_dir, head, s["upstream"])
        ours = files_on_disk(folder)
        for rel in sorted(set(base) | set(theirs) | set(ours)):
            b, t, o = base.get(rel), theirs.get(rel), ours.get(rel)
            shown = f"skills/{name}/{rel}"
            if o == t or b == t:
                continue
            if o == b:
                if t is None:
                    (folder / rel).unlink()
                    print(f"remove {shown}")
                else:
                    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                    (folder / rel).write_bytes(t)
                    print(f"{'update' if o is not None else 'add'} {shown}")
            elif t is None:
                conflicts += 1
                print(f"conflict {shown}: deleted upstream, changed by Mifal; kept Mifal's")
            elif o is None:
                conflicts += 1
                print(f"conflict {shown}: deleted by Mifal, changed upstream; left deleted")
            else:
                merged, clean = merge(o, b or b"", t)
                (folder / rel).write_bytes(merged)
                if clean:
                    print(f"merge {shown}")
                else:
                    conflicts += 1
                    print(f"conflict {shown}: both changed the same lines; resolve the markers")
        s.update(commit=head, tree=now, modified=modified(files_on_disk(folder), theirs))
        print(f"{name}: now at {component['repo']} {head[:12]}")
    save(repo, data)
    if conflicts:
        print(f"{plural(conflicts, 'conflict')} to resolve. Then follow MAINTAINING.md: inventory, tests, review.")
        return 1
    print("Next: review the diff, then follow MAINTAINING.md: inventory, tests, review.")
    return 0


def merge(ours: bytes, base: bytes, theirs: bytes):
    with tempfile.TemporaryDirectory(prefix="mifal-merge-") as temp:
        paths = [Path(temp) / n for n in ("ours", "base", "theirs")]
        for path, content in zip(paths, (ours, base, theirs)):
            path.write_bytes(content)
        result = subprocess.run(["git", "merge-file", "-p", "-L", "mifal", "-L", "upstream base", "-L", "upstream",
                                 *map(str, paths)], capture_output=True, stdin=subprocess.DEVNULL)
        if result.returncode < 0 or result.returncode > 127:
            raise Failed(f"git merge-file failed: {result.stderr.decode(errors='replace').strip()}")
        return result.stdout, result.returncode == 0


def diff(repo: Path, name) -> int:
    component, s = find(load(repo), name)
    git_dir, _ = fetch(component["repo"])
    base = files_at(git_dir, s["commit"], s["upstream"])
    ours = files_on_disk(repo / "skills" / name)
    for rel in sorted(set(base) | set(ours)):
        if base.get(rel) == ours.get(rel):
            continue
        a = (base.get(rel) or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        b = (ours.get(rel) or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(a, b, f"a/{s['upstream']}/{rel}" if rel in base else "/dev/null",
                                                   f"b/{s['upstream']}/{rel}" if rel in ours else "/dev/null"))
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                        help="the Mifal checkout (default: the one holding this script)")
    parser = argparse.ArgumentParser(prog="upstream.py", description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", parents=[common])
    sub.add_parser("pull", parents=[common]).add_argument("skills", nargs="+")
    sub.add_parser("diff", parents=[common]).add_argument("skill")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return status(args.repo)
        if args.command == "pull":
            return pull(args.repo, args.skills)
        return diff(args.repo, args.skill)
    except Failed as e:
        print(f"upstream.py: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
