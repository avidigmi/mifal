#!/usr/bin/env python3
"""Tiered AGENTS.md compliance checking (spec 0001, ticket 07).

Stdlib only. Rules in a project's AGENTS.md are classified into tiers matched
to what each rule protects:

  Tier 0  safety hard lines    — enforced: a violation blocks dispatch
  Tier 1  project conventions  — checked per task, reported in the task's
                                 compliance report
  Tier 2  style preferences    — advised, never blocking

Classification is heuristic (AGENTS.md is prose): shipped default patterns
(stewardship/compliance-tiers.json) plus a per-project override file
(.compliance-overrides.json at the project root). Uncertainty always resolves
to the MORE conservative tier and says so; no rule is ever lowered silently.

The dispatch gate is a library function (gate_dispatch) plus a CLI with a JSON
in/out seam, so the socket adapter and the lead loop (ticket 06) can call it.
Compliance results land in the team record: a per-task sidecar under
tasks/<id>/ and an append-only compliance-log.jsonl at the record root, so a
repeated violation is visible as repeated.

Usage:
  check_compliance.py rules PROJECT [--json]
  check_compliance.py check PROJECT --evidence FILE [--json]
  check_compliance.py gate PROJECT --evidence FILE
  check_compliance.py record PROJECT --evidence FILE --record DIR
  check_compliance.py history --record DIR [--rule PATTERN] [--member NAME] [--json]
  check_compliance.py selfcheck

PROJECT is a directory holding AGENTS.md (plus an optional
.compliance-overrides.json). Evidence is a JSON object describing what the
lead knows about the task at check time:

  {"task": "7", "attempt": "7.1", "member": "kai",
   "files_changed": ["src/x.ts", ".env"],
   "commands": ["pnpm test", "git commit -m 'feat: x'"],
   "planned_commands": ["npm publish"],
   "commit_messages": ["feat: x"],
   "claims": [{"claim": "tests", "status": "passed"}],
   "approved_by": null}

A field that is PRESENT but empty was checked and found clean; a field whose
key is ABSENT was never checked, and armed detectors say so (uncheckable
entries) instead of reporting silent clean. A gate call with no checkable
field at all refuses: nothing was actually checked.

'-' as the evidence path reads stdin. Exit codes: 0 ok / gate allows,
1 findings / gate refuses, 2 usage error.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shlex
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS_FILE = (
    Path(__file__).resolve().parent.parent
    / "stewardship" / "compliance-tiers.json"
)
OVERRIDE_NAME = ".compliance-overrides.json"
LOG_NAME = "compliance-log.jsonl"

TIER_ORDER = (0, 1, 2)  # 0 is the most conservative

# Evidence fields any detector can consume; a gate call with none of them
# checked nothing and refuses.
CHECKABLE_FIELDS = (
    "files_changed", "commands", "planned_commands", "commit_messages", "claims",
)

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class UsageError(Exception):
    pass


class Uncheckable(Exception):
    """A detector met evidence it cannot model; the rule is reported
    uncheckable instead of clean."""


# ---------------------------------------------------------------------------
# rule extraction and tier classification
# ---------------------------------------------------------------------------

BULLET_RE = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s+(.*\S)\s*$")
FENCE_RE = re.compile(r"^\s*```")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->")


def extract_rules(text: str):
    """Rule statements are list items outside code fences (AGENTS.md rules are
    bullets; headings and prose give context, they are not rules)."""
    rules = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = BULLET_RE.match(line)
        if not m:
            continue
        body = HTML_COMMENT_RE.sub("", m.group(1)).strip()
        if body:
            rules.append({"line": lineno, "text": body})
    return rules


def rule_key(text: str) -> str:
    """Stable identity across runs: normalized text hash. Note: rewording a
    rule changes its key — history treats the reworded rule as new."""
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def load_tier_config(project_root: Path, defaults_path: Path | None = None):
    """Return (defaults, overrides, tier_config_note). Raises UsageError."""
    dpath = defaults_path or DEFAULTS_FILE
    if not dpath.is_file():
        raise UsageError(f"shipped tier defaults not found: {dpath}")
    try:
        defaults = json.loads(dpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"tier defaults {dpath} is not valid JSON: {e}")
    overrides = []
    opath = project_root / OVERRIDE_NAME
    if opath.is_file():
        try:
            raw = json.loads(opath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise UsageError(f"override file {opath} is not valid JSON: {e}")
        for i, ov in enumerate(raw.get("overrides", [])):
            if not isinstance(ov, dict) or "match" not in ov or "tier" not in ov:
                raise UsageError(
                    f"override #{i + 1} in {opath} needs 'match' (regex) and "
                    f"'tier' (0|1|2); got {ov!r}"
                )
            if ov["tier"] not in TIER_ORDER:
                raise UsageError(
                    f"override #{i + 1} in {opath}: tier must be 0, 1, or 2; "
                    f"got {ov['tier']!r}"
                )
            try:
                re.compile(ov["match"])
            except re.error as e:
                raise UsageError(
                    f"override #{i + 1} in {opath}: bad regex "
                    f"'{ov['match']}': {e}"
                )
            overrides.append(ov)
    note = {
        "defaults": str(dpath),
        "override": str(opath) if overrides else None,
        "unmatched_overrides": [],
    }
    return defaults, overrides, note


def heuristic_tier(text: str, defaults: dict):
    """Return (tier, confidence, notes). Several tiers matched → the most
    conservative wins, and the note says so. Nothing matched → the configured
    unmatched default (never a silent lower tier)."""
    matched = []
    for tier_s, spec in defaults["tiers"].items():
        for pat in spec.get("patterns", []):
            if re.search(pat, text, re.I):
                matched.append(int(tier_s))
                break
    notes = []
    if not matched:
        tier = int(defaults.get("unmatched_default_tier", 1))
        notes.append(
            f"no tier pattern matched; defaulted to tier {tier} "
            f"(checked and reported) rather than guessing lower"
        )
        return tier, "low", notes
    tier = min(matched)
    if len(matched) > 1:
        notes.append(
            f"matched patterns for tiers {sorted(set(matched))}; "
            f"took the more conservative tier {tier}"
        )
    return tier, "heuristic", notes


def classify_rules(project_root: Path, defaults_path: Path | None = None):
    """Load AGENTS.md + tier config, return (rules, tier_config_note, defaults).

    Override resolution: every override whose regex matches a rule applies;
    the rule takes the MOST conservative (lowest) of their tiers, and the
    notes name them all. Overrides matching no rule are reported in the tier
    config note (a typo'd override must not vanish silently)."""
    agents_md = project_root / "AGENTS.md"
    if not agents_md.is_file():
        raise UsageError(f"no AGENTS.md at {agents_md}")
    defaults, overrides, note = load_tier_config(project_root, defaults_path)
    used = set()
    rules = []
    for i, raw in enumerate(extract_rules(agents_md.read_text(encoding="utf-8")), 1):
        text = raw["text"]
        h_tier, confidence, notes = heuristic_tier(text, defaults)
        entry = {
            "id": f"r{i}",
            "key": rule_key(text),
            "line": raw["line"],
            "text": text,
            "tier": h_tier,
            "tier_source": "patterns" if confidence == "heuristic" else "fallback",
            "confidence": confidence,
            "notes": notes,
        }
        matched_ovs = []
        for j, ov in enumerate(overrides):
            if re.search(ov["match"], text, re.I):
                matched_ovs.append(ov)
                used.add(j)
        if matched_ovs:
            tier = min(ov["tier"] for ov in matched_ovs)
            ov_notes = [
                f"override '{ov['match']}' set tier {ov['tier']}"
                + (f" ({ov['why']})" if ov.get("why") else "")
                for ov in matched_ovs
            ]
            if len(matched_ovs) > 1:
                ov_notes.append(
                    f"{len(matched_ovs)} overrides matched; took the most "
                    f"conservative tier {tier}"
                )
            ov_notes.append(f"heuristic said {h_tier}")
            entry.update(
                tier=tier, tier_source="override", confidence="configured",
                heuristic_tier=h_tier, notes=ov_notes,
            )
        rules.append(entry)
    note["unmatched_overrides"] = [
        ov for j, ov in enumerate(overrides) if j not in used
    ]
    return rules, note, defaults


# ---------------------------------------------------------------------------
# command tokenization and dangerous-command families
# ---------------------------------------------------------------------------

def split_command(cmd: str):
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


# The non-simple controls end the command before them too (zsh "&!", "&|";
# "|&" pipes; ";;" ";&" ";|" end a case item), so they split like ";".
SEPARATORS = {";", "&&", "||", "|", "&", "&!", "&|", "|&", ";;", ";&", ";|"}
SHELL_PUNCTUATION = "();<>|&"
# Every operator spelling, longest first. An operator is the longest listed
# spelling at its position, never a whole punctuation run: a greedy run read
# ";>" as one unknown operator and hid the command before it.
OPERATORS = sorted(SEPARATORS | {
    ">>&|", ">>&!", "&>>|", "&>>!",
    "<<-", "<<<", ">>|", ">>!", ">&|", ">&!", "&>|", "&>!", ">>&", "&>>",
    "<<", ">>", "<&", ">&", "<>", ">|", ">!", "&>",
    "<", ">", "(", ")",
}, key=len, reverse=True)
NESTED_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def command_segments(cmd: str):
    """The simple commands joined by a SEPARATORS operator or a newline in an evidence
    command, each as (tokens, why_unsupported). Operators are found in the raw
    text while quote and backslash state is still known (POSIX decoding erases
    it, so a quoted ";" would otherwise split a command), then each segment is
    POSIX-decoded. A segment using substitution, expansion, redirection, a
    subshell or a nested shell is not modeled and carries the reason; its
    siblings are still evaluated. Unbalanced quotes or a trailing backslash
    cannot be segmented and raise Uncheckable. Nothing is executed."""
    raw_segments, buf, why, quote, i = [], [], None, None, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote == "'":
            quote = None if ch == "'" else quote
            buf.append(ch)
            i += 1
            continue
        if ch == "\\":
            if i + 1 == len(cmd):
                raise Uncheckable(f"command '{cmd}' has a trailing escape")
            if cmd[i + 1] != "\n":  # an unquoted backslash-newline is a line continuation: both are removed
                buf.append(cmd[i:i + 2])
            i += 2
            continue
        if quote:
            quote = None if ch == '"' else quote
            buf.append(ch)
            i += 1
            continue
        if ch == "\n" or ch in SHELL_PUNCTUATION:
            op = ch if ch == "\n" else next(o for o in OPERATORS if cmd.startswith(o, i))
            end = i + len(op)
            if op == "\n" or op in SEPARATORS:
                raw_segments.append(("".join(buf), why))
                buf, why = [], None
            else:
                why = why or f"unsupported shell syntax '{op}'"
                buf.append(f" {op} ")
            i = end
            continue
        if ch in "'\"":
            quote = ch
        buf.append(ch)
        i += 1
    if quote:
        raise Uncheckable(f"command '{cmd}' has unbalanced quoting")
    raw_segments.append(("".join(buf), why))
    result = []
    for raw, why in raw_segments:
        try:
            seg = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            raise Uncheckable(f"command '{cmd}' has unbalanced quoting or escaping")
        if "$" in raw or "`" in raw:
            why = why or "substitution or expansion"
        names = {t.rsplit("/", 1)[-1] for t in seg}
        if not why and ("eval" in names or ("-c" in seg and names & NESTED_SHELLS)):
            why = "a nested shell"
        result.append((seg, why))
    return result


SHORT_FLAG_RUN = re.compile(r"^-([a-zA-Z]+)$")


def has_short_flag(tok: str, letter: str) -> bool:
    m = SHORT_FLAG_RUN.match(tok)
    return bool(m) and letter in m.group(1)


def is_force_flag(tok: str) -> bool:
    # '--force-with-lease' deliberately does NOT match; it is the safe form.
    return tok == "--force" or has_short_flag(tok, "f")


def is_recursive_flag(tok: str) -> bool:
    return (
        tok == "--recursive"
        or has_short_flag(tok, "r")
        or has_short_flag(tok, "R")
    )


def families_in_tokens(tokens, lease_forbidden: bool = False):
    """Dangerous-command families present in a tokenized command, matched by
    verb + flag independently so argument order cannot evade them."""
    low = [t.lower() for t in tokens]
    fams = set()
    if "push" in low and any(
        is_force_flag(t) or (lease_forbidden and t == "--force-with-lease")
        for t in low
    ):
        fams.add("push-force")
    if "reset" in low and "--hard" in low:
        fams.add("reset-hard")
    if "rm" in low:
        flags = [t for t in low if t.startswith("-")]
        if any(is_recursive_flag(t) for t in flags) and any(
            is_force_flag(t) for t in flags
        ):
            fams.add("rm-rf")
    if any(pair == ("drop", "table") for pair in zip(low, low[1:])):
        fams.add("drop-table")
    if "git" in low and "clean" in low:
        fams.add("git-clean")
    return fams


# Prose fallbacks for arming when a rule names a family without a backticked
# example command.
FAMILY_ARM_RES = {
    "push-force": re.compile(
        r"\bpush --force\b|\bpush\s+-[a-z]*f\b|\bforce.?push\b", re.I
    ),
    "reset-hard": re.compile(r"\breset --hard\b", re.I),
    "rm-rf": re.compile(
        r"\brm\s+(?:-[a-z]+\s+)*-[a-z]*(?:r[a-z]*f|f[a-z]*r)[a-z]*\b"
        r"|\brm\s+-[a-z]*r\s+-[a-z]*f\b"
        r"|\brm\s+-[a-z]*f\s+-[a-z]*r\b",
        re.I,
    ),
    "drop-table": re.compile(r"\bdrop\s+table\b", re.I),
    "git-clean": re.compile(r"\bgit clean\b", re.I),
}


# ---------------------------------------------------------------------------
# mechanical detectors (armed by rule text, run against task evidence)
#
# Each detector declares the evidence fields it reads. A rule arms a detector
# only if the rule names that command/check family. A detector runs only when
# at least one of its fields is PRESENT in the evidence (an empty list is a
# checked-and-clean answer; an absent key means nothing was checked and the
# rule is marked uncheckable instead of silently passing).
# ---------------------------------------------------------------------------

NEGATION_RE = re.compile(r"\b(never|do not|don't|must not|no)\b", re.I)
BACKTICK_RE = re.compile(r"`([^`]+)`")
PATHISH_RE = re.compile(r"^[A-Za-z0-9_.*/@-]+$")

APPROVAL_WORD_RE = re.compile(
    r"\b(approval|approved|approve|permission|explicit|confirm(?:ation)?)\b", re.I
)
PUBLISH_RULE_RE = re.compile(r"\b(publish(?:ing)?|deploy(?:s|ment)?|releases?)\b", re.I)
PUBLISH_CMD_RE = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)(?:\s+run)?\s+publish\b"
    r"|\bdeploy\w*\b|\breleases?\b"
    r"|\bdocker\s+push\b|\bgit\s+tag\b|\bgit\s+push\b[^\n&|;]*--tags",
    re.I,
)

