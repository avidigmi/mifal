#!/usr/bin/env python3
"""A read-only preflight of a worker's return, for feedback before it stops. It decides nothing and writes nothing:
acceptance stays with the lead, which runs its own checks (see LEAD.md, Preflight)."""

import json
import os
from pathlib import Path
import sys

from adapter import Refusal, identifier, invalid, json_loads, object_fields
import lead


OK, MISSING, CANNOT = "preflight-ok", "missing", "cannot-evaluate"


def verdict(kind, **fields):
    return {"verdict": kind, **fields}


def split_errors(error):
    return [part for part in str(error).split("; ") if part]


def closes_attempt(path, attempt):
    """Whether the return file already holds a Return section that closes this attempt, valid or not."""
    try:
        return bool(lead.closing_sections(lead.BoundedReader(max_reads=1).text(Path(path)), attempt))
    except (Refusal, OSError, UnicodeError):
        return False


def from_record(root, state, pane_id):
    """The lead.py path: the member on this pane, its in-flight task, and the one claim evaluator without checks.
    None means the pane is not a member of this record."""
    owners = [name for name, member in state["members"].items() if member["pane_id"] == pane_id]
    if not owners:
        return None
    tasks = sorted((t for t in state["tasks"].values() if t["owner"] == owners[0] and t["status"] in lead.IN_FLIGHT),
                   key=lambda t: int(t["id"]))
    if not tasks:
        return verdict(CANNOT, reason="nothing_in_flight", detail=f"{owners[0]} has no task in flight")
    task = tasks[0]
    where = {"source": "lead-record", "task": task["id"], "attempt": task["active"], "return": task["return"],
             "closes": closes_attempt(lead.local_path(root, task["return"]), task["active"])}
    try:
        errors, evidence = lead.review_receipt(root, task, {"artifact": task["return"]}, run_checks=False)
    except FileNotFoundError:
        return verdict(MISSING, **where, errors=["the assigned return file does not exist"])
    except Refusal as error:
        if error.code in {"receipt_mismatch", "contract_invalid"}:
            return verdict(MISSING, **where, errors=split_errors(error))
        return verdict(CANNOT, reason=error.code, detail=str(error))
    except (OSError, UnicodeError) as error:
        return verdict(CANNOT, reason="return_unreadable", detail=error.__class__.__name__)
    if errors:
        return verdict(MISSING, **where, errors=errors)
    return verdict(OK, **where, claims=[row["claim"] for row in evidence])


def from_triple(triple, source):
    """The manual path: no claims exist outside a lead.py plan, so the return is checked against itself and the
    receipt validator. The lead-owned brief prefix is unknown, so the file's own frontmatter must name the attempt."""
    path, attempt = Path(triple["return"]), triple["attempt"]
    where = {"source": source, "attempt": attempt, "return": str(path), "closes": False}
    if "dispatched_at" in triple:  # echoed so a stale pin is visible to the reader; no freshness rule is applied to it
        where["dispatched_at"] = triple["dispatched_at"]
    try:
        text = lead.BoundedReader(max_reads=1).text(path)
    except FileNotFoundError:
        return verdict(MISSING, **where, errors=["the assigned return file does not exist"])
    except (OSError, UnicodeError) as error:
        return verdict(CANNOT, reason="return_unreadable", detail=error.__class__.__name__)
    errors = []
    fields, _, _ = lead.contracts.parse_frontmatter(text)
    named = (fields or {}).get("attempt") or (fields or {}).get("task")
    if named != attempt:
        errors.append(f"frontmatter names attempt {named or 'none'}, not {attempt}")
    closing = lead.closing_sections(text, attempt)
    where["closes"] = bool(closing)
    if not closing:
        errors.append(f"no Return section closes attempt {attempt}")
    elif len(closing) > 1:
        errors.append(f"{len(closing)} Return sections close attempt {attempt}; exactly one must")
    try:
        lead.checked("receipt", text, path, matt=triple["matt"])
    except Refusal as error:
        errors += split_errors(error)
    return verdict(MISSING, **where, errors=errors) if errors else verdict(OK, **where)


def from_pane(root, pane_id):
    if not root.is_dir():
        return verdict(CANNOT, reason="record_unavailable", detail="the record root is not a directory")
    state_file = root / "lead-state.json"
    if state_file.exists():
        try:
            state = lead.load_state(lead.BoundedReader(max_reads=1).text(state_file))
        except (Refusal, OSError, UnicodeError, ValueError, KeyError) as error:
            return verdict(CANNOT, reason="record_corrupt", detail=str(error)[:200])
        found = from_record(root, state, pane_id)
        if found is not None:
            return found
    try:
        pin = lead.load_pin(root, pane_id)
    except Refusal as error:
        return verdict(CANNOT, reason="pin_invalid", detail=str(error))
    if pin is None:
        return verdict(CANNOT, reason="unassigned_pane", detail=f"no member or assignment pin for {pane_id}")
    return from_triple(pin, "assignment-pin")


def preflight(params):
    object_fields(params, {"record_root", "pane_id", "return", "attempt", "matt"}, set())
    triple = {"return", "attempt", "matt"} & set(params)
    if "pane_id" in params:
        if triple or "record_root" not in params:
            invalid("pane_id needs record_root and excludes return, attempt and matt")
        root = Path(identifier(params["record_root"], "record_root")).resolve()
        lead.pin_path(root, params["pane_id"])  # refuses anything that is not a Herdr pane id
        return from_pane(root, params["pane_id"])
    if triple != {"return", "attempt", "matt"} or "record_root" in params:
        invalid("give pane_id with record_root, or an explicit return, attempt and matt")
    if type(params["return"]) is not str or not os.path.isabs(params["return"]):
        invalid("return must be an absolute path")
    if type(params["attempt"]) is not str or not lead.ATTEMPT_RE.fullmatch(params["attempt"]):
        invalid("attempt must be a task id or <task>.<n>")
    if type(params["matt"]) is not bool:
        invalid("matt must be true or false")
    return from_triple(params, "explicit")


def main():
    try:
        payload = json_loads(sys.stdin.read())
        object_fields(payload, {"command", "params"}, {"command", "params"})
        if payload["command"] != "preflight":
            raise Refusal("unknown_command", "expected preflight")
        try:
            result = preflight(payload["params"])
        except Refusal:
            raise
        except Exception as error:  # a preflight that cannot judge says so; it never raises past the caller
            result = verdict(CANNOT, reason="evaluation_failed", detail=f"{error.__class__.__name__}: {error}"[:200])
        print(json.dumps({"ok": True, "result": result}, allow_nan=False))
        return 0
    except (Refusal, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {
            "code": getattr(error, "code", "invalid_input"), "message": str(error),
        }}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
