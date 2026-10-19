#!/usr/bin/env python3
"""Stewardship registries for the team-lead skill (spec 0001, ticket 02).

One stdlib-only CLI that keeps the lead's stewardship picture:

  build            Reconcile the skill inventory from the real agency tree
                   (pool + .skill-lock.json + skill-scopes.json +
                   invocation-policy.json + SKILLS.md catalog), merge the
                   lead's curated overlays and member capability briefs, and
                   write generated/skill-inventory.json + generated/INVENTORY.md.
  check            Read-only drift gate: rebuild in memory and compare with the
                   COMMITTED generated/skill-inventory.json blob at HEAD
                   (git show). Exit 1 on drift, so a rebuilt-but-never-committed
                   inventory cannot pass. With --working-tree, compare only
                   the generated working copy for preparation, not release.
  brief add FILE   Validate a member capability brief and install it into
                   briefs/<member>.md (never edits curated inventory fields).
  briefs           List installed capability briefs.
  ledger add MEMBER TOOL [--kind mcp|connector] [--at ISO_DATE] [--note TEXT]
                   Record a member's MCP/connector in tool-ledger.json.
  ledger verify MEMBER TOOL
                   Refresh a tool's lastVerifiedAt to now.
  ledger show [--stale-days N]
                   Show the ledger with staleness flags (default 30 days).
  who QUERY...     Assignment query: "who has demonstrated fit for X?"
                   Answers from curated inventory AND self-reported briefs AND
                   the tool ledger, with the source of every match annotated.

Sources of truth (read, never written): the agency config files. Curated
stewardship edits live in overlays/skill-overlays.json; member self-reports
live in briefs/; the tool ledger lives in tool-ledger.json. Generated output
is committed so stewardship changes are reviewable history.
"""

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

AGENCY = Path.home() / ".agents"
POOL = AGENCY / "skills"
STEWARDSHIP = POOL / "team-lead" / "stewardship"

LOCK_FILE = AGENCY / ".skill-lock.json"
SCOPES_FILE = STEWARDSHIP / "skill-scopes.json"
POLICY_FILE = STEWARDSHIP / "invocation-policy.json"
CATALOG_FILE = POOL / "SKILLS.md"

OVERLAYS_FILE = STEWARDSHIP / "overlays" / "skill-overlays.json"
BRIEFS_DIR = STEWARDSHIP / "briefs"
LEDGER_FILE = STEWARDSHIP / "tool-ledger.json"
GENERATED_DIR = STEWARDSHIP / "generated"
INVENTORY_JSON = GENERATED_DIR / "skill-inventory.json"
INVENTORY_MD = GENERATED_DIR / "INVENTORY.md"

DEFAULT_STALE_DAYS = 30

_policy_spec = importlib.util.spec_from_file_location("invocation_policy", STEWARDSHIP / "invocation_policy.py")
invocation = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(invocation)

# ---------------------------------------------------------------------------
# small parsing helpers (stdlib only; frontmatter is plain key: value lines)
# ---------------------------------------------------------------------------