CHECK_WORDS = ("test", "lint", "typecheck", "build", "check")
BEFORE_COMMIT_RE = re.compile(
    r"\bbefore (?:commit|push)|\bmust pass\b|\bnever ships\b|\bmust be green\b", re.I
)
CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([\w ,.-]+\))?!?:\s+\S"
)


def evidence_commands(evidence: dict):
    commands = []
    for field in ("commands", "planned_commands"):
        value = evidence.get(field, [])
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            raise UsageError(f"evidence '{field}' must be a list of strings")
        commands += value
    return commands


def arm_forbidden_path(text: str):
    if not NEGATION_RE.search(text):
        return None
    tokens = []
    for tok in BACKTICK_RE.findall(text):
        tok = tok.strip()
        if PATHISH_RE.match(tok) and ("." in tok or "*" in tok or "/" in tok):
            tokens.append(tok)
    return {"tokens": tokens} if tokens else None


def run_forbidden_path(params: dict, evidence: dict):
    for path in evidence.get("files_changed", []):
        base = path.rsplit("/", 1)[-1]
        for tok in params["tokens"]:
            variants = [tok] if "*" in tok else [tok, tok + "*"]
            if any(
                fnmatch.fnmatch(base, v) or fnmatch.fnmatch(path, v)
                for v in variants
            ):
                return f"changed path '{path}' matches forbidden '{tok}'"
    return None


