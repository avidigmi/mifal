#!/usr/bin/env python3
"""Recruitment flow for the team-lead skill (spec 0001, ticket 08).

The lead notices the fleet lacks a capability, searches the skill pool, and
pitches a new specialist to the HUMAN. Hiring is human-reserved, always: the
pitch file carries a decision section that only the human fills in, and the
onboarding step reads the decision from that file — never from a flag.

  gaps --mission FILE      Detect capability gaps: mission capabilities vs
                           the fleet (member capability briefs + tool ledger).
                           Read-only; exit 0 whether or not gaps exist.
  pitch --mission FILE     For each gap, search the pool (generated skill
                           inventory) for candidate skills and write a durable
                           pitch file per gap into pitches/. Writes NOTHING
                           when the fleet covers the mission (no false
                           positives). Gaps with no pool candidate are logged
                           in gaps.md and skipped.
  onboard PITCH [--project DIR] [--out DIR] [--task N]
                           Approval-gated hire. Refuses unless the pitch's
                           frontmatter decision is `approved` (written by the
                           human). On approval: onboards the member through
                           scripts/resolve_role.py (role defaults + project
                           overrides, RESOLUTION.md) and installs the member's
                           capability brief via `steward.py brief add`.
  record PITCH             Decline path. Requires decision `declined` with a
                           non-empty decision-reason (the human's words).
                           Logs the decline in gaps.md with the reason; the
                           gap stays visible as OPEN. Declining is recorded,
                           never punished.

Coverage model: the FLEET is its members — their self-reported capability
briefs (stewardship/briefs/, read live via steward.load_briefs) plus the
lead-maintained tool ledger. The skill POOL (generated/skill-inventory.json)
is the search space for candidates, never coverage — otherwise nothing would
ever be a gap. A capability is covered when its token set is contained in a
claimed capability's or ledgered tool's token set (or exact name match).

Exit codes (family convention): 0 success, 1 refusal/failure, 2 usage.

Stdlib only. Reuses steward.py as a module (frontmatter parsing, tokenizer,
brief/ledger loaders, member-name validation) and calls steward.py and
resolve_role.py as subprocesses for the write paths they own.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
STEWARDSHIP = SKILL_ROOT / "stewardship"
RECRUITMENT = SKILL_ROOT / "recruitment"
sys.path.insert(0, str(STEWARDSHIP))

# Module reuse of ticket 02's steward.py: this imports the on-disk version at
# runtime (not a vendored copy), so we are coupled to these symbols:
# now_iso, read_frontmatter, tokenize, load_json, load_briefs, load_ledger,
# iter_ledger_tools, MEMBER_NAME_RE. A steward.py refactor must keep them.
import steward  # noqa: E402  (team-lead stewardship CLI, ticket 02)

INVENTORY_JSON = STEWARDSHIP / "generated" / "skill-inventory.json"
STEWARD_PY = STEWARDSHIP / "steward.py"
RESOLVE_ROLE_PY = SKILL_ROOT / "scripts" / "resolve_role.py"
PITCHES_DIR = RECRUITMENT / "pitches"
GAPS_LEDGER = RECRUITMENT / "gaps.md"
ONBOARDED_DIR = RECRUITMENT / "onboarded"

DECISIONS = ("undecided", "approved", "declined")
# Role names are role-folder names (`implementer`, `_generic`): bare names,
# never flags or paths (a `role: --help` must never reach a subprocess).
ROLE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def as_dict(value):
    """Coerce hand-edited JSON values: anything non-dict becomes {}."""
    return value if isinstance(value, dict) else {}


def ledger_cell(text):
    """One pipe-table cell: no `|` or newlines can corrupt the row."""
    return str(text).replace("|", "/").replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# mission + fleet picture
# ---------------------------------------------------------------------------


def load_mission(path):
    """Mission file: frontmatter `capabilities:` (comma list), optional
    `mission:` title and `role:` default hire role; body is free text."""
    p = Path(path)
    if not p.is_file():
        print(f"error: mission file not found: {p}", file=sys.stderr)
        sys.exit(2)
    try:
        fm, body = steward.read_frontmatter(p)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read mission file {p}: {exc}", file=sys.stderr)
        sys.exit(2)
    caps = [c.strip() for c in fm.get("capabilities", "").split(",") if c.strip()]
    if not caps:
        print(f"error: mission {p} has no `capabilities:` frontmatter list "
              f"(comma-separated capability names the mission requires)",
              file=sys.stderr)
        sys.exit(2)
    role = fm.get("role", "_generic")
    title = fm.get("mission", p.stem)
    return {"path": p, "title": title, "role": role, "capabilities": caps,
            "body": body.strip()}


def tokens_of(text):
    return set(steward.tokenize(text.replace("-", " ")))


def fleet_claims():
    """capability name -> [members], from live member briefs."""
    claims = {}
    for brief in steward.load_briefs():
        for cap in brief["capabilities"]:
            claims.setdefault(cap, []).append(brief["member"])
    return claims


def fleet_tools():
    """tool name -> [members], from the tool ledger."""
    tools = {}
    for member, t in steward.iter_ledger_tools(steward.load_ledger()):
        tools.setdefault(t["name"], []).append(member)
    return tools


def coverage_for(cap, claims, tools):
    """Return (kind, name, members) covering the capability, or None.

    Multi-token capabilities may be covered by a more specific claim (token
    containment). A single-token capability needs an exact name match —
    otherwise one common word like `design` would be "covered" by every
    claim that happens to contain it."""
    if not cap:
        return None
    tokens = tokens_of(cap)
    for source, kind in ((claims, "capability brief"), (tools, "tool ledger")):
        for name, members in source.items():
            if cap == name or (len(tokens) > 1 and tokens <= tokens_of(name)):
                return (kind, name, sorted(members))
    return None


def detect_gaps(mission):
    """Split mission capabilities into (covered, gaps)."""
    claims, tools = fleet_claims(), fleet_tools()
    covered, gaps = [], []
    for cap in mission["capabilities"]:
        cov = coverage_for(cap, claims, tools)
        if cov:
            covered.append((cap, cov))
        else:
            gaps.append(cap)
    return covered, gaps


def load_pool():
    if not INVENTORY_JSON.exists():
        print(f"error: no generated inventory at {INVENTORY_JSON}; run "
              f"`python3 stewardship/steward.py build` first", file=sys.stderr)
        sys.exit(1)
    inv = steward.load_json(INVENTORY_JSON, None)
    if not isinstance(inv, dict) or not isinstance(inv.get("skills"), dict):
        print(f"error: {INVENTORY_JSON} is not a steward.py inventory; "
              f"rebuild with `python3 stewardship/steward.py build`",
              file=sys.stderr)
        sys.exit(1)
    # warn-and-skip hand-edited malformed entries (iter_ledger_tools
    # precedent): a string/list entry must not crash candidate search
    skills = {}
    for name, entry in inv["skills"].items():
        if not isinstance(entry, dict):
            print(f"warning: skipping malformed inventory entry {name!r} "
                  f"(expected an object, got {type(entry).__name__})",
                  file=sys.stderr)
            continue
        skills[name] = entry
    inv["skills"] = skills
    return inv


def find_candidates(gap, skills, limit=3):
    """Rank pool skills against a gap. Returns [(score, name, hits)]."""
    tokens = tokens_of(gap)
    scored = []
    for name, s in skills.items():
        curated = as_dict(s.get("curated")).get("notes", "")
        catalog = as_dict(s.get("catalog")).get("purpose", "")
        description = s.get("description", "")
        if not isinstance(description, str):
            description = ""
        haystack = f"{name.replace('-', ' ')} {description} {catalog} {curated}"
        hay_tokens = set(steward.tokenize(haystack))
        hits = [t for t in tokens if t in hay_tokens]
        if hits:
            score = len(hits) + 2 * sum(1 for t in tokens if t in name)
            scored.append((score, name, hits))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:limit]


# ---------------------------------------------------------------------------
# gaps ledger (append-only; current state per pitch is its LAST row)
# ---------------------------------------------------------------------------


def ledger_append(date, mission, gap, pitch, status, note=""):
    if not GAPS_LEDGER.exists():
        GAPS_LEDGER.write_text(
            "# Known capability gaps (append-only)\n\n"
            "One row per event; the current state of a gap/pitch is its LAST "
            "row. A declined or unfilled gap stays visible here as OPEN — it "
            "is information, not a failure. Written by `recruit.py`.\n\n"
            "| date | mission | gap | pitch | status | note |\n"
            "|------|---------|-----|-------|--------|------|\n",
            encoding="utf-8")
    cells = [ledger_cell(x) for x in (date, mission, gap, pitch, status, note)]
    with GAPS_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write("| " + " | ".join(cells) + " |\n")


# ---------------------------------------------------------------------------
# pitch artifact
# ---------------------------------------------------------------------------


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "gap"


def render_pitch(pitch_id, mission, gap, member, role, candidate, candidates,
                 skill, covered):
    created = steward.now_iso()
    origin = as_dict(skill.get("origin"))
    if origin.get("type") == "github-lock":
        origin_line = f"github-lock (source: {origin.get('source')}); the skill is already installed — nothing to fetch"
    else:
        origin_line = "local pool skill, already on disk — nothing to fetch"
    maturity = skill.get("maturity", "?")
    owner = skill.get("owner", "?")
    gap_tokens = tokens_of(gap)
    strong = gap_tokens and gap_tokens <= tokens_of(candidate)
    description = skill.get("description")
    if not isinstance(description, str) or not description:
        description = "(no description)"
    if strong:
        recommendation = (
            f"**APPROVE.** Pool skill `{candidate}` matches the gap directly "
            f"(maturity: {maturity}, owner: {owner}). The fleet simply has no "
            f"member carrying it yet.")
    else:
        recommendation = (
            f"**REVIEW CAREFULLY before approving.** Best pool candidate "
            f"`{candidate}` is only a partial token match for `{gap}` "
            f"(matched: {', '.join(candidates[0][2])}). Read the skill's "
            f"description above and judge the fit yourself — I would rather "
            f"hold this pitch than hire a near-miss.")
    runners_up = [n for _s, n, _h in candidates[1:]]
    covered_lines = "\n".join(
        f"- `{cap}` — covered by {', '.join(members)} ({kind})"
        for cap, (kind, _name, members) in covered) or "- (none)"
    fm = [
        "---",
        f"pitch: {pitch_id}",
        f"created: {created}",
        f"mission: {mission['title']}",
        f"mission-file: {mission['path']}",
        f"gap: {gap}",
        f"candidate-skill: {candidate}",
        f"proposed-member: {member}",
        f"role: {role}",
        "status: pitched",
        "decision: undecided",
        "decision-reason:",
        "decided-by:",
        "---",
    ]
    body = f"""