def shown(path):
    """A path as the inventory records it: under HOME as ~/..., so the inventory is the same on every machine."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    """Parse ISO-8601-ish timestamps ('Z' or offset). Returns aware datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: malformed JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def read_frontmatter(path):
    """Return (frontmatter dict, body) for a Markdown file with --- fences.

    Values are plain strings; `description: >-` folded blocks are joined.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm, i = {}, 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2)
            if value in (">-", ">", "|-", "|", ""):
                # folded/literal block or empty: consume indented continuations
                parts = [] if value else [value]
                j = i + 1
                while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")):
                    parts.append(lines[j].strip())
                    j += 1
                if parts:
                    fm[key] = " ".join(p for p in parts if p)
                    i = j
                    continue
                fm[key] = ""
            else:
                fm[key] = value
        i += 1
    body = "\n".join(lines[i + 1:]) if i < len(lines) else ""
    return fm, body


# ---------------------------------------------------------------------------
# reconcile: read the real agency tree
# ---------------------------------------------------------------------------


def scan_pool():
    """Every pool entry with a SKILL.md (symlinked dirs included)."""
    found = {}
    for entry in sorted(POOL.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        fm, _ = read_frontmatter(skill_md)
        found[entry.name] = {
            "description": fm.get("description", ""),
            "path": str(skill_md),
        }
    return found


def parse_catalog():
    """Extract per-skill decisions from the SKILLS.md audit dashboard.

    Only rows whose link target is inside the shared pool are used; the
    lock/scopes/policy files remain authoritative — this adds the curated
    decision + purpose text as searchable context.
    """
    decisions = {}
    if not CATALOG_FILE.exists():
        return decisions
    prefix = str(POOL) + "/"
    row_re = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|([^|]*)\|\s*\*\*([^*]+)\*\*\s*\|([^|]*)\|")
    for line in CATALOG_FILE.read_text(encoding="utf-8").splitlines():
        m = row_re.match(line)
        if not m:
            continue
        name, target, provider, decision, purpose = (x.strip() for x in m.groups())
        if not target.startswith(prefix):
            continue
        skill = Path(target).parent.name
        if skill in decisions:
            continue
        decisions[skill] = {
            "provider": provider,
            "decision": decision,
            "purpose": purpose,
        }
    return decisions


def build_inventory():
    """Reconcile the inventory from the real tree. Pure: no writes."""
    lock = load_json(LOCK_FILE, {"version": None, "skills": {}})
    scopes = load_json(SCOPES_FILE, {"mirrors": {}, "dependencies": {}})
    policy = invocation.read_policy(POLICY_FILE)
    overlays = load_json(OVERLAYS_FILE, {"skills": {}})
    pool = scan_pool()
    catalog = parse_catalog()

    locked = lock.get("skills", {})
    mirrors = scopes.get("mirrors", {})
    dependencies = scopes.get("dependencies", {})
    overlay_map = overlays.get("skills", {})

    skills = {}
    for name in sorted(pool):
        entry_lock = locked.get(name)
        origin = (
            {
                "type": "github-lock",
                "source": entry_lock.get("source"),
                "sourceUrl": entry_lock.get("sourceUrl"),
                "skillFolderHash": entry_lock.get("skillFolderHash"),
                "installedAt": entry_lock.get("installedAt"),
                "updatedAt": entry_lock.get("updatedAt"),
            }
            if entry_lock
            else {"type": "local", "source": None}
        )
        derived_maturity = "tracked-upstream" if entry_lock else "local"
        derived_owner = entry_lock.get("source") if entry_lock else "local"
        # empty overlay values ("" / null / []) are unset, not overrides
        overlay = {
            k: v for k, v in overlay_map.get(name, {}).items()
            if v not in (None, "", [], {})
        }
        catalog_row = catalog.get(name, {})
        body = Path(pool[name]["path"]).resolve()
        claude_disabled = invocation.declared(body.read_bytes(), "SKILL.md")
        metadata = body.parent / "agents/openai.yaml"
        codex_implicit = invocation.declared(metadata.read_bytes(), "agents/openai.yaml") if metadata.exists() else None
        skills[name] = {
            "description": pool[name]["description"],
            "origin": origin,
            "owner": overlay.get("owner", derived_owner),
            "maturity": overlay.get("maturity", derived_maturity),
            "mirrors": mirrors.get(name, "all"),
            "automaticPolicy": invocation.automatic(policy, name),
            "resolvedBody": shown(POOL / body.relative_to(POOL.resolve())) if POOL.resolve() in body.parents
            else shown(body),
            "declaredInvocation": {
                "claudeDisableModelInvocation": claude_disabled,
                "codexAllowImplicitInvocation": codex_implicit,
                "claudeAutomaticEligible": claude_disabled is not True,
                "codexAutomaticEligible": codex_implicit is not False,
            },
            "runtimeDiscovery": "unverified",
            "dependencies": dependencies.get(name, []),
            "catalog": catalog_row or None,
        }
        if overlay:
            skills[name]["curated"] = {
                k: v for k, v in overlay.items() if k in ("owner", "maturity", "notes")
            }

    # briefs merge in as distinguishable self-reported claims
    claims = {}
    unmatched = {}
    for brief in load_briefs():
        member = brief["member"]
        for cap in brief["capabilities"]:
            target = claims if cap in skills else unmatched
            target.setdefault(cap, []).append(
                {"member": member, "evidence": brief.get("evidence", ""), "reported": brief.get("reported", "")}
            )

    reconciliation = {
        "poolSkills": len(skills),
        "lockEntries": len(locked),
        "lockedNotOnDisk": sorted(set(locked) - set(skills)),
        "onDiskNotLocked": sorted(set(skills) - set(locked)),
        "catalogRowsMatched": len([s for s in skills if skills[s]["catalog"]]),
        "overlayEntries": len(overlay_map),
        "briefsInstalled": len(load_briefs()),
    }

    return {
        "spec": "0001",
        "ticket": "02-stewardship-registries",
        "generatedAt": now_iso(),
        "sources": {
            "pool": shown(POOL),
            "lock": shown(LOCK_FILE),
            "scopes": shown(SCOPES_FILE),
            "policy": shown(POLICY_FILE),
            "catalog": shown(CATALOG_FILE),
            "overlays": shown(OVERLAYS_FILE),
            "briefs": shown(BRIEFS_DIR),
        },
        "reconciliation": reconciliation,
        "skills": skills,
        "selfReportedClaims": {
            "matched": {k: v for k, v in sorted(claims.items())},
            "notInInventory": {k: v for k, v in sorted(unmatched.items())},
        },
    }


def render_inventory_md(inv):
    r = inv["reconciliation"]
    lines = [
        "# Skill inventory (generated)",
        "",
        f"Generated by `stewardship/steward.py build` at {inv['generatedAt']}.",
        "Do not hand-edit: curated edits go to `overlays/skill-overlays.json`,",
        "member self-reports go to `briefs/`, then rebuild.",
        "",
        "## Reconciliation with agency config",
        "",
        f"- Pool skills on disk: **{r['poolSkills']}**",
        f"- Lock entries (`.skill-lock.json` v3): **{r['lockEntries']}**",
        f"- Catalog rows matched (`SKILLS.md`): **{r['catalogRowsMatched']}**",
        f"- Curated overlays applied: **{r['overlayEntries']}**",
        f"- Capability briefs installed: **{r['briefsInstalled']}**",
        f"- Locked but missing on disk: {', '.join(r['lockedNotOnDisk']) or 'none'}",
        f"- On disk but not in lock (local): {len(r['onDiskNotLocked'])}"
        + (f" — {', '.join(r['onDiskNotLocked'])}" if r["onDiskNotLocked"] else ""),
        "",
        "## Skills",
        "",
        "Eligibility below reflects local declarations only. Profile scope, runtime discovery, body loading and actual use need separate evidence.",
        "",
        "| Skill | Maturity | Owner | Origin | Automatic policy | Claude eligible | Codex eligible | Catalog decision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in inv["skills"].items():
        origin = s["origin"]["source"] if s["origin"]["type"] == "github-lock" else "local"
        cat = s["catalog"]["decision"] if s["catalog"] else "—"
        lines.append(
            f"| {name} | {s['maturity']} | {s['owner']} | {origin} | "
            f"{'yes' if s['automaticPolicy'] else 'no'} | "
            f"{'yes' if s['declaredInvocation']['claudeAutomaticEligible'] else 'no'} | "
            f"{'yes' if s['declaredInvocation']['codexAutomaticEligible'] else 'no'} | {cat} |"
        )
    claims = inv["selfReportedClaims"]
    lines += ["", "## Self-reported capability claims (member briefs)", ""]
    if not claims["matched"] and not claims["notInInventory"]:
        lines.append("No capability briefs installed yet (`briefs/`).")
    for cap, entries in claims["matched"].items():
        who = "; ".join(f"{e['member']} ({e['reported']})" for e in entries)
        lines.append(f"- **{cap}** — claimed by {who} [self-reported]")
    for cap, entries in claims["notInInventory"].items():
        who = "; ".join(f"{e['member']} ({e['reported']})" for e in entries)
        lines.append(f"- **{cap}** — claimed by {who} [self-reported, NOT in pool inventory]")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# capability briefs
# ---------------------------------------------------------------------------

BRIEF_LIST_KEYS = ("capabilities", "tools")
BRIEF_REQUIRED = ("member", "capabilities")
# Briefs are member-submitted input: the member name becomes a filename, so it
# must be a bare safe name (no path separators, no leading dot, no traversal).
MEMBER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def parse_brief(path):
    fm, body = read_frontmatter(Path(path))
    for key in BRIEF_LIST_KEYS:
        if key in fm:
            fm[key] = [x.strip() for x in fm[key].split(",") if x.strip()]
    missing = [k for k in BRIEF_REQUIRED if not fm.get(k)]
    if missing:
        raise ValueError(f"brief missing required frontmatter: {', '.join(missing)}")
    if not MEMBER_NAME_RE.fullmatch(fm["member"]):
        raise ValueError(
            f"invalid member name {fm['member']!r}: must match "
            f"{MEMBER_NAME_RE.pattern} (a bare name, not a path)"
        )
    fm["notes"] = body.strip()
    return fm


def load_briefs():
    briefs = []
    if not BRIEFS_DIR.exists():
        return briefs
    for path in sorted(BRIEFS_DIR.glob("*.md")):
        if path.name == "TEMPLATE.md":
            continue
        try:
            briefs.append(parse_brief(path))
        except ValueError as exc:
            print(f"warning: skipping invalid brief {path.name}: {exc}", file=sys.stderr)
    return briefs


# ---------------------------------------------------------------------------
# tool ledger
# ---------------------------------------------------------------------------


def load_ledger():
    return load_json(LEDGER_FILE, {"version": 1, "members": {}})


def ledger_add(member, tool, kind, at, note):
    ledger = load_ledger()
    stamp = at or now_iso()
    if at:
        try:
            parse_iso(at)  # validate
        except ValueError as exc:
            print(f"error: invalid --at value {at!r}: {exc}", file=sys.stderr)
            sys.exit(1)
    tools = ledger["members"].setdefault(member, {"tools": []})["tools"]
    for t in tools:
        if t["name"] == tool:
            # re-recording updates verification, never identity or history:
            # kind/notes/recordedAt are preserved unless explicitly passed
            if kind:
                t["kind"] = kind
            if note:
                t["notes"] = note
            t["recordedAt"] = t.get("recordedAt", stamp)
            t["lastVerifiedAt"] = stamp
            result_kind = t.get("kind", "mcp")
            break
    else:
        result_kind = kind or "mcp"
        tools.append({"name": tool, "kind": result_kind,
                      "recordedAt": stamp, "lastVerifiedAt": stamp, "notes": note or ""})
    write_json(LEDGER_FILE, ledger)
    print(f"recorded {tool} ({result_kind}) for member {member} at {stamp}")


def ledger_verify(member, tool):
    ledger = load_ledger()
    for t in ledger.get("members", {}).get(member, {}).get("tools", []):
        if t["name"] == tool:
            t["lastVerifiedAt"] = now_iso()
            write_json(LEDGER_FILE, ledger)
            print(f"verified {tool} for member {member} at {t['lastVerifiedAt']}")
            return
    print(f"error: no tool '{tool}' recorded for member '{member}'", file=sys.stderr)
    sys.exit(1)


def iter_ledger_tools(ledger):
    """Yield (member, tool-dict) from a possibly hand-edited ledger.

    Malformed entries are skipped with a warning instead of crashing.
    """
    for member, record in sorted(ledger.get("members", {}).items()):
        tools = record.get("tools", []) if isinstance(record, dict) else []
        if not isinstance(tools, list):
            print(f"warning: member '{member}' has malformed 'tools'; skipped",
                  file=sys.stderr)
            continue
        for t in tools:
            if not isinstance(t, dict) or not t.get("name"):
                print(f"warning: member '{member}' has malformed tool entry "
                      f"{t!r}; skipped", file=sys.stderr)
                continue
            yield member, t


def tool_age_days(t, now):
    """Days since last verification; None when the date is unusable."""
    stamp = t.get("lastVerifiedAt") or t.get("recordedAt")
    if not stamp:
        print(f"warning: tool '{t.get('name')}' has no recordedAt; skipped",
              file=sys.stderr)
        return None
    try:
        return (now - parse_iso(stamp)).days
    except ValueError:
        print(f"warning: tool '{t.get('name')}' has unparseable date "
              f"{stamp!r}; skipped", file=sys.stderr)
        return None


def ledger_show(stale_days):
    ledger = load_ledger()
    now = datetime.now(timezone.utc)
    if not ledger.get("members"):
        print("tool ledger is empty")
        return
    for member, record in sorted(ledger.get("members", {}).items()):
        print(f"{member}:")
        for _member, t in iter_ledger_tools({"members": {member: record}}):
            age = tool_age_days(t, now)
            if age is None:
                continue
            stale = age > stale_days
            marker = f"STALE (>{stale_days}d)" if stale else "fresh"
            recorded = t.get("recordedAt", "?")
            print(
                f"  - {t['name']} [{t.get('kind', 'mcp')}] recorded {recorded}, "
                f"last verified {t.get('lastVerifiedAt', recorded)}, "
                f"age {age}d -> {marker}"
                + (f" — {t['notes']}" if t.get("notes") else "")
            )


# ---------------------------------------------------------------------------
# assignment query
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "for", "who", "has", "with", "and", "or", "of", "to", "in", "on", "is", "fit", "demonstrated"}


def tokenize(text):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in STOPWORDS]


def who(query_tokens):
    query_tokens = tokenize(" ".join(query_tokens))
    inv = build_inventory()
    ledger = load_ledger()
    now = datetime.now(timezone.utc)

    curated, reported, tools_out = [], [], []
    for name, s in inv["skills"].items():
        haystack = f"{name.replace('-', ' ')} {s['description']} " + (
            s["catalog"]["purpose"] if s["catalog"] else ""
        )
        hay_tokens = set(tokenize(haystack))
        hits = [t for t in query_tokens if t in hay_tokens or t in name]
        if hits:
            score = len(hits) + 2 * sum(1 for t in query_tokens if t in name)
            curated.append((score, name, s, hits))
    for cap, entries in inv["selfReportedClaims"]["matched"].items():
        hits = [t for t in query_tokens if t in cap or t in tokenize(cap.replace("-", " "))]
        if hits:
            reported.append((cap, entries, "in-inventory"))
    for cap, entries in inv["selfReportedClaims"]["notInInventory"].items():
        hits = [t for t in query_tokens if t in cap or t in tokenize(cap.replace("-", " "))]
        if hits:
            reported.append((cap, entries, "NOT-in-inventory"))
    for member, tool in iter_ledger_tools(ledger):
        if any(t in tool["name"] or t in tokenize(tool["name"].replace("-", " ")) for t in query_tokens):
            age = tool_age_days(tool, now)
            if age is not None:
                tools_out.append((member, tool, age))

    print(f"Assignment query: \"{' '.join(query_tokens)}\"")
    print()
    print("== Curated inventory matches [source: lead-curated, reconciled from agency tree] ==")
    if curated:
        for score, name, s, hits in sorted(curated, key=lambda x: -x[0])[:10]:
            print(f"  - {name} (maturity: {s['maturity']}, owner: {s['owner']}) — matched on: {', '.join(hits)}")
    else:
        print("  (none)")
    print()
    print("== Self-reported capability matches [source: member brief] ==")
    if reported:
        for cap, entries, status in reported:
            for e in entries:
                ev = f" — evidence: {e['evidence']}" if e.get("evidence") else ""
                print(f"  - member {e['member']} claims {cap} [{status}]{ev}")
    else:
        print("  (none)")
    print()
    print("== Tool ledger matches [source: lead-maintained ledger] ==")
    if tools_out:
        for member, tool, age in tools_out:
            print(f"  - member {member} has {tool['name']} [{tool.get('kind', 'mcp')}] (age {age}d)")
    else:
        print("  (none)")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_build(_args):
    inv = build_inventory()
    write_json(INVENTORY_JSON, inv)
    INVENTORY_MD.write_text(render_inventory_md(inv), encoding="utf-8")
    r = inv["reconciliation"]
    print(f"built inventory: {r['poolSkills']} pool skills, {r['lockEntries']} lock entries, "
          f"{r['catalogRowsMatched']} catalog rows, {r['overlayEntries']} overlays, "
          f"{r['briefsInstalled']} briefs")
    print(f"  on disk but not in lock (local): {len(r['onDiskNotLocked'])}")
    print(f"  locked but missing on disk: {len(r['lockedNotOnDisk'])}")
    print(f"wrote {INVENTORY_JSON}")
    print(f"wrote {INVENTORY_MD}")


REPO_REL_INVENTORY = "team-lead/stewardship/generated/skill-inventory.json"


def committed_inventory():
    """The inventory blob committed at HEAD of the skills repo.

    The drift gate compares against HEAD, not the working copy, so a
    rebuilt-but-never-committed inventory cannot pass.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(POOL), "show", f"HEAD:./{REPO_REL_INVENTORY}"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        print("error: git not found; the drift gate requires the skills repo",
              file=sys.stderr)
        sys.exit(1)
    if out.returncode != 0:
        print(f"FAIL: no committed inventory at HEAD "
              f"(git show: {out.stderr.strip()}); run `steward.py build` and commit",
              file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        print(f"FAIL: committed inventory at HEAD is malformed JSON: {exc}",
              file=sys.stderr)
        sys.exit(1)


def cmd_check(args):
    fresh = build_inventory()
    working_tree = getattr(args, "working_tree", False)
    label = "working" if working_tree else "committed"
    committed = load_json(INVENTORY_JSON, None) if working_tree else committed_inventory()
    if not isinstance(committed, dict):
        print(f"FAIL: no valid {label} inventory; run `steward.py build`", file=sys.stderr)
        sys.exit(1)
    fresh.pop("generatedAt", None)
    committed.pop("generatedAt", None)
    if fresh == committed:
        r = fresh["reconciliation"]
        print(f"OK: {label} inventory matches the real tree "
              f"({r['poolSkills']} pool skills, {r['lockEntries']} lock entries)")
        return
    problems = []
    for key in sorted(set(fresh) | set(committed)):
        if fresh.get(key) != committed.get(key):
            if key == "skills":
                fa, ca = fresh["skills"], committed["skills"]
                for name in sorted(set(fa) | set(ca)):
                    if fa.get(name) != ca.get(name):
                        problems.append(f"skill '{name}' differs")
            else:
                problems.append(f"section '{key}' differs")
    print(f"DRIFT: {label} inventory is stale vs the real tree:", file=sys.stderr)
    for p in problems[:20]:
        print(f"  - {p}", file=sys.stderr)
    print("run `steward.py build`" + ("" if working_tree else " and commit the result when authorized"), file=sys.stderr)
    sys.exit(1)


def cmd_brief_add(args):
    try:
        brief = parse_brief(args.file)
    except ValueError as exc:
        print(f"error: invalid brief {args.file}: {exc}", file=sys.stderr)
        sys.exit(1)
    dest = BRIEFS_DIR / f"{brief['member']}.md"
    if dest.exists() and not args.replace:
        print(f"error: {dest} already exists (use --replace to update the self-report)",
              file=sys.stderr)
        sys.exit(1)
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.file, dest)
    print(f"installed brief for member '{brief['member']}' -> {dest}")
    print(f"  capabilities: {', '.join(brief['capabilities'])}")
    if brief.get("tools"):
        print(f"  tools: {', '.join(brief['tools'])}")
    print("curated inventory fields untouched; claims surface via `build`/`who` "
          "under selfReportedClaims")