def arm_forbidden_command(text: str):
    lease = "force-with-lease" in text.lower()
    fams = set()
    for span in BACKTICK_RE.findall(text):
        fams |= families_in_tokens(split_command(span), lease_forbidden=lease)
    for fam, rx in FAMILY_ARM_RES.items():
        if rx.search(text):
            fams.add(fam)
    if lease and "push-force" not in fams and not fams:
        # The rule names --force-with-lease itself as forbidden.
        fams.add("push-force")
    if not fams:
        return None
    return {"families": sorted(fams), "lease_forbidden": lease}


def run_forbidden_command(params: dict, evidence: dict):
    armed = set(params["families"])
    unmodeled = None
    for cmd in evidence_commands(evidence):
        try:
            segments = command_segments(cmd)
        except Uncheckable as e:
            unmodeled = unmodeled or e
            continue
        for seg, why in segments:
            if why:
                # A modeled sibling segment still decides; only a clean
                # result is downgraded to uncheckable.
                unmodeled = unmodeled or Uncheckable(f"command '{cmd}' uses {why}")
                continue
            hits = families_in_tokens(seg, params["lease_forbidden"]) & armed
            if hits:
                return f"command '{cmd}' matches forbidden '{sorted(hits)[0]}'"
    if unmodeled:
        raise unmodeled
    return None