# Recruitment pitch — {member} for `{gap}`

**To: the human. From: the lead.** Hiring is human-reserved; this file is the
decision record. Nothing downstream runs until you decide (below).

## Who

Proposed specialist **{member}**, carrying pool skill **{candidate}** —
{description}

- maturity: {maturity} · owner: {owner} · origin: {origin_line}
- role folder: `{role}` (onboarding runs through `scripts/resolve_role.py`,
  shipped defaults + project overrides, with RESOLUTION.md naming every source)
- other pool candidates considered: {', '.join(f'`{r}`' for r in runners_up) or 'none'}

## Why — the gap

Mission "{mission['title']}" requires `{gap}`. No fleet member claims it in a
capability brief, and no member's ledgered MCP/connector provides it. What the
fleet already covers for this mission:

{covered_lines}

The gap was detected against live member briefs and the tool ledger, not the
pool: the pool having the skill is exactly why a hire can fix it.

## What it enables

Hiring {member} puts `{gap}` on the roster, closing the last uncovered
capability of mission "{mission['title']}" — the mission goes from blocked on
staffing to fully assignable through the normal `steward.py who` path.

## Cost

Honest, in this fleet's terms:

- one more member on the roster: one more pane on the wall to watch, one more
  capacity ledger (`people/{member}.md`) and tool ledger to steward;
