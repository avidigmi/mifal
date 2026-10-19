#!/usr/bin/env python3
"""Opt-in M1 demos: scratch-only fake member and returning lead; zero LLM calls."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from live_lead_loop import MissionProxy, Schema


HERE = Path(__file__).resolve().parent


def driver(directory, proxy):
    assert os.environ["HERDR_ENV"] == "1"
    (directory / "lead-ready.json").write_text(json.dumps({
        "pid": os.getpid(), "pane_id": os.environ["HERDR_PANE_ID"],
    }))
    end = time.monotonic() + 180
    for index in range(1, 21):
        job = directory / f"job-{index}.json"
        while not job.exists():
            assert time.monotonic() < end, "bounded lead fixture expired"
            time.sleep(0.05)
        payload = json.loads(job.read_text())
        assert payload["command"] in {"resume", "lead"}
        script = "resume.py" if payload["command"] == "resume" else "lead.py"
        argv = [sys.executable, str(HERE.parent / script), "--socket", proxy,
                "--state-dir", str(directory / "adapter-state"),
                "--idle-seconds", "0.25", "--read-gap-seconds", "0.2"]
        with (directory / f"turn-{index}.ndjson").open("w") as log:
            result = subprocess.run(argv, input=json.dumps(payload), text=True,
                                    stdout=subprocess.PIPE, stderr=log, timeout=20)
        response = json.loads(result.stdout)
        output = {"argv": argv, "stdin": payload, "exit": result.returncode, "response": response}
        temporary = directory / f"result-{index}.tmp"
        temporary.write_text(json.dumps(output, indent=2) + "\n")
        temporary.replace(directory / f"result-{index}.json")
        print(f"M1 {index}: {response}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--driver", action="store_true")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--socket")
    args = parser.parse_args()
    if args.driver:
        return driver(args.directory, args.socket)
    directory = Path(tempfile.mkdtemp(prefix="adapter-resume-live-", dir="/tmp"))
    root = directory / ".team"
    upstream = str(Path.home() / ".config/herdr/herdr.sock")
    label = "ADAPTER-RESUME-TEST-" + uuid.uuid4().hex[:8]
    token = uuid.uuid4().hex[:8]
    workspace, panes, proxy = None, [], None
    commands, focus = [], []
    schema = Schema()

    def save(name, value):
        (directory / name).write_text(json.dumps(value, indent=2) + "\n")

    def raw(method, params):
        schema.params(method, params)
        request_id = str(uuid.uuid4())
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect(upstream)
            connection.sendall((json.dumps({
                "id": request_id, "method": method, "params": params,
            }) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        assert response["id"] == request_id and "error" not in response, response
        return response["result"]

    def fixture(method, params):
        if method == "workspace.create":
            assert params == {"label": label, "cwd": str(directory), "focus": False}
        elif method == "pane.split":
            assert params["workspace_id"] == workspace and params["target_pane_id"] in panes
            assert params["focus"] is False
        elif method in {"pane.send_input", "pane.read"}:
            assert params["pane_id"] in panes
        elif method == "workspace.close":
            assert params["workspace_id"] == workspace and workspace not in baseline_ids
            owned = [w for w in raw("workspace.list", {})["workspaces"] if w["workspace_id"] == workspace]
            assert len(owned) == 1 and owned[0]["label"] == label
        else:
            raise AssertionError("unapproved fixture operation")
        return raw(method, params)

    def identity(snapshot):
        return {"focus": {key: snapshot.get(key) for key in
                          ("focused_workspace_id", "focused_tab_id", "focused_pane_id")},
                "panes": [{key: p.get(key) for key in
                           ("workspace_id", "tab_id", "pane_id", "terminal_id", "cwd")}
                          for p in snapshot["panes"]]}

    def check_focus(operation):
        current = identity(raw("session.snapshot", {})["snapshot"])["focus"]
        focus.append({"operation": operation, **current})
        assert current == before_identity["focus"]

    def wait_file(path, timeout=25):
        end = time.monotonic() + timeout
        while not path.exists():
            assert time.monotonic() < end, "missing " + str(path)
            time.sleep(0.05)
        return path.read_text()

    def job(command, params, expected=0):
        index = len(commands) + 1
        temporary = directory / f"job-{index}.tmp"
        temporary.write_text(json.dumps({"command": command, "params": params}))
        temporary.replace(directory / f"job-{index}.json")
        result = json.loads(wait_file(directory / f"result-{index}.json"))
        commands.append(result)
        save("commands.json", commands)
        assert result["exit"] == expected, result
        check_focus("job-" + str(index))
        return result["response"]

    def resume(rid, record=root, **params):
        return job("resume", {"record_root": str(record), "resume_id": rid, **params})["result"]

    before = raw("workspace.list", {})
    before_identity = identity(raw("session.snapshot", {})["snapshot"])
    baseline_ids = {w["workspace_id"] for w in before["workspaces"]}
    save("workspace-before.json", before)
    save("identities-before.json", before_identity)
    print("Evidence directory:", directory, flush=True)
    try:
        created = fixture("workspace.create", {"label": label, "cwd": str(directory), "focus": False})
        workspace = created["workspace"]["workspace_id"]
        assert workspace not in baseline_ids
        panes.append(created["root_pane"]["pane_id"])
        save("scratch.json", {"workspace_id": workspace, "panes": panes})
        split = fixture("pane.split", {"workspace_id": workspace, "target_pane_id": panes[0],
                                      "direction": "right", "cwd": str(directory),
                                      "ratio": 0.5, "focus": False})
        panes.append(split["pane"]["pane_id"])
        save("scratch.json", {"workspace_id": workspace, "panes": panes})
        check_focus("create-and-split")
        proxy = MissionProxy(directory / "proxy.sock", upstream, panes)
        (directory / "AGENTS.md").write_text("- Never run `git push --force`.\n")
        (directory / "source.txt").write_text(" Ada\nBea\nada\n")
        init = {"command": "init", "params": {
            "record_root": str(root), "project_root": str(directory), "workspace_id": workspace,
            "lead_pane_id": panes[0], "mission": "Resume one checked contact count without duplication.",
            "members": [{"name": "counter", "pane_id": panes[1], "input_box": {
                "agent": "adapter-fake", "start": f"[INPUT {token}]", "end": f"[/INPUT {token}]",
            }}],
            "tasks": [{"id": "8", "owner": "counter", "why": "A checked contact count.",
                       "outcome": "Write tasks/8/result.txt from source.txt.", "value": 10,
                       "claims": {"artifact": {"path": "tasks/8/result.txt",
                                               "sha256": hashlib.sha256(b"2\n").hexdigest()}},
                       "stop_rule": "Stop within 90 seconds; write an honest receipt.",
                       "planned_commands": [], "files_changed": ["tasks/8/result.txt"]}],
        }}
        result = subprocess.run([sys.executable, str(HERE.parent / "lead.py")],
                                input=json.dumps(init), capture_output=True, text=True, timeout=10)
        save("init.json", {"stdin": init, "stdout": result.stdout, "exit": result.returncode})
        assert result.returncode == 0, result.stdout + result.stderr
        time.sleep(2.5)
        member_command = [sys.executable, str(HERE / "fake_mission.py"), "--workspace", workspace,
                          "--directory", str(directory / "counter"), "--record", str(root),
                          "--name", "counter", "--token", token]
        fixture("pane.send_input", {"pane_id": panes[1], "text": shlex.join(member_command), "keys": ["enter"]})
        wait_file(directory / "counter/ready.json")
        lead_command = [sys.executable, str(Path(__file__).resolve()), "--driver",
                        "--directory", str(directory), "--socket", proxy.path]
        fixture("pane.send_input", {"pane_id": panes[0], "text": shlex.join(lead_command), "keys": ["enter"]})
        wait_file(directory / "lead-ready.json")
        check_focus("start-fixtures")

        never = resume("never-sent")
        assert never["plan"][0]["classification"] == "never-sent", never
        assert never["plan"][0]["pane_inspected"] and never["outcome"]["action"] == "dispatch", never
        replay = resume("never-sent")
        assert replay["replayed"] and replay["first_action"] == never["first_action"]
        # No lead turn handles the member's DONE. Its durable return survives the break.
        end = time.monotonic() + 10
        while "Closes: 8" not in (root / "tasks/8.md").read_text():
            assert time.monotonic() < end, "member did not finish"
            time.sleep(0.05)
        complete = resume("completed-unreported")
        assert complete["plan"][0]["classification"] == "completed-unreported", complete
        assert complete["outcome"]["action"] == "accept" and complete["handoff"] == "lead", complete
        returned = job("lead", {"record_root": str(root), "wait_seconds": 0.15})["result"]
        if returned["action"] == "reject-doorbell":  # the now-obsolete visible DONE
            returned = job("lead", {"record_root": str(root), "wait_seconds": 0.15})["result"]
        assert returned["action"] == "complete", returned
        assert sum(r["method"] == "pane.send_input" for r in proxy.wire) == 1

        # Synthetic old-server pin + real live confirmation. Never restart the real server.
        reused_root = directory / "reused-record"
        shutil.copytree(root, reused_root)
        state = json.loads((reused_root / "lead-state.json").read_text())
        state["members"]["counter"]["identity"]["terminal_id"] = "old-server-terminal-fixture"
        (reused_root / "lead-state.json").write_text(json.dumps(state))
        wire_before = len(proxy.wire)
        reused = resume("pane-reused", reused_root)
        assert reused["plan"][0]["code"] == "identity_mismatch" and reused["handoff"] == "hold", reused
        assert reused["plan"][0]["refused_actions"] == ["prompt", "close"]
        assert [r["method"] for r in proxy.wire[wire_before:]] == ["session.snapshot"]
        denied = job("lead", {"record_root": str(reused_root)}, expected=1)
        assert denied["error"]["code"] == "resume_required"

        budget_root = directory / "budget-record"
        shutil.copytree(root, budget_root)
        state = json.loads((budget_root / "lead-state.json").read_text())
        state["tasks"]["9"] = {**state["tasks"]["8"], "id": "9", "active": "9", "status": "queued",
                               "return": "tasks/9.md"}
        (budget_root / "lead-state.json").write_text(json.dumps(state))
        capped = resume("budget-cap", budget_root, max_tasks=1)
        assert capped["budget"]["used"]["tasks"] == 1 and capped["uninspected_tasks"] == 1, capped
        assert capped["inspection_error"]["code"] == "resume_budget" and capped["handoff"] == "hold"
        assert sum(r["method"] == "pane.send_input" for r in proxy.wire) == 1
        assert not proxy.errors and not proxy.stream_requests
        save("screens.json", [fixture("pane.read", {"pane_id": pane, "source": "visible",
                                                   "format": "text"}) for pane in panes])
        save("summary.json", {"outcome": "pass", "model_calls": 0, "dispatches": 1,
                              "completed_unreported": complete["outcome"],
                              "never_sent": never["outcome"], "pane_reuse": reused["plan"][0],
                              "first_action_replay_identical": True, "budget": capped["budget"],
                              "uninspected_tasks": capped["uninspected_tasks"]})
        print("PASS: never-sent dispatch, unreported completion, replay, pane reuse, bounded HOLD", flush=True)
    finally:
        if workspace:
            fixture("workspace.close", {"workspace_id": workspace})
        if proxy:
            proxy.close()
            save("wire.json", proxy.wire)
            save("proxy.json", {"errors": proxy.errors, "requests_on_stream": proxy.stream_requests})
        after = raw("workspace.list", {})
        after_identity = identity(raw("session.snapshot", {})["snapshot"])
        save("workspace-after.json", after)
        save("identities-after.json", after_identity)
        save("focus.json", focus)
        shape = lambda ws: [(w["workspace_id"], w.get("label"), w.get("pane_count")) for w in ws["workspaces"]]
        assert shape(before) == shape(after) and before_identity == after_identity
        terminated = []
        for path in list(directory.glob("*/ready.json")) + list(directory.glob("lead-ready.json")):
            pid = json.loads(path.read_text())["pid"]
            end = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    terminated.append(pid)
                    break
                assert time.monotonic() < end, ("fixture survived scratch close", pid)
                time.sleep(0.1)
        save("teardown.json", {"closed_workspace": workspace, "terminated_fixture_pids": terminated,
                               "before": shape(before), "after": shape(after),
                               "identities_and_focus_unchanged": before_identity == after_identity})
        print("PASS: scratch closed, fixture pids terminated, fleet identities and focus unchanged", flush=True)


if __name__ == "__main__":
    main()