def arm_approval_required(text: str):
    if not (APPROVAL_WORD_RE.search(text) and PUBLISH_RULE_RE.search(text)):
        return None
    # The rule names its exact publish commands; arm them as literals too.
    literals = []
    for span in BACKTICK_RE.findall(text):
        toks = split_command(span)
        if toks:
            literals.append(" ".join(toks[:2]))
    return {"literals": literals}


def run_approval_required(_params: dict, evidence: dict):
    if evidence.get("approved_by"):
        return None
    for cmd in evidence_commands(evidence):
        if PUBLISH_CMD_RE.search(cmd) or any(
            lit and lit in cmd for lit in _params["literals"]
        ):
            return (
                f"command '{cmd}' is a publish/deploy action with no recorded "
                f"approval (approved_by is empty)"
            )
    return None


def arm_check_before_commit(text: str):
    if not BEFORE_COMMIT_RE.search(text):
        return None
    words = [w for w in CHECK_WORDS if re.search(rf"\b{w}s?\b", text, re.I)]
    return {"checks": words} if words else None


def run_check_before_commit(params: dict, evidence: dict):
    committed = evidence.get("commit_messages") or any(
        re.search(r"\bgit (?:commit|push)\b", c)
        for c in evidence_commands(evidence)
    )
    if not committed:
        return None
    passed = [
        str(claim.get("claim", "")) for claim in evidence.get("claims", [])
        if isinstance(claim, dict) and claim.get("status") == "passed"
    ]
    # Same word grammar that armed the rule; every required check needs its
    # own passed claim.
    missing = [
        w for w in params["checks"]
        if not any(re.search(rf"\b{w}s?\b", c, re.I) for c in passed)
    ]
    if not missing:
        return None
    return (
        f"changes were committed with no passing claim covering "
        f"{', '.join(missing)}"
    )


def arm_commit_convention(text: str):
    return {} if re.search(r"conventional commits?", text, re.I) else None


def run_commit_convention(_params: dict, evidence: dict):
    bad = [
        m for m in evidence.get("commit_messages", [])
        if not CONVENTIONAL_RE.match(m)
    ]
    if bad:
        return (
            f"commit message '{bad[0]}' is not Conventional Commits "
            f"(expected 'feat: ...', 'fix: ...', ...)"
        )
    return None


DETECTORS = (
    ("forbidden-path", ("files_changed",), arm_forbidden_path, run_forbidden_path),
    ("forbidden-command", ("commands", "planned_commands"),
     arm_forbidden_command, run_forbidden_command),
    ("approval-required", ("commands", "planned_commands"),
     arm_approval_required, run_approval_required),
    ("check-before-commit", ("commands", "planned_commands", "commit_messages"),
     arm_check_before_commit, run_check_before_commit),
    ("commit-convention", ("commit_messages",),
     arm_commit_convention, run_commit_convention),
)


# ---------------------------------------------------------------------------
# the check, the gate, the record
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_task(project_root: Path, evidence: dict,
               defaults_path: Path | None = None) -> dict:
    """Run every armed detector for tier 0/1 rules over the evidence and
    return the full compliance report."""
    project_root = Path(project_root)
    rules, tier_config, defaults = classify_rules(project_root, defaults_path)
    findings, advice, uncheckable = [], [], []
    evidence_commands(evidence)  # malformed command evidence fails before any detector
    for rule in rules:
        if rule["tier"] == 2:
            advice.append({
                "rule_id": rule["id"], "rule": rule["text"], "tier": 2,
                "note": "style preference — advised, never blocking",
            })
            continue
        armed = []
        for name, fields, arm, run in DETECTORS:
            params = arm(rule["text"])
            if params is not None:
                armed.append((name, fields, params, run))
        if not armed:
            uncheckable.append({
                "rule_id": rule["id"], "rule": rule["text"],
                "tier": rule["tier"],
                "why": "no mechanical detector matched; the lead verifies "
                       "this rule by reading the evidence",
            })
            continue
        for name, fields, params, run in armed:
            if not any(f in evidence for f in fields):
                uncheckable.append({
                    "rule_id": rule["id"], "rule": rule["text"],
                    "tier": rule["tier"], "detector": name,
                    "why": f"evidence field(s) {', '.join(fields)} absent — "
                           f"this detector checked nothing",
                })
                continue
            try:
                detail = run(params, evidence)
            except Uncheckable as e:
                uncheckable.append({
                    "rule_id": rule["id"], "rule": rule["text"],
                    "tier": rule["tier"], "detector": name, "why": str(e),
                })
                continue
            if detail:
                findings.append({
                    "rule_id": rule["id"], "rule_key": rule["key"],
                    "rule": rule["text"], "tier": rule["tier"],
                    "detector": name,
                    "outcome": "violation" if rule["tier"] == 0 else "issue",
                    "detail": detail,
                })
    by_tier = {t: sum(1 for r in rules if r["tier"] == t) for t in TIER_ORDER}
    return {
        "kind": "compliance-report",
        "version": 1,
        "at": now_iso(),
        "project": str(project_root),
        "agents_md": str(project_root / "AGENTS.md"),
        "task": evidence.get("task"),
        "attempt": evidence.get("attempt"),
        "member": evidence.get("member"),
        "tier_config": tier_config,
        "summary": {
            "rules": len(rules),
            "tier0": by_tier[0], "tier1": by_tier[1], "tier2": by_tier[2],
            "violations": sum(1 for f in findings if f["tier"] == 0),
            "issues": sum(1 for f in findings if f["tier"] == 1),
            "advice": len(advice),
            "uncheckable": len(uncheckable),
        },
        "tiers": {s: {"name": t["name"], "enforcement": t["enforcement"]}
                  for s, t in defaults["tiers"].items()},
        "rules": rules,
        "findings": findings,
        "advice": advice,
        "uncheckable": uncheckable,
    }