- the skill is already in the pool ({origin_line.split(';')[0]}) — no install,
  no new external dependency;
- onboarding cost is minutes, not explanation: the `{role}` role folder
  carries the onboarding pointers and brief template;
- if it doesn't work out, the decline/removal path is the same record
  (`gaps.md`) — the experiment is cheap and reversible.

## Lead's recommendation

{recommendation}

## Decision — human only

Only the human edits the three frontmatter lines at the top of this file:

- `decision:` — set to `approved` or `declined` (leave `undecided` to defer)
- `decision-reason:` — your words; REQUIRED when declining (the gap stays on
  the record with your reason attached)
- `decided-by:` — your name or handle

The tooling reads the decision from this file only, never from a flag. After
deciding, tell the lead (or run yourself):

- approved: `python3 recruitment/recruit.py onboard recruitment/pitches/{pitch_id}.md`
- declined: `python3 recruitment/recruit.py record recruitment/pitches/{pitch_id}.md`
"""
    return "\n".join(fm) + body


def write_pitch(mission, gap, member, role, candidate, candidates, skill, covered):
    date = steward.now_iso()[:10]
    base = f"p-{date}-{slugify(gap)}"
    pitch_id, n = base, 2
    while (PITCHES_DIR / f"{pitch_id}.md").exists():
        pitch_id, n = f"{base}-{n}", n + 1
    PITCHES_DIR.mkdir(parents=True, exist_ok=True)
    path = PITCHES_DIR / f"{pitch_id}.md"
    path.write_text(
        render_pitch(pitch_id, mission, gap, member, role, candidate,
                     candidates, skill, covered),
        encoding="utf-8")
    return pitch_id, path


# ---------------------------------------------------------------------------
# pitch decision parsing (the gate)
# ---------------------------------------------------------------------------


def read_pitch(path):
    p = Path(path)
    if not p.is_file():
        print(f"error: pitch file not found: {p}", file=sys.stderr)
        sys.exit(2)
    try:
        fm, _body = steward.read_frontmatter(p)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read pitch file {p}: {exc}", file=sys.stderr)
        sys.exit(2)
    missing = [k for k in ("pitch", "gap", "candidate-skill",
                           "proposed-member", "role", "status", "decision")
               if k not in fm]
    if missing:
        print(f"error: {p} is not a recruitment pitch (missing frontmatter: "
              f"{', '.join(missing)})", file=sys.stderr)
        sys.exit(1)
    if fm["decision"] not in DECISIONS:
        print(f"error: pitch {fm['pitch']} has decision "
              f"{fm['decision']!r}; the human sets it to one of: "
              f"{', '.join(DECISIONS)}", file=sys.stderr)
        sys.exit(1)
    if not steward.MEMBER_NAME_RE.fullmatch(fm["proposed-member"]):
        print(f"error: proposed-member {fm['proposed-member']!r} is not a "
              f"valid member name ({steward.MEMBER_NAME_RE.pattern})",
              file=sys.stderr)
        sys.exit(1)
    fm["_path"] = p
    return fm


def rewrite_pitch_status(pitch, new_status, outcome_lines):
    """Lead-side update after the human's decision: status line + Outcome."""
    text = pitch["_path"].read_text(encoding="utf-8")
    new_text, count = re.subn(rf"^status: .*$", f"status: {new_status}",
                              text, count=1, flags=re.MULTILINE)
    if count != 1:
        print(f"error: could not update status line in {pitch['_path']}",
              file=sys.stderr)
        sys.exit(1)
    new_text += "\n## Outcome\n\n" + outcome_lines + "\n"
    pitch["_path"].write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_gaps(args):
    mission = load_mission(args.mission)
    covered, gaps = detect_gaps(mission)
    print(f"Mission: {mission['title']} ({mission['path']})")
    print(f"Required capabilities: {', '.join(mission['capabilities'])}")
    print()
    for cap, (kind, name, members) in covered:
        print(f"  COVERED  {cap} — {', '.join(members)} ({kind}: {name})")
    for cap in gaps:
        print(f"  GAP      {cap} — no member brief claims it, no ledgered tool provides it")
    print()
    if gaps:
        print(f"{len(gaps)} gap(s): {', '.join(gaps)}")
        print("next: `python3 recruitment/recruit.py pitch --mission "
              f"{mission['path']}`")
    else:
        print("the fleet covers this mission — nothing to pitch")
    return 0


