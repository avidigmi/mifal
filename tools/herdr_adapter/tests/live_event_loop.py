#!/usr/bin/env python3
"""Opt-in live JSON-seam loop tests; owns exactly one disposable scratch fleet."""

import argparse
import json
import os
from pathlib import Path
import select
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from adapter import Schema


class Proxy:
    """Read-only fault proxy. Never forwards a request to a focused/default pane."""

    def __init__(self, path, upstream, panes, mode):
        self.path, self.upstream, self.panes, self.mode = str(path), upstream, set(panes), mode
        self.schema = Schema()
        self.wire, self.injections, self.errors = [], [], []
        self.stream_requests = []
        self.subscriptions = 0
        self.stop = threading.Event()
        self.sockets, self.threads = [], []
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(target=self.handle, args=(connection,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def handle(self, downstream):
        try:
            with downstream, downstream.makefile("rb") as source, socket.socket(socket.AF_UNIX) as upstream:
                self.sockets.extend([downstream, upstream])
                request = json.loads(source.readline())
                self.schema.params(request["method"], request["params"])
                assert request["method"] in {"session.snapshot", "ping", "events.subscribe"}
                if request["method"] == "events.subscribe":
                    for entry in request["params"]["subscriptions"]:
                        if "pane_id" in entry:
                            assert entry["pane_id"] in self.panes
                self.wire.append({"at": time.time(), **request})
                upstream.settimeout(5)
                upstream.connect(self.upstream)
                upstream.sendall((json.dumps(request) + "\n").encode())
                if request["method"] != "events.subscribe":
                    with upstream.makefile("rb") as response:
                        downstream.sendall(response.readline())
                    return
                self.subscriptions += 1
                number = self.subscriptions
                started = time.monotonic()
                injected = False
                buffer = b""
                acked = False
                while not self.stop.is_set():
                    age = time.monotonic() - started
                    if number == 1 and age >= 1 and not injected and self.mode != "healthy":
                        injected = True
                        self.injections.append({"mode": self.mode, "at": time.time()})
                        if self.mode == "kill":
                            return
                        if self.mode == "slow":
                            # Stop reading the actual herdr stream, not just forwarding it.
                            if self.stop.wait(15):
                                return
                    readable, _, _ = select.select([upstream, downstream], [], [], 0.03)
                    if downstream in readable:
                        extra = downstream.recv(65536)
                        if extra:
                            self.stream_requests.append(extra.decode())
                        return
                    if upstream not in readable:
                        continue
                    chunk = upstream.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        frame = json.loads(line)
                        if "error" in frame:
                            self.injections.append({"server_error": frame["error"], "at": time.time()})
                        if not acked:
                            downstream.sendall(line + b"\n")
                            acked = True
                        elif not (number == 1 and injected and self.mode == "silence"):
                            downstream.sendall(line + b"\n")
        except (OSError, ValueError):
            pass
        except Exception as error:
            self.errors.append(repr(error))

    def close(self):
        self.stop.set()
        self.thread.join(2)
        for connection in self.sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in self.threads:
            thread.join(6)
        self.listener.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--soak-seconds", type=float, default=180)
    args = parser.parse_args()
    assert args.soak_seconds >= 180, "acceptance requires several minutes"
    directory = Path(tempfile.mkdtemp(prefix="adapter-loop-live-", dir="/tmp"))
    real_socket = os.path.expanduser("~/.config/herdr/herdr.sock")
    token = uuid.uuid4().hex[:8]
    label = "ADAPTER-LOOP-TEST-" + token
    workspace_id = None
    panes = []
    schema = Schema()
    focus_log = []
    commands = []
    summary = {}

    def save(name, value):
        (directory / name).write_text(json.dumps(value, indent=2) + "\n")

    def raw(method, params):
        schema.params(method, params)
        packet = {"id": str(uuid.uuid4()), "method": method, "params": params}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(real_socket)
            connection.sendall((json.dumps(packet) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        assert "error" not in response, response
        return response["result"]

    def fixture(method, params):
        if method == "workspace.create":
            assert params == {"label": label, "cwd": str(directory), "focus": False}
        elif method == "pane.split":
            assert params["workspace_id"] == workspace_id
            assert params["target_pane_id"] in panes and params["focus"] is False
        elif method == "pane.send_input":
            assert params["pane_id"] in panes
        elif method == "workspace.close":
            assert params["workspace_id"] == workspace_id and workspace_id not in baseline_ids
            owned = [w for w in raw("workspace.list", {})["workspaces"] if w["workspace_id"] == workspace_id]
            assert len(owned) == 1 and owned[0]["label"] == label
        else:
            raise AssertionError("unexpected fixture mutation: " + method)
        return raw(method, params)

    def identity(snapshot):
        return {
            "focus": {k: snapshot.get(k) for k in ("focused_pane_id", "focused_tab_id", "focused_workspace_id")},
            "panes": [{k: p.get(k) for k in ("pane_id", "terminal_id", "workspace_id", "tab_id", "cwd")}
                      for p in snapshot["panes"]],
        }

    def focus_check(operation):
        focus = identity(raw("session.snapshot", {})["snapshot"])["focus"]
        focus_log.append({"operation": operation, **focus})
        assert focus == before_identity["focus"], focus_log

    def run(name, duration, mode="healthy", **overrides):
        proxy = Proxy(directory / (name + ".sock"), real_socket, panes, mode)
        params = {
            "workspace_id": workspace_id, "duration_seconds": duration, "floor_seconds": 3,
            "ping_seconds": 2, "expected_event_seconds": 1, "silence_periods": 30,
            "retry_seconds": 0.2, "request_timeout_seconds": 1, **overrides,
        }
        payload = {"command": "watch", "params": params}
        argv = [sys.executable, str(HERE.parent / "adapter.py"), "--socket", proxy.path]
        started = time.time()
        try:
            with (directory / (name + ".ndjson")).open("w") as log:
                process = subprocess.run(argv, input=json.dumps(payload), text=True, stdout=subprocess.PIPE,
                                         stderr=log, timeout=duration + 15)
            result = json.loads(process.stdout)
            commands.append({"name": name, "argv": argv, "stdin": payload, "exit": process.returncode,
                             "started_at": started, "ended_at": time.time(), "response": result})
            save("commands.json", commands)
            assert process.returncode == 0, result
            assert {m["pane_id"] for m in result["result"]["picture"]["members"]} == set(panes)
            assert not proxy.errors and not proxy.stream_requests, (proxy.errors, proxy.stream_requests)
            rows = [json.loads(line) for line in (directory / (name + ".ndjson")).read_text().splitlines()]
            assert result["result"]["stats"]["pings"] >= 2
            focus_check(name)
            return result["result"], rows, proxy.injections, started
        finally:
            proxy.close()
            save(name + "-wire.json", proxy.wire)
            save(name + "-proxy.json", {
                "injections": proxy.injections, "errors": proxy.errors,
                "requests_on_stream": proxy.stream_requests,
            })

    before = raw("workspace.list", {})
    before_identity = identity(raw("session.snapshot", {})["snapshot"])
    baseline_ids = {w["workspace_id"] for w in before["workspaces"]}
    save("workspace-before.json", before)
    save("identities-before.json", before_identity)
    print("Evidence directory:", directory, flush=True)
    try:
        created = fixture("workspace.create", {"label": label, "cwd": str(directory), "focus": False})
        workspace_id = created["workspace"]["workspace_id"]
        assert workspace_id not in baseline_ids
        panes.append(created["root_pane"]["pane_id"])
        save("scratch.json", {"workspace_id": workspace_id, "panes": panes})
        focus_check("create")
        for direction in ("right", "down", "right"):
            split = fixture("pane.split", {
                "workspace_id": workspace_id, "target_pane_id": panes[0], "direction": direction,
                "cwd": str(directory), "ratio": 0.5, "focus": False,
            })
            panes.append(split["pane"]["pane_id"])
            save("scratch.json", {"workspace_id": workspace_id, "panes": panes})
            focus_check("split")
        time.sleep(2.5)
        for index, pane in enumerate(panes):
            member_dir = directory / ("member-" + str(index))
            command = [sys.executable, str(HERE / "fake_agent.py"), "--workspace", workspace_id,
                       "--directory", str(member_dir), "--name", "loop-" + str(index), "--token", token, "--cycle"]
            fixture("pane.send_input", {"pane_id": pane, "text": shlex.join(command), "keys": ["enter"]})
            deadline = time.monotonic() + 10
            while not (member_dir / "ready.json").exists():
                assert time.monotonic() < deadline, "fake agent did not start"
                time.sleep(0.1)
            focus_check("start-fake")

        result, rows, injections, _ = run("killed", 8, "kill")
        assert any(r["kind"] == "death" and r["reason"] == "stream_closed" for r in rows), rows
        assert result["stats"]["reconnects"] >= 1
        assert sum(r["kind"] == "bootstrap_applied" for r in rows) >= 2
        assert any(r["kind"] == "event" and r["generation"] >= 2 for r in rows)
        summary["killed"] = {"outcome": "pass", "stats": result["stats"]}

        result, rows, injections, _ = run("silent", 10, "silence", silence_periods=4, floor_seconds=0.5)
        deaths = [r for r in rows if r["kind"] == "death" and r["reason"] == "silence"]
        assert deaths and 4 <= deaths[0]["silence_seconds"] < 4.25, deaths
        assert result["stats"]["reconnects"] >= 1
        floor_changes = [r for r in rows if r["kind"] == "reconcile" and r["reason"] == "floor"
                         and r["changes"] and r["generation"] == 1 and r["elapsed_seconds"] > 1]
        assert floor_changes, rows
        assert any(r["kind"] == "event" and r["generation"] >= 2 for r in rows)
        summary["silent_and_floor"] = {"outcome": "pass", "deaths": deaths, "floor_changes": len(floor_changes)}

        result, rows, injections, _ = run("slow", 24, "slow", include_updates=True)
        lost = any(r.get("server_error", {}).get("code") == "events_lost" for r in injections)
        if lost:
            assert any(r["kind"] == "death" and r["reason"] == "events_lost" for r in rows)
            assert result["stats"]["reconnects"] >= 1
        summary["slow"] = {
            "outcome": "pass" if lost else "attempted-and-covered-by-unit-test",
            "paused_upstream_read_seconds": 15, "events_lost_observed": lost,
            "stats": result["stats"],
        }

        result, rows, _, started = run("soak", args.soak_seconds)
        assert result["stats"]["deaths"] == result["stats"]["reconnects"] == 0
        expected = []
        for path in directory.glob("member-*/states.ndjson"):
            expected.extend(json.loads(line) for line in path.read_text().splitlines()
                            if started + 1 < json.loads(line)["reported_at"] < started + args.soak_seconds - 1)
        observed = {}
        for row in rows:
            if row["kind"] == "reconcile":
                for change in row["changes"]:
                    member = change["after"]
                    if member:
                        observed[(member["pane_id"], member["state_change_seq"])] = {
                            "state": member["agent_status"], "reason": row["reason"],
                        }
        missing = [e for e in expected if observed.get((e["pane_id"], e["state_change_seq"]), {}).get("state") != e["state"]]
        floor_only = [e for e in expected if observed.get((e["pane_id"], e["state_change_seq"]), {}).get("reason") == "floor"]
        summary["soak"] = {
            "outcome": "pass" if not missing else "fail", "duration_seconds": args.soak_seconds,
            "expected_transitions": len(expected), "missing": missing, "floor_only": len(floor_only),
            "stats": result["stats"],
        }
        save("summary.json", summary)
        assert expected and not missing, summary["soak"]
        # A floor racing an event is legitimate; events must still carry the fast path.
        assert len(floor_only) < len(expected) * 0.1, summary["soak"]
        assert any(r["kind"] == "ping" and r["stream_connected"] for r in rows)
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        if workspace_id:
            fixture("workspace.close", {"workspace_id": workspace_id})
        after = raw("workspace.list", {})
        after_identity = identity(raw("session.snapshot", {})["snapshot"])
        save("workspace-after.json", after)
        save("identities-after.json", after_identity)
        save("focus.json", focus_log)
        save("summary.json", summary)
        shape = lambda value: [(w["workspace_id"], w.get("label"), w.get("pane_count")) for w in value["workspaces"]]
        assert shape(before) == shape(after)
        assert after_identity == before_identity
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
                assert time.monotonic() < deadline, ("fake agent survived", pid)
                time.sleep(0.1)
        save("teardown.json", {
            "closed_workspace": workspace_id, "terminated_fake_agent_pids": dead,
            "workspaces_unchanged": shape(before) == shape(after),
            "identities_and_focus_unchanged": after_identity == before_identity,
            "before": shape(before), "after": shape(after),
        })
        print("PASS: owned workspace closed; fake agents dead; original workspaces, identities and focus unchanged",
              flush=True)


if __name__ == "__main__":
    main()