def gate_dispatch(project_root: Path, evidence: dict,
                  defaults_path: Path | None = None) -> dict:
    """The dispatch gate: refuse iff any tier-0 rule is violated, or if the
    evidence carries no checkable field at all (nothing was actually checked).
    Tier-0 rules that went unverified are named as non-blocking notes in the
    reasons. JSON in, JSON out — the socket adapter and the lead loop call
    this."""
    report = check_task(project_root, evidence, defaults_path)
    blocks = [f for f in report["findings"] if f["tier"] == 0]
    reasons = [
        f"tier-0 violation of '{f['rule']}' ({f['rule_id']}): {f['detail']}"
        for f in blocks
    ]
    decision = "allow"
    if blocks:
        decision = "refuse"
    elif not any(f in evidence for f in CHECKABLE_FIELDS):
        decision = "refuse"
        reasons.append(
            "no checkable evidence present (files_changed, commands, "
            "planned_commands, commit_messages, claims all absent) — nothing "
            "was actually checked"
        )
    for u in report["uncheckable"]:
        if u["tier"] == 0:
            reasons.append(
                f"note: tier-0 rule went unverified: '{u['rule']}' "
                f"({u['rule_id']}) — {u['why']}"
            )
    return {
        "kind": "dispatch-gate",
        "decision": decision,
        "reasons": reasons,
        "report": report,
    }


def safe_id(value, label: str) -> str:
    """Task/attempt ids become path components; reject traversal."""
    text = str(value)
    if not SAFE_ID_RE.match(text) or ".." in text:
        raise UsageError(
            f"unsafe {label} id {text!r}; use plain ids like '7' or '7.2' "
            f"([A-Za-z0-9._-], no '..')"
        )
    return text


def record_report(record_root: Path, report: dict) -> dict:
    """Land the report in the team record: a per-task sidecar plus append-only
    JSONL history entries (one run entry, one per finding)."""
    record_root = Path(record_root)
    task = safe_id(report.get("task") or "unassigned", "task")
    attempt = report.get("attempt")
    slug = safe_id(attempt, "attempt") if attempt else datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    task_dir = record_root / "tasks" / task
    task_dir.mkdir(parents=True, exist_ok=True)
    sidecar = task_dir / f"compliance-{slug}.json"
    sidecar.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    log_path = record_root / LOG_NAME
    entries = [{
        "kind": "run", "at": report["at"], "task": task,
        "attempt": report.get("attempt"), "member": report.get("member"),
        "violations": report["summary"]["violations"],
        "issues": report["summary"]["issues"],
        "sidecar": str(sidecar.relative_to(record_root)),
    }]
    for f in report["findings"]:
        entries.append({
            "kind": "finding", "at": report["at"], "task": task,
            "attempt": report.get("attempt"), "member": report.get("member"),
            "rule_key": f["rule_key"], "rule": f["rule"], "tier": f["tier"],
            "outcome": f["outcome"], "detector": f["detector"],
            "detail": f["detail"],
        })
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in entries))
    return {"sidecar": str(sidecar), "log": str(log_path),
            "findings_logged": len(report["findings"])}


def query_history(record_root: Path, rule_pat: str | None = None,
                  member: str | None = None) -> dict:
    """Group finding entries by rule. A rule is REPEATED when findings against
    it span two or more distinct tasks — one task tripping two detectors is a
    single bad run, not a pattern. Undecodable or malformed log lines are
    skipped and counted, never fatal."""
    log_path = Path(record_root) / LOG_NAME
    if not log_path.is_file():
        return {"kind": "compliance-history", "runs": 0, "skipped": 0,
                "rules": []}
    runs = 0
    skipped = 0
    groups: dict[str, dict] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(entry, dict) or entry.get("kind") not in ("run", "finding"):
            skipped += 1
            continue
        if entry["kind"] == "run":
            runs += 1
            continue
        if any(k not in entry for k in ("rule_key", "rule", "tier", "outcome",
                                        "task", "at", "detail")):
            skipped += 1
            continue
        if rule_pat and not re.search(rule_pat, entry["rule"], re.I):
            continue
        if member and entry.get("member") != member:
            continue
        g = groups.setdefault(entry["rule_key"], {
            "rule_key": entry["rule_key"], "rule": entry["rule"],
            "tier": entry["tier"], "outcome": entry["outcome"],
            "count": 0, "occurrences": [],
        })
        g["count"] += 1
        g["occurrences"].append({
            "at": entry["at"], "task": entry["task"],
            "member": entry.get("member"), "detail": entry["detail"],
        })
    rules = sorted(groups.values(), key=lambda g: (-g["count"], g["rule"]))
    for g in rules:
        g["tasks"] = sorted({o["task"] for o in g["occurrences"]})
        g["repeated"] = len(g["tasks"]) >= 2
    return {"kind": "compliance-history", "runs": runs, "skipped": skipped,
            "rules": rules}


# ---------------------------------------------------------------------------
# evidence loading
# ---------------------------------------------------------------------------

def load_evidence(arg: str) -> dict:
    try:
        raw = (
            sys.stdin.read()
            if arg == "-"
            else Path(arg).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError) as e:
        raise UsageError(f"cannot read evidence '{arg}': {e}")
    try:
        evidence = json.loads(raw)
    except json.JSONDecodeError as e:
        raise UsageError(f"evidence '{arg}' is not valid JSON: {e}")
    if not isinstance(evidence, dict):
        raise UsageError("evidence must be a JSON object; see the docstring")
    return evidence


# ---------------------------------------------------------------------------
# human-readable printing
# ---------------------------------------------------------------------------

def warn_unmatched_overrides(tier_config: dict):
    for ov in tier_config.get("unmatched_overrides", []):
        print(
            f"warning: override '{ov['match']}' matched no rule — "
            f"check for a typo", file=sys.stderr
        )


def print_rules(rules, tier_config):
    print(f"tier config: defaults {tier_config['defaults']}")
    print(f"             override {tier_config['override'] or '(none)'}")
    print()
    for r in rules:
        print(f"tier {r['tier']}  {r['id']} (line {r['line']}) [{r['tier_source']}]")
        print(f"        {r['text']}")
        for n in r["notes"]:
            print(f"        note: {n}")


