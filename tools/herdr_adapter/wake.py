#!/usr/bin/env python3
"""A bounded caller-side waiter for the lead pane. Observes and journals; never sends, dispatches or accepts."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from adapter import Client, Parser, Refusal, identifier, invalid, json_loads, object_fields
from event_loop import member_states, watch
import lead


MAX_SECONDS = 3600
SLICE_SECONDS = 10
MIN_SLICE = 0.05
MAX_RETURNS = 32
WATCHED = ("state_change_seq", "agent_status")


def bounded(params, name, default, ceiling):
    value = params.get(name, default)
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= ceiling:
        invalid(f"{name} must be a number above 0 and at most {ceiling}")
    return value


def baseline(state):
    """What the lead last saw: its final watch picture, else the signatures its last look read."""
    picture = state.get("last_watch", {}).get("picture")
    if picture:
        return {pane: {key: member[key] for key in WATCHED} for pane, member in member_states(picture).items()}
    return {member["pane_id"]: dict(zip(WATCHED, state["read_states"][name]))
            for name, member in state["members"].items() if name in state["read_states"]}


def pane_changes(names, before, picture):
    """Panes whose signature moved. `names` maps pane to member for a lead record; None reports every pane."""
    live = member_states(picture)
    return [{"pane_id": pane, "member": pane if names is None else names[pane], "before": before[pane],
             "after": {key: live[pane][key] for key in WATCHED}}
            for pane in before if (names is None or pane in names) and pane in live
            and any(live[pane][key] != before[pane][key] for key in WATCHED)]


def journal(root, reason, changes):
    """Record last_wake under the lead's lock; a lead turn in progress means the wake is only reported."""
    try:
        with lead.record_lock(root):
            state = lead.load_state(lead.local_path(root, "lead-state.json").read_text())
            state["last_wake"] = {"at": lead.stamp(), "reason": reason, "changes": changes}
            lead.write_json(root, "lead-state.json", state)
    except Refusal as error:
        if error.code != "lead_in_progress":
            raise
        return False
    return True


def parse_returns(value):
    """Assigned return files for the manual path: at most 32 {path, attempt}, each an absolute existing file."""
    if type(value) is not list or not 1 <= len(value) <= MAX_RETURNS:
        invalid(f"returns must be a list of 1 to {MAX_RETURNS} items")
    listed = []
    for item in value:
        object_fields(item, {"path", "attempt"}, {"path", "attempt"})
        path, attempt = item["path"], item["attempt"]
        if type(path) is not str or not os.path.isabs(path) or not os.path.isfile(path):
            invalid("returns path must be an absolute path to an existing file")
        if type(attempt) is not str or not re.fullmatch(r"[0-9]+(\.[0-9]+)?", attempt):
            invalid("returns attempt must be a task id or <task>.<n>")
        listed.append({"path": path, "attempt": attempt})
    return listed


def closed_returns(listed):
    """Listed files whose own text now carries a '## Return' section that closes the listed attempt.

    There is no lead record here, so the lead-owned brief prefix is unknown: the file's own frontmatter
    (attempt, else task) must name the listed attempt, and only a real return section counts."""
    found = []
    for item in listed:
        data = lead.BoundedReader(max_reads=1).bytes(item["path"])
        text = data.decode("utf-8")
        fields, _, _ = lead.contracts.parse_frontmatter(text)
        if fields is None or (fields.get("attempt") or fields.get("task")) != item["attempt"]:
            raise Refusal("receipt_mismatch", f"{item['path']}: its frontmatter does not name attempt {item['attempt']}")
        if lead.closing_sections(text, item["attempt"]):
            found.append({**item, "sha256": hashlib.sha256(data).hexdigest()})
    return found


def wait(options, max_seconds, slice_seconds, workspace_id, find_returns, names, before):
    """Check the return files, then watch one slice at a time. Without a workspace there is no socket at all."""
    client = None
    started = time.monotonic()
    while True:
        changes = {"panes": [], "returns": find_returns()}
        remaining = started + max_seconds - time.monotonic()
        if changes["returns"] or remaining < MIN_SLICE:
            break
        duration = min(slice_seconds, remaining)
        if workspace_id is None:
            time.sleep(duration)
            continue
        client = client or Client(options)
        picture = watch(client, {"workspace_id": workspace_id, "duration_seconds": duration})["picture"]
        if not before:
            before = {pane: {key: m[key] for key in WATCHED} for pane, m in member_states(picture).items()}
        changes["panes"] = pane_changes(names, before, picture)
        if changes["panes"]:
            break
    reason = "new_return" if changes["returns"] else "pane_status" if changes["panes"] else "deadline"
    return reason, changes, round(time.monotonic() - started, 2)


def wake(params, options):
    object_fields(params, {"record_root", "returns", "workspace_id", "max_seconds", "slice_seconds"}, {"max_seconds"})
    if ("record_root" in params) == ("returns" in params):
        invalid("give exactly one of record_root (a lead record) or returns (assigned return files)")
    max_seconds = bounded(params, "max_seconds", None, MAX_SECONDS)
    slice_seconds = bounded(params, "slice_seconds", SLICE_SECONDS, 60)
    if "returns" in params:
        listed = parse_returns(params["returns"])
        workspace = identifier(params["workspace_id"], "workspace_id") if "workspace_id" in params else None
        # No lead record: nothing is read from or written to one, and nothing is journaled.
        reason, changes, waited = wait(options, max_seconds, slice_seconds, workspace,
                                       lambda: closed_returns(listed), None, {})
        return {"reason": reason, "changes": changes, "journaled": False, "waited_seconds": waited}
    if "workspace_id" in params:
        invalid("workspace_id belongs to returns mode; a lead record names its own workspace")
    root = Path(identifier(params["record_root"], "record_root")).resolve()
    state = lead.load_state(lead.local_path(root, "lead-state.json").read_text())
    names = {member["pane_id"]: name for name, member in state["members"].items()}
    reason, changes, waited = wait(options, max_seconds, slice_seconds, state["workspace_id"],
                                   lambda: lead.new_returns(root, state), names, baseline(state))
    return {"reason": reason, "changes": changes, "journaled": journal(root, reason, changes),
            "waited_seconds": waited}


def main():
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--socket", default=os.environ.get(
            "HERDR_SOCKET_PATH", os.path.expanduser("~/.config/herdr/herdr.sock")))
        options = parser.parse_args()
        payload = json_loads(sys.stdin.read())
        object_fields(payload, {"command", "params"}, {"command", "params"})
        if payload["command"] != "wake":
            raise Refusal("unknown_command", "expected wake")
        print(json.dumps({"ok": True, "result": wake(payload["params"], options)}, allow_nan=False))
        return 0
    except (Refusal, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": {
            "code": getattr(error, "code", "invalid_input"), "message": str(error),
        }}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