def cmd_pitch(args):
    mission = load_mission(args.mission)
    if args.role:
        mission["role"] = args.role
    covered, gaps = detect_gaps(mission)
    if not gaps:
        print(f"the fleet covers mission \"{mission['title']}\" "
              f"({', '.join(mission['capabilities'])}) — no pitch written")
        return 0
    inv = load_pool()
    skills = inv["skills"]
    date = steward.now_iso()[:10]
    wrote = 0
    for gap in gaps:
        candidates = find_candidates(gap, skills)
        if not candidates:
            print(f"gap `{gap}`: no candidate in the skill pool — logged as "
                  f"OPEN, no pitch written (the lead researches outside the "
                  f"pool before pitching)")
            ledger_append(date, mission["title"], gap, "—",
                          "OPEN — no pool candidate")
            continue
        _score, candidate, _hits = candidates[0]
        member = args.member or candidate
        if not steward.MEMBER_NAME_RE.fullmatch(member):
            print(f"error: proposed member name {member!r} is invalid "
                  f"({steward.MEMBER_NAME_RE.pattern}); pass --member",
                  file=sys.stderr)
            return 1
        pitch_id, path = write_pitch(
            mission, gap, member, mission["role"], candidate, candidates,
            skills[candidate], covered)
        ledger_append(date, mission["title"], gap, pitch_id,
                      "pitched — awaiting human decision")
        wrote += 1
        print(f"pitch written: {path}")
        print(f"  who: {member} (skill: {candidate}, role: {mission['role']})")
        print(f"  why: gap `{gap}` — no fleet coverage")
        print("  next: the HUMAN edits the decision lines in the pitch file")
    if wrote:
        print(f"\n{wrote} pitch(es) awaiting the human in {PITCHES_DIR}/; "
              f"ledger: {GAPS_LEDGER}")
    return 0