def print_report(report):
    s = report["summary"]
    print(
        f"compliance report — task {report.get('task') or '?'} "
        f"member {report.get('member') or '?'} at {report['at']}"
    )
    print(
        f"  {s['rules']} rules (tier0 {s['tier0']}, tier1 {s['tier1']}, "
        f"tier2 {s['tier2']}) → {s['violations']} violations, {s['issues']} "
        f"issues, {s['advice']} advice, {s['uncheckable']} uncheckable"
    )
    for f in report["findings"]:
        print(f"  {f['outcome']:9s} tier {f['tier']} [{f['detector']}] {f['rule']}")
        print(f"            {f['detail']}")
    for a in report["advice"]:
        print(f"  advice    tier 2 {a['rule']}")
    for u in report["uncheckable"]:
        print(f"  uncheckable tier {u['tier']} {u['rule']}")


def print_history(hist):
    print(f"{hist['runs']} recorded runs"
          + (f", {hist['skipped']} malformed log lines skipped"
             if hist["skipped"] else ""))
    if not hist["rules"]:
        print("no findings recorded")
        return
    for g in hist["rules"]:
        flag = "REPEATED" if g["repeated"] else "once"
        print(
            f"  [{flag}] x{g['count']} tier {g['tier']} {g['outcome']}: "
            f"{g['rule']}"
        )
        print(f"         tasks: {', '.join(g['tasks'])}")
        for o in g["occurrences"]:
            print(f"         - {o['at']} task {o['task']}: {o['detail']}")


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def selfcheck(fixtures: Path) -> int:
    checks = []

    def expect(label, cond, detail=""):
        checks.append((label, bool(cond), detail))
        print(f"{'pass' if cond else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not cond else ""))

    project = fixtures / "project"
    evidence_dir = fixtures / "evidence"

    rules, tier_config, _ = classify_rules(project)

    def rule_with(fragment):
        return next((r for r in rules if fragment.lower() in r["text"].lower()), None)

    expected = [
        ("Never commit `.env` files", 0),
        ("git push --force", 0),
        ("explicit approval", 0),
        ("Run `pnpm test`", 1),
        ("Conventional Commits", 1),
        ("frobnicate", 1),
        ("Prefer small helpers", 2),
        ("single quotes", 2),
    ]
    for fragment, tier in expected:
        r = rule_with(fragment)
        expect(f"classify '{fragment}' → tier {tier}",
               r is not None and r["tier"] == tier,
               f"got {r and r['tier']}")

    fb = rule_with("frobnicate")
    expect("unmatched rule defaults conservative with a note",
           fb is not None and fb["tier_source"] == "fallback"
           and fb["confidence"] == "low" and fb["notes"],
           f"got {fb and (fb['tier_source'], fb['confidence'])}")

    conflict_tier, _, notes = heuristic_tier(
        "Never run `git push --force`; prefer rebasing.",
        json.loads(DEFAULTS_FILE.read_text(encoding="utf-8")),
    )
    expect("conflicting matches take the conservative tier and say so",
           conflict_tier == 0 and any("conservative" in n for n in notes),
           f"got tier {conflict_tier}, notes {notes}")

    # --- C1: forbidden-command matching is verb+flag, order-independent
    arm_push = arm_forbidden_command(
        "Never run `git push --force` or `git reset --hard`."
    )

    def cmd_ev(c):
        return {"commands": [c]}

    expect("C1 'git push origin main --force' violates",
           run_forbidden_command(arm_push, cmd_ev("git push origin main --force")))
    expect("C1 'git push origin -f' violates",
           run_forbidden_command(arm_push, cmd_ev("git push origin -f")))
    expect("C1 'git push --force-with-lease' is safe (rule does not name it)",
           run_forbidden_command(arm_push, cmd_ev("git push --force-with-lease"))
           is None)
    expect("C1 'git reset HEAD~1 --hard' violates",
           run_forbidden_command(arm_push, cmd_ev("git reset HEAD~1 --hard")))
    arm_rm = arm_forbidden_command("Never run `rm -rf` outside /tmp.")
    expect("C1 rule arms rm family from 'rm -rf'",
           arm_rm is not None and "rm-rf" in arm_rm["families"])
    expect("C1 'rm -fr /x' violates",
           run_forbidden_command(arm_rm, cmd_ev("rm -fr /x")))
    expect("C1 'rm -r -f /x' violates",
           run_forbidden_command(arm_rm, cmd_ev("rm -r -f /x")))
    expect("C1 plain 'rm old.log' is safe",
           run_forbidden_command(arm_rm, cmd_ev("rm old.log")) is None)
    expect("C1 'rm -r build' without force is safe",
           run_forbidden_command(arm_rm, cmd_ev("rm -r build")) is None)
    arm_lease = arm_forbidden_command("Never use --force-with-lease.")
    expect("C1 a rule naming the lease forbids it",
           arm_lease is not None
           and run_forbidden_command(arm_lease, cmd_ev("git push --force-with-lease")))

    # --- W6: approval-required covers publish spellings beyond 'npm publish'
    arm_ap = arm_approval_required(
        "Publishing (`npm publish`) requires the user's explicit approval."
    )
    expect("W6 'npm run publish' violates",
           run_approval_required(arm_ap, cmd_ev("npm run publish")))
    expect("W6 'docker push' violates",
           run_approval_required(arm_ap, cmd_ev("docker push registry/img")))
    expect("W6 'git tag && git push --tags' violates",
           run_approval_required(arm_ap, cmd_ev("git tag v1 && git push --tags")))
    expect("W6 recorded approval satisfies the rule",
           run_approval_required(
               arm_ap, {"commands": ["npm publish"], "approved_by": "the user"}
           ) is None)

    # gate refuses a tier-0 violation, naming the reason
    ev0 = json.loads((evidence_dir / "tier0-violation.json").read_text())
    gate = gate_dispatch(project, ev0)
    expect("gate refuses tier-0 violation",
           gate["decision"] == "refuse")
    expect("refusal names the rule and the reason",
           any(".env" in reason for reason in gate["reasons"]),
           f"reasons: {gate['reasons']}")

    # tier-1 issues are reported, never blocking
    ev1 = json.loads((evidence_dir / "tier1-issue.json").read_text())
    gate1 = gate_dispatch(project, ev1)
    findings1 = gate1["report"]["findings"]
    expect("gate allows tier-1-only issues", gate1["decision"] == "allow")
    expect("tier-1 issues land in the report",
           len(findings1) == 2 and all(f["tier"] == 1 for f in findings1)
           and all(f["outcome"] == "issue" for f in findings1),
           f"got {[(f['tier'], f['detector']) for f in findings1]}")

    # clean evidence: no findings, style rules are advice only
    evc = json.loads((evidence_dir / "clean.json").read_text())
    repc = check_task(project, evc)
    expect("clean evidence yields no findings", not repc["findings"],
           f"got {repc['findings']}")
    expect("style rules appear as advice only",
           len(repc["advice"]) == 2
           and all(a["tier"] == 2 for a in repc["advice"]),
           f"got {repc['advice']}")
    expect("undetectable rule is marked uncheckable, not silently dropped",
           any("frobnicate" in u["rule"] for u in repc["uncheckable"]))

    # --- W1: absent evidence fields are uncheckable, never silent clean
    sparse = check_task(project, {"task": "7", "member": "kai"})
    expect("W1 absent fields mark armed rules uncheckable",
           not sparse["findings"]
           and any(u.get("detector") == "forbidden-path"
                   for u in sparse["uncheckable"]),
           f"got findings={sparse['findings']} uncheckable={sparse['uncheckable']}")
    gate_sparse = gate_dispatch(project, {"task": "7"})
    expect("W1 gate refuses when no field was checkable",
           gate_sparse["decision"] == "refuse"
           and any("nothing was actually checked" in r
                   for r in gate_sparse["reasons"]),
           f"got {gate_sparse['decision']}: {gate_sparse['reasons']}")
    gate_empty = gate_dispatch(project, {})
    expect("W1 gate refuses fully-empty evidence",
           gate_empty["decision"] == "refuse")
    expect("W1 present-but-empty fields count as checked",
           gate_dispatch(project, {"files_changed": [], "commands": [],
                                   "commit_messages": [], "claims": []})["decision"]
           == "allow")

    # --- W3: unreadable evidence is a usage error, not a traceback
    try:
        load_evidence("/nonexistent/evidence.json")
        expect("W3 missing evidence file raises UsageError", False)
        w3_cli = None
    except UsageError:
        expect("W3 missing evidence file raises UsageError", True)
        w3_cli = main(["gate", str(project), "--evidence",
                       "/nonexistent/evidence.json"])
    expect("W3 gate exits 2 (not a traceback exit 1) on missing evidence",
           w3_cli == 2, f"got {w3_cli}")

    with tempfile.TemporaryDirectory(prefix="compliance-selfcheck-") as tmp:
        tmp = Path(tmp)

        # override moves a rule between tiers, resolution visible
        ov_project = tmp / "project-override"
        shutil.copytree(project, ov_project)
        shutil.copy(fixtures / "overrides" / "promote-style-to-convention.json",
                    ov_project / OVERRIDE_NAME)
        ov_rules, ov_cfg, _ = classify_rules(ov_project)
        moved = next(r for r in ov_rules if "single quotes" in r["text"])
        expect("override moves the rule to tier 1",
               moved["tier"] == 1, f"got tier {moved['tier']}")
        expect("override resolution is visible",
               moved["tier_source"] == "override"
               and moved.get("heuristic_tier") == 2
               and moved["notes"],
               f"got {moved['tier_source']}")
        expect("override file is named in the tier config",
               ov_cfg["override"] is not None)
        expect("a matched override is not reported as unmatched",
               not ov_cfg["unmatched_overrides"])

        # --- W2: an override matching no rule is reported
        bad_ov = tmp / "project-bad-override"
        shutil.copytree(project, bad_ov)
        (bad_ov / OVERRIDE_NAME).write_text(json.dumps({"overrides": [
            {"match": "no such rule text anywhere", "tier": 0, "why": "typo"}
        ]}), encoding="utf-8")
        _, bad_cfg, _ = classify_rules(bad_ov)
        expect("W2 non-matching override is reported in the tier config",
               any(o["match"] == "no such rule text anywhere"
                   for o in bad_cfg["unmatched_overrides"]),
               f"got {bad_cfg['unmatched_overrides']}")

        # --- S1: multiple matching overrides take the most conservative tier
        multi = tmp / "project-multi-override"
        shutil.copytree(project, multi)
        (multi / OVERRIDE_NAME).write_text(json.dumps({"overrides": [
            {"match": "single quotes", "tier": 2},
            {"match": "quotes", "tier": 0, "why": "strict here"},
        ]}), encoding="utf-8")
        multi_rules, _, _ = classify_rules(multi)
        m = next(r for r in multi_rules if "single quotes" in r["text"])
        expect("S1 multiple overrides take the most conservative tier",
               m["tier"] == 0
               and any("most conservative" in n for n in m["notes"]),
               f"got tier {m['tier']}, notes {m['notes']}")

        # --- W1: an allow decision still names unverified tier-0 rules
        vault = tmp / "project-vault"
        vault.mkdir()
        (vault / "AGENTS.md").write_text(
            "# Rules\n\n- Never touch the secrets vault.\n", encoding="utf-8"
        )
        gate_vault = gate_dispatch(vault, {"task": "1", "commands": ["pnpm test"]})
        expect("W1 allow decision carries unverified tier-0 notes",
               gate_vault["decision"] == "allow"
               and any("unverified" in r for r in gate_vault["reasons"]),
               f"got {gate_vault['decision']}: {gate_vault['reasons']}")

        # --- W4: task/attempt ids cannot traverse out of the record
        evil = check_task(project, {
            "task": "../evil", "member": "kai", "files_changed": [],
            "commands": ["git commit -m 'feat: x'"], "commit_messages": ["feat: x"],
            "claims": [{"claim": "tests", "status": "passed"}],
        })
        try:
            record_report(tmp / "record-evil", evil)
            expect("W4 task id traversal is rejected", False)
        except UsageError:
            expect("W4 task id traversal is rejected", True)
        evil_attempt = check_task(project, {
            "task": "7", "attempt": "7/../../x", "member": "kai",
            "files_changed": [], "commands": [], "commit_messages": [],
            "claims": [],
        })
        try:
            record_report(tmp / "record-evil2", evil_attempt)
            expect("W4 attempt id traversal is rejected", False)
        except UsageError:
            expect("W4 attempt id traversal is rejected", True)
        expect("W4 no traversal artifacts were written",
               not (tmp / "evil").exists())

        # history: the same violation recorded twice shows as repeated
        record = tmp / "record"
        for name in ("repeat-a.json", "repeat-b.json"):
            ev = json.loads((evidence_dir / name).read_text())
            record_report(record, check_task(project, ev))
        hist = query_history(record)
        conv = next((g for g in hist["rules"]
                     if "Conventional Commits" in g["rule"]), None)
        expect("history records both runs", hist["runs"] == 2,
               f"got {hist['runs']}")
        expect("repeated violation shows as repeated",
               conv is not None and conv["repeated"] and conv["count"] == 2
               and conv["tasks"] == ["10", "11"],
               f"got {conv and (conv['count'], conv['tasks'])}")

        # --- S4: two findings on one rule in ONE task are not 'repeated'
        one_task = tmp / "record-one-task"
        one_task.mkdir()
        finding = {
            "kind": "finding", "at": "t", "task": "7", "member": "k",
            "rule_key": "rk", "rule": "Rule X", "tier": 1,
            "outcome": "issue", "detector": "d", "detail": "x",
        }
        (one_task / LOG_NAME).write_text(
            json.dumps({"kind": "run", "at": "t", "task": "7"}) + "\n"
            + json.dumps(finding) + "\n"
            + json.dumps(dict(finding, detector="d2")) + "\n",
            encoding="utf-8",
        )
        h_one = query_history(one_task)
        expect("S4 two findings in one task are not 'repeated'",
               h_one["rules"][0]["count"] == 2
               and not h_one["rules"][0]["repeated"],
               f"got {h_one['rules'][0]}")

        # --- W5: corrupted log lines are skipped and counted, never fatal
        corrupted = tmp / "record-corrupted"
        corrupted.mkdir()
        (corrupted / LOG_NAME).write_text(
            json.dumps({"kind": "run", "at": "t", "task": "1"}) + "\n"
            + json.dumps(finding) + "\n"
            + "{not json at all\n"
            + '{"kind": "finding"}\n',
            encoding="utf-8",
        )
        h_bad = query_history(corrupted)
        expect("W5 malformed log lines are skipped and counted",
               h_bad["skipped"] == 2 and h_bad["runs"] == 1
               and len(h_bad["rules"]) == 1,
               f"got skipped={h_bad['skipped']} runs={h_bad['runs']} "
               f"rules={len(h_bad['rules'])}")

    failures = [c for c in checks if not c[1]]
    print()
    print(f"{len(checks)} checks, {len(failures)} unexpected outcomes")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def main(argv) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rules = sub.add_parser("rules", help="classify a project's AGENTS.md rules")
    p_rules.add_argument("project", type=Path)
    p_rules.add_argument("--json", action="store_true")

    p_check = sub.add_parser("check", help="check task evidence against the rules")
    p_check.add_argument("project", type=Path)
    p_check.add_argument("--evidence", required=True)
    p_check.add_argument("--json", action="store_true")

    p_gate = sub.add_parser(
        "gate", help="dispatch gate: JSON in, JSON out; exit 1 refuses"
    )
    p_gate.add_argument("project", type=Path)
    p_gate.add_argument("--evidence", required=True)

    p_record = sub.add_parser(
        "record", help="check and land the report in the team record"
    )
    p_record.add_argument("project", type=Path)
    p_record.add_argument("--evidence", required=True)
    p_record.add_argument("--record", type=Path, required=True,
                          help="team record root (named in agreement.md)")

    p_hist = sub.add_parser("history", help="query compliance history")
    p_hist.add_argument("--record", type=Path, required=True)
    p_hist.add_argument("--rule", help="regex filter on rule text")
    p_hist.add_argument("--member", help="filter by member")
    p_hist.add_argument("--json", action="store_true")

    sub.add_parser("selfcheck", help="run the checker against its fixtures")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "rules":
            rules, tier_config, _ = classify_rules(args.project)
            warn_unmatched_overrides(tier_config)
            if args.json:
                print(json.dumps({"tier_config": tier_config, "rules": rules},
                                 indent=2))
            else:
                print_rules(rules, tier_config)
            return 0

        if args.cmd == "check":
            report = check_task(args.project, load_evidence(args.evidence))
            warn_unmatched_overrides(report["tier_config"])
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print_report(report)
            return 1 if report["findings"] else 0

        if args.cmd == "gate":
            decision = gate_dispatch(args.project, load_evidence(args.evidence))
            warn_unmatched_overrides(decision["report"]["tier_config"])
            print(json.dumps(decision, indent=2))
            return 1 if decision["decision"] == "refuse" else 0

        if args.cmd == "record":
            report = check_task(args.project, load_evidence(args.evidence))
            warn_unmatched_overrides(report["tier_config"])
            paths = record_report(args.record, report)
            print_report(report)
            print()
            print(f"sidecar: {paths['sidecar']}")
            print(f"history: {paths['log']} "
                  f"({paths['findings_logged']} findings logged)")
            return 1 if report["summary"]["violations"] else 0

        if args.cmd == "history":
            hist = query_history(args.record, args.rule, args.member)
            if args.json:
                print(json.dumps(hist, indent=2))
            else:
                print_history(hist)
            return 0

        return selfcheck(Path(__file__).resolve().parent / "fixtures"
                         / "compliance")
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
