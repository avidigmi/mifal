#!/usr/bin/env python3
"""Opt-in seam-2 test. Creates/owns/tears down one ADAPTER-TEST workspace."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


HERE = Path(__file__).resolve().parent
CLI = HERE.parent / "adapter.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--safe-defaults", action="store_true",
                        help="also dispatch with the real 60s/10s default guard")
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="adapter-live-", dir="/tmp"))
    real_socket = os.path.expanduser("~/.config/herdr/herdr.sock")
    proxy_path = str(directory / "proxy.sock")
    workspace_id = None
    panes = []
    wire = []
    commands = []
    focused = []
    token = uuid.uuid4().hex[:8]
    baseline_focus = None

    def save(name, data):
        (directory / name).write_text(json.dumps(data, indent=2) + "\n")

    def raw(method, params):
        packet = {"id": str(uuid.uuid4()), "method": method, "params": params}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(real_socket)
            connection.sendall((json.dumps(packet) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        assert "error" not in response, response
        return response["result"]

    def validate(method, params):
        result = subprocess.run([sys.executable, str(CLI)], input=json.dumps({
            "command": "validate", "params": {"method": method, "params": params},
        }), capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr

    def fixture(method, params):
        validate(method, params)
        if method in ("pane.send_input", "pane.send_text"):
            assert params["pane_id"] in panes
        elif method == "pane.split":
            assert params["workspace_id"] == workspace_id
            assert params["target_pane_id"] in panes
            assert params["focus"] is False
        elif method == "workspace.close":
            assert params["workspace_id"] == workspace_id
            assert workspace_id not in baseline_ids
            owned = [w for w in raw("workspace.list", {})["workspaces"] if w["workspace_id"] == workspace_id]
            assert len(owned) == 1 and owned[0]["label"] == "ADAPTER-TEST-" + token
        elif method == "workspace.create":
            assert params["focus"] is False and Path(params["cwd"]) == directory
        elif method not in ("session.snapshot", "workspace.list", "agent.list", "pane.read"):
            raise AssertionError("fixture method not allowed: " + method)
        if method == "pane.read":
            assert params["pane_id"] in panes
        return raw(method, params)

    def focus_check(operation):
        snapshot = raw("session.snapshot", {})["snapshot"]
        now = snapshot.get("focused_pane_id")
        focused.append({"operation": operation, "focused_pane_id": now})
        assert now == baseline_focus, focused
        return snapshot

    listener = socket.socket(socket.AF_UNIX)
    listener.bind(proxy_path)
    listener.listen()
    listener.settimeout(0.1)
    stop = threading.Event()
    proxy_errors = []

    def proxy():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            try:
                with connection, connection.makefile("rb") as stream:
                    request = json.loads(stream.readline())
                    wire.append(request)
                    method, params = request["method"], request["params"]
                    assert method in {"session.snapshot", "pane.read", "pane.send_input"}
                    if method != "session.snapshot":
                        assert params["pane_id"] in panes
                    result = raw(method, params)
                    response = {"id": request["id"], "result": result}
                    connection.sendall((json.dumps(response) + "\n").encode())
            except Exception as error:
                proxy_errors.append(repr(error))

    thread = threading.Thread(target=proxy, daemon=True)
    thread.start()

    def cli(name, payload, expected=0, safe_defaults=False):
        flags = [] if safe_defaults else ["--idle-seconds", "0.3", "--read-gap-seconds", "0.2"]
        argv = [sys.executable, str(CLI), "--socket", proxy_path,
                "--state-dir", str(directory / "state"), "--shell-ready-ms", "500", *flags]
        before = len(wire)
        result = subprocess.run(argv, input=json.dumps(payload), text=True,
                                capture_output=True, timeout=100)
        response = json.loads(result.stdout)
        commands.append({"name": name, "argv": argv, "stdin": payload, "exit": result.returncode,
                         "response": response, "wire_requests": len(wire) - before})
        save("commands.json", commands)
        assert not result.stderr, result.stderr
        assert result.returncode == expected, (name, response)
        focus_check(name)
        return response

    def wait_file(path):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.exists():
                return json.loads(path.read_text())
            time.sleep(0.1)
        raise AssertionError("missing artifact: " + str(path))

    before = raw("workspace.list", {})
    save("workspace-before.json", before)
    baseline_ids = {w["workspace_id"] for w in before["workspaces"]}
    baseline_snapshot = raw("session.snapshot", {})["snapshot"]
    baseline_focus = baseline_snapshot["focused_pane_id"]
    save("identities-before.json", {
        "focus": baseline_focus,
        "panes": [{key: p.get(key) for key in ("pane_id", "terminal_id", "workspace_id", "tab_id", "cwd")}
                  for p in baseline_snapshot["panes"]],
    })
    print("Evidence directory:", directory, flush=True)
    try:
        result = fixture("workspace.create", {
            "label": "ADAPTER-TEST-" + token, "cwd": str(directory), "focus": False,
        })
        workspace_id = result["workspace"]["workspace_id"]
        assert workspace_id not in baseline_ids
        panes.append(result["root_pane"]["pane_id"])
        save("scratch.json", {"workspace_id": workspace_id, "panes": panes})
        focus_check("workspace.create")
        for direction in ("right", "down", "right"):
            result = fixture("pane.split", {
                "workspace_id": workspace_id, "target_pane_id": panes[0],
                "direction": direction, "cwd": str(directory), "ratio": 0.5, "focus": False,
            })
            panes.append(result["pane"]["pane_id"])
            save("scratch.json", {"workspace_id": workspace_id, "panes": panes})
            focus_check("pane.split")
        time.sleep(2.5)
        for index, pane_id in enumerate(panes):
            member_dir = directory / f"member-{index}"
            command = [sys.executable, str(HERE / "fake_agent.py"), "--workspace", workspace_id,
                       "--directory", str(member_dir), "--name", f"adapter-{index}", "--token", token]
            if index == 2:
                command.append("--cycle")
            fixture("pane.send_input", {"pane_id": pane_id, "text": shlex.join(command), "keys": ["enter"]})
            wait_file(member_dir / "ready.json")
            focus_check("fake-agent-start")

        payload = {"command": "observe", "params": {"workspace_id": workspace_id}}
        response = cli("observe", payload)
        assert len(response["result"]["members"]) == len(response["result"]["panes"]) == 4
        assert {member["pane_id"] for member in response["result"]["members"]} == set(panes)
        agents = fixture("agent.list", {})
        save("agent-list.json", [a for a in agents["agents"] if a["workspace_id"] == workspace_id])
        brief = directory / "brief.md"
        brief.write_text("Task 03: write a durable receipt.\nCloses: 03.1\n")
        dispatch_params = {
            "pane_id": panes[0], "workspace_id": workspace_id, "dispatch_id": "03.1",
            "brief_file": str(brief),
            "input_box": {"agent": "adapter-fake", "start": f"[INPUT {token}]", "end": f"[/INPUT {token}]"},
        }
        response = cli("dispatch-file", {"command": "dispatch", "params": dispatch_params})
        assert response["result"]["status"] == "sent" and response["result"]["delivery"] == "unverified"
        receipt = wait_file(directory / "member-0" / "receipt-1.json")
        assert receipt["text"] == brief.read_text()
        assert receipt["sha256"] == hashlib.sha256(brief.read_bytes()).hexdigest()
        assert receipt["pane_id"] == panes[0]
        # Runtime-computed marker, never merely the echoed brief.
        read = fixture("pane.read", {"pane_id": panes[0], "source": "visible"})["read"]["text"]
        assert re.search(r"(?<!\w)" + re.escape(receipt["marker"]) + r"(?!\w)", "".join(read.split()))
        assert all(not list((directory / f"member-{i}").glob("receipt-*.json")) for i in (1, 2, 3))

        cli("schema-invalid", {"command": "validate", "params": {"method": "pane.split", "params": {
            "pane_id": panes[0], "workspace_id": workspace_id, "direction": "right",
        }}}, expected=1)
        assert commands[-1]["wire_requests"] == 0
        missing = {k: v for k, v in dispatch_params.items() if k != "pane_id"}
        cli("missing-pane", {"command": "dispatch", "params": missing}, expected=1)
        assert commands[-1]["wire_requests"] == 0
        assert commands[-1]["response"]["error"]["code"] == "missing_target"

        fixture("pane.send_text", {"pane_id": panes[1], "text": "HUMAN DRAFT DO NOT SUBMIT"})
        time.sleep(0.2)
        draft_read = fixture("pane.read", {"pane_id": panes[1], "source": "visible"})["read"]["text"]
        assert "HUMAN DRAFT DO NOT SUBMIT" in draft_read
        response = cli("draft-refusal", {"command": "dispatch", "params": {
            **dispatch_params, "pane_id": panes[1], "dispatch_id": "03.draft",
        }}, expected=1)
        assert response["error"]["code"] == "guard_refused" and "draft" in response["error"]["message"]
        after_read = fixture("pane.read", {"pane_id": panes[1], "source": "visible"})["read"]["text"]
        assert after_read == draft_read
        assert not list((directory / "member-1").glob("receipt-*.json"))

        response = cli("unreadable-refusal", {"command": "dispatch", "params": {
            **dispatch_params, "pane_id": panes[3], "dispatch_id": "03.unreadable",
            "input_box": {"agent": "adapter-fake", "start": "[UNKNOWN]", "end": "[/UNKNOWN]"},
        }}, expected=1)
        assert response["error"]["code"] == "guard_refused"
        if args.safe_defaults:
            response = cli("safe-defaults", {"command": "dispatch", "params": {
                **dispatch_params, "pane_id": panes[3], "dispatch_id": "03.defaults",
            }}, safe_defaults=True)
            assert response["result"]["guard"]["idle_seconds_observed"] >= 60
            assert response["result"]["guard"]["empty_read_gap_seconds"] >= 10
            receipt = wait_file(directory / "member-3" / "receipt-1.json")
            assert receipt["text"] == brief.read_text()
        assert not proxy_errors, proxy_errors
        print("PASS: dispatch receipt, observe, schema, missing pane, draft, unreadable, focus", flush=True)
    finally:
        if workspace_id is not None:
            fixture("workspace.close", {"workspace_id": workspace_id})
        after = raw("workspace.list", {})
        save("workspace-after.json", after)
        after_snapshot = raw("session.snapshot", {})["snapshot"]
        after_identity = {
            "focus": after_snapshot["focused_pane_id"],
            "panes": [{key: p.get(key) for key in ("pane_id", "terminal_id", "workspace_id", "tab_id", "cwd")}
                      for p in after_snapshot["panes"]],
        }
        save("identities-after.json", after_identity)
        save("focus.json", focused)
        save("wire.json", wire)
        stop.set()
        thread.join(2)
        listener.close()
        assert {w["workspace_id"] for w in after["workspaces"]} == baseline_ids
        assert after_identity == json.loads((directory / "identities-before.json").read_text())
        before_shape = [(w["workspace_id"], w.get("label"), w.get("pane_count")) for w in before["workspaces"]]
        after_shape = [(w["workspace_id"], w.get("label"), w.get("pane_count")) for w in after["workspaces"]]
        assert before_shape == after_shape
        dead = []
        for ready in directory.glob("member-*/ready.json"):
            pid = json.loads(ready.read_text())["pid"]
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    dead.append(pid)
                    break
                assert time.monotonic() < deadline, ("fake agent survived workspace close", pid)
                time.sleep(0.1)
        save("teardown.json", {"closed_workspace": workspace_id, "terminated_fake_agent_pids": dead})
        print("PASS: scratch workspace closed; original workspace/pane identities and focus unchanged", flush=True)
        print(json.dumps({"before": before_shape, "after": after_shape}), flush=True)


if __name__ == "__main__":
    main()
