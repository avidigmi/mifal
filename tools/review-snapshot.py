#!/usr/bin/env python3
"""Identify committed, staged and working-tree review scope without changing Git."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


# Only ref, tree, index and raw-byte reads: Git's own worktree comparison runs repository clean/process
# filters, and repository config can name an fsmonitor hook, a promisor remote or (on a terminal) a pager.
SAFE = ["--no-pager", "-c", "core.fsmonitor=false", "--no-optional-locks", "--no-lazy-fetch",
        "--no-replace-objects"]
ARGV_BUDGET = 128 * 1024


def git(repo, *args, data=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(repo), *SAFE, *args], capture_output=True, env=env,
                            input=data if data is not None else b"")
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def names(repo, *args):
    return sorted(set(os.fsdecode(value) for value in git(repo, *args).split(b"\0") if value))


def entries(repo, *args):
    """Map path -> (mode, object id) from ls-tree/ls-files -z rows; refuse unmerged index stages."""
    rows = {}
    for row in git(repo, *args).split(b"\0"):
        if row:
            meta, name = row.split(b"\t", 1)
            fields = meta.decode().split()
            if args[0] == "ls-files" and fields[2] != "0":
                raise ValueError("Index has unmerged entries; resolve them before taking a review snapshot")
            rows[os.fsdecode(name)] = (fields[0], fields[1] if args[0] == "ls-files" else fields[2])
    return rows


def worktree_entries(repo, tracked):
    """Raw-byte object ids for tracked paths, without filters; gitlinks keep their recorded id."""
    found, regular = {}, []
    for name, (mode, oid) in tracked.items():
        path = repo / name
        if mode == "160000":
            found[name] = (mode, oid)  # submodule worktrees are outside this scope (see limits)
        elif path.is_symlink():
            found[name] = ("120000", git(repo, "hash-object", "--no-filters", "--stdin",
                                         data=os.fsencode(os.readlink(path))).decode().strip())
        elif path.is_file():
            regular.append(name)
            found[name] = ("100755" if path.stat().st_mode & 0o111 else "100644", None)
        elif path.exists():
            found[name] = ("040000", None)
    # Names go in argv after "--": --stdin-paths C-unquotes a line starting with a double quote, so a file
    # named "a" would be hashed as a. Batch by bytes to stay under the OS argument limit.
    batches, size = [[]], 0
    for name in regular:
        cost = len(os.fsencode(name)) + 1
        if batches[-1] and size + cost > ARGV_BUDGET:
            batches.append([])
            size = 0
        batches[-1].append(name)
        size += cost
    for batch in filter(None, batches):
        oids = git(repo, "hash-object", "--no-filters", "--", *batch).split()
        if len(oids) != len(batch):
            raise ValueError("git hash-object returned an unexpected number of ids")
        for name, oid in zip(batch, oids):
            found[name] = (found[name][0], oid.decode())
    return found


def file_identity(repo, name):
    path = repo / name
    if path.is_symlink():
        data = os.fsencode(os.readlink(path))
        kind = "symlink"
    elif path.is_file():
        data = path.read_bytes()
        kind = "file"
    elif not path.exists():
        return {"kind": "deleted"}
    else:
        raise ValueError(f"Review scope contains a directory/submodule: {name}; review it separately")
    identity = {"kind": kind, "sha256": hashlib.sha256(data).hexdigest()}
    if kind == "file":
        identity["executable"] = bool(path.stat().st_mode & 0o111)
    return identity


def snapshot(repo, base, paths=()):
    repo = Path(os.fsdecode(git(Path(repo).resolve(), "rev-parse", "--show-toplevel")).strip())
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    base_commit = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    merge_base = git(repo, "merge-base", base_commit, head).decode().strip()
    scope = ["--", *paths]
    base_rows = entries(repo, "ls-tree", "-r", "-z", merge_base, *scope)
    index_rows = entries(repo, "ls-files", "--stage", "-z", *scope)
    live = worktree_entries(repo, {**base_rows, **index_rows})
    tracked = sorted(name for name in set(base_rows) | set(index_rows) if live.get(name) != base_rows.get(name))
    staged = sorted(name for name in set(base_rows) | set(index_rows)
                    if index_rows.get(name) != base_rows.get(name))
    unstaged = sorted(name for name in index_rows if live.get(name) != index_rows[name])
    candidates = names(repo, "ls-files", "--others", "--exclude-standard", "-z", *scope)
    untracked = candidates if paths else []
    files = {name: file_identity(repo, name) for name in sorted(set(tracked + staged + untracked))}
    index = git(repo, "ls-files", "--stage", "-z", *scope)
    state = {
        "schemaVersion": 2,
        "repo": str(repo),
        "baseCommit": base_commit,
        "mergeBase": merge_base,
        "head": head,
        "paths": list(paths),
        "files": files,
        "indexSha256": hashlib.sha256(index).hexdigest(),
        "workingTreePaths": tracked,
        "indexPaths": staged,
        "indexWorktreeDifferPaths": unstaged,
        "untrackedPaths": untracked,
        "omittedUntrackedPaths": [] if paths else candidates,
        "reviewable": bool(files),
    }
    canonical = json.dumps(state, sort_keys=True, ensure_ascii=True).encode()
    state["snapshotSha256"] = hashlib.sha256(canonical).hexdigest()
    prefix = ["git", "-C", str(repo), *SAFE]
    state["reviewCommands"] = {
        "index": shlex.join([*prefix, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv",
                             merge_base, *scope]),
    }
    state["limits"] = [
        "Hashes identify bytes, not behavioral correctness or reviewer execution.",
        "Read explicitly scoped untracked files separately; git diff does not include them.",
        "Untracked files are excluded unless --path gives an explicit review scope.",
        "Git-ignored files are outside this Git scope; inspect any required ignored artifacts explicitly.",
        "Collection is not atomic; check identity again after review and avoid concurrent writers.",
        "No working-tree diff command is offered: Git's worktree comparison runs repository clean/process "
        "filters. Review live bytes by reading the files listed in files and comparing them with a saved "
        "pre-edit baseline.",
        "Working-tree identities hash raw bytes, so a path whose filter normalizes content (line endings, "
        "LFS) can be listed as changed; submodule worktrees are not inspected.",
    ]
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--check", type=Path, help="verify an earlier JSON snapshot is still current")
    args = parser.parse_args()
    try:
        if args.check:
            previous = json.loads(args.check.read_text())
            if previous.get("schemaVersion") != 2:
                print("STALE: snapshot schema changed; take a new review snapshot", file=sys.stderr)
                return 1
            if args.base or args.path:
                raise ValueError("--check uses the saved scope; do not also pass --base or --path")
            current = snapshot(previous["repo"], previous["baseCommit"], previous["paths"])
            if current["snapshotSha256"] != previous["snapshotSha256"]:
                print("STALE: review snapshot changed; review the affected final work again", file=sys.stderr)
                return 1
            print("OK: review snapshot identity unchanged")
            return 0
        if not args.base:
            raise ValueError("Provide --base with a commit, branch or tag")
        print(json.dumps(snapshot(args.repo, args.base, args.path), indent=2, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