def cmd_onboard(args):
    pitch = read_pitch(args.pitch)
    pid = pitch["pitch"]
    if pitch["status"] == "hired":
        print(f"error: pitch {pid} is already hired; refusing to onboard "
              f"twice", file=sys.stderr)
        return 1
    if pitch["decision"] == "undecided":
        print(f"REFUSED: pitch {pid} is UNDECIDED. Hiring is human-reserved: "
              f"the human edits `decision:` in {pitch['_path']} to `approved` "
              f"or `declined`; onboarding never reads the decision from a "
              f"flag.", file=sys.stderr)
        return 1
    if pitch["decision"] == "declined":
        print(f"REFUSED: the human declined pitch {pid}. Record it instead: "
              f"`python3 recruitment/recruit.py record {pitch['_path']}`",
              file=sys.stderr)
        return 1
    decided_by = pitch.get("decided-by", "").strip()
    if not decided_by:
        print(f"REFUSED: pitch {pid} is approved but `decided-by:` is empty "
              f"— the record must name the human who approved. Ask the human "
              f"to fill it in {pitch['_path']}.", file=sys.stderr)
        return 1

    member, role, gap = pitch["proposed-member"], pitch["role"], pitch["gap"]
    if not ROLE_NAME_RE.fullmatch(role):
        print(f"error: pitch {pid} has invalid role {role!r} (must match "
              f"{ROLE_NAME_RE.pattern} — a bare role-folder name, never a "
              f"flag or path); refusing to run the resolver", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else ONBOARDED_DIR / member
    date = steward.now_iso()[:10]

    # 1. onboarding through the role folder resolver (ticket 05)
    cmd = [sys.executable, str(RESOLVE_ROLE_PY), role,
           "--member", member, "--task", args.task,
           "--title", f"cover `{gap}` for mission \"{pitch['mission']}\"",
           "--out", str(out)]
    if args.project:
        cmd += ["--project", str(args.project)]
    print(f"onboarding {member} through role folder '{role}'...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        print(f"error: resolve_role.py failed ({result.returncode}); hire "
              f"NOT completed — the pitch stays `pitched`", file=sys.stderr)
        return 1
    if not (out / "RESOLUTION.md").is_file():
        # exit 0 alone is not proof: the resolver's report must exist
        print(f"error: {out}/RESOLUTION.md was not produced by "
              f"resolve_role.py despite exit 0; hire NOT completed — the "
              f"pitch stays `pitched`", file=sys.stderr)
        return 1

    # 2. capability brief through the steward's validated path (ticket 02)
    brief_path = out / "capability-brief.md"
    caps = sorted({gap, pitch["candidate-skill"]})
    brief_path.write_text(
        "---\n"
        f"member: {member}\n"
        f"reported: {date}\n"
        f"capabilities: {', '.join(caps)}\n"
        f"evidence: recruited via pitch {pid} ({pitch['_path'].name}); "
        f"approved by {decided_by} on {date}; onboarded through role "
        f"folder '{role}'\n"
        "status: active\n"
        "---\n\n"
        f"Recruited specialist carrying pool skill "
        f"`{pitch['candidate-skill']}` to close the `{gap}` capability gap "
        f"for mission \"{pitch['mission']}\". Onboarding resolution: "
        f"{out}/RESOLUTION.md.\n",
        encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(STEWARD_PY), "brief", "add", str(brief_path)],
        capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        print(f"error: steward.py brief add failed; onboarding files are in "
              f"{out} but the inventory did NOT gain the brief",
              file=sys.stderr)
        return 1

    # 3. record the hire
    rewrite_pitch_status(
        pitch, "hired",
        f"- decision: **approved** by {decided_by}"
        + (f" — {pitch['decision-reason']}" if pitch.get("decision-reason", "").strip() else "")
        + f"\n- onboarded {date}: role `{role}` via `scripts/resolve_role.py` -> {out}/ (see RESOLUTION.md)"
        f"\n- capability brief installed -> stewardship/briefs/{member}.md (surfaces in the inventory on the next `steward.py build`)")
    ledger_append(date, pitch["mission"], gap, pid,
                  f"FILLED — hired {member}")
    print(f"\nhired {member} (skill: {pitch['candidate-skill']}); pitch {pid} "
          f"-> status: hired; gap recorded FILLED in {GAPS_LEDGER}")
    return 0


def cmd_record(args):
    pitch = read_pitch(args.pitch)
    pid = pitch["pitch"]
    if pitch["status"] in ("hired", "declined"):
        print(f"error: pitch {pid} is already {pitch['status']}; nothing to "
              f"record", file=sys.stderr)
        return 1
    if pitch["decision"] == "undecided":
        print(f"REFUSED: pitch {pid} is UNDECIDED. The human edits "
              f"`decision:` in {pitch['_path']} first; there is no flag that "
              f"decides for them.", file=sys.stderr)
        return 1
    if pitch["decision"] == "approved":
        print(f"REFUSED: the human APPROVED pitch {pid}. Onboard instead: "
              f"`python3 recruitment/recruit.py onboard {pitch['_path']}`",
              file=sys.stderr)
        return 1
    reason = pitch.get("decision-reason", "").strip()
    if not reason:
        print(f"error: pitch {pid} is declined but `decision-reason:` is "
              f"empty. The decline is recorded WITH the human's reason — ask "
              f"the human to write it in {pitch['_path']}.", file=sys.stderr)
        return 1
    decided_by = pitch.get("decided-by", "").strip() or "(the human)"
    date = steward.now_iso()[:10]
    rewrite_pitch_status(
        pitch, "declined",
        f"- decision: **declined** by {decided_by}\n"
        f"- reason (human's words): {reason}\n"
        f"- the gap stays OPEN in {GAPS_LEDGER} — information, not a failure")
    ledger_append(date, pitch["mission"], pitch["gap"], pid,
                  "DECLINED — gap remains OPEN", note=reason)
    print(f"decline recorded: pitch {pid} -> status: declined")
    print(f"  reason: {reason}")
    print(f"  gap `{pitch['gap']}` remains OPEN in {GAPS_LEDGER}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_gaps = sub.add_parser("gaps", help="detect capability gaps for a mission")
    p_gaps.add_argument("--mission", required=True, help="mission file")

    p_pitch = sub.add_parser("pitch", help="write pitch files for the gaps")
    p_pitch.add_argument("--mission", required=True, help="mission file")
    p_pitch.add_argument("--role", help="override the mission's hire role")
    p_pitch.add_argument("--member", help="override the proposed member name")

    p_onb = sub.add_parser("onboard", help="approval-gated hire from a pitch")
    p_onb.add_argument("pitch", help="pitch file under pitches/")
    p_onb.add_argument("--project", type=Path,
                       help="project root with .team/roles/<role>/ overrides")
    p_onb.add_argument("--out", help="onboarding output dir "
                                     "(default: recruitment/onboarded/<member>/)")
    p_onb.add_argument("--task", default="1",
                       help="starter brief task id (default: 1)")

    p_rec = sub.add_parser("record", help="record a human decline (+ reason)")
    p_rec.add_argument("pitch", help="pitch file under pitches/")

    args = parser.parse_args()
    if args.command == "gaps":
        sys.exit(cmd_gaps(args))
    if args.command == "pitch":
        sys.exit(cmd_pitch(args))
    if args.command == "onboard":
        sys.exit(cmd_onboard(args))
    if args.command == "record":
        sys.exit(cmd_record(args))


if __name__ == "__main__":
    main()