def cmd_briefs(_args):
    briefs = load_briefs()
    if not briefs:
        print("no capability briefs installed (see briefs/TEMPLATE.md)")
        return
    for b in briefs:
        print(f"{b['member']} (reported {b.get('reported', '?')}): "
              f"{', '.join(b['capabilities'])}"
              + (f" | tools: {', '.join(b['tools'])}" if b.get("tools") else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="reconcile + write generated inventory")
    p_check = sub.add_parser("check", help="verify committed inventory matches the real tree")
    p_check.add_argument("--working-tree", action="store_true",
                         help="check the generated working copy without claiming committed release freshness")

    p_brief = sub.add_parser("brief", help="capability brief operations")
    brief_sub = p_brief.add_subparsers(dest="brief_command", required=True)
    p_add = brief_sub.add_parser("add", help="validate + install a member brief")
    p_add.add_argument("file")
    p_add.add_argument("--replace", action="store_true",
                       help="replace the member's existing self-report")

    sub.add_parser("briefs", help="list installed capability briefs")

    p_ledger = sub.add_parser("ledger", help="tool ledger operations")
    ledger_sub = p_ledger.add_subparsers(dest="ledger_command", required=True)
    p_ladd = ledger_sub.add_parser("add", help="record a member's MCP/connector")
    p_ladd.add_argument("member")
    p_ladd.add_argument("tool")
    p_ladd.add_argument("--kind", choices=["mcp", "connector"], default=None,
                        help="tool kind; omit to keep an existing entry's kind")
    p_ladd.add_argument("--at", help="ISO date/datetime override (default: now)")
    p_ladd.add_argument("--note", default="")
    p_lverify = ledger_sub.add_parser("verify", help="refresh lastVerifiedAt")
    p_lverify.add_argument("member")
    p_lverify.add_argument("tool")
    p_lshow = ledger_sub.add_parser("show", help="show ledger with staleness")
    p_lshow.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)

    p_who = sub.add_parser("who", help="discover skill candidates, member claims and recorded tools")
    p_who.add_argument("query", nargs="+")

    args = parser.parse_args()

    if args.command == "build":
        cmd_build(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "brief":
        if args.brief_command == "add":
            cmd_brief_add(args)
    elif args.command == "briefs":
        cmd_briefs(args)
    elif args.command == "ledger":
        if args.ledger_command == "add":
            ledger_add(args.member, args.tool, args.kind, args.at, args.note)
        elif args.ledger_command == "verify":
            ledger_verify(args.member, args.tool)
        elif args.ledger_command == "show":
            ledger_show(args.stale_days)
    elif args.command == "who":
        who(args.query)


if __name__ == "__main__":
    main()
