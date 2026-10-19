#!/usr/bin/env python3
"""Opt-in, zero-LLM mission through lead/adapter/events/contracts/compliance."""

import argparse
import hashlib
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
from lead import contracts


class MissionProxy:
    def __init__(self, path, upstream, panes):
        self.path, self.upstream, self.panes = str(path), upstream, set(panes)
        self.wire, self.errors, self.stream_requests = [], [], []
        self.stop = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.threads = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                downstream, _ = self.listener.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(target=self.handle, args=(downstream,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def handle(self, downstream):
        try:
            with downstream, downstream.makefile("rb") as source, socket.socket(socket.AF_UNIX) as upstream:
                request = json.loads(source.readline())
                method, params = request["method"], request["params"]
                Schema().params(method, params)
                assert method in {"session.snapshot", "pane.read", "pane.send_input", "ping", "events.subscribe"}
                if method.startswith("pane."):
                    assert params["pane_id"] in self.panes
                if method == "events.subscribe":
                    assert all(e.get("pane_id") in self.panes for e in params["subscriptions"] if "pane_id" in e)
                self.wire.append(request)
                upstream.settimeout(5)
                upstream.connect(self.upstream)
                upstream.sendall((json.dumps(request) + "\n").encode())
                if method != "events.subscribe":
                    with upstream.makefile("rb") as response:
                        downstream.sendall(response.readline())
                    return
                while not self.stop.is_set():
                    readable, _, _ = select.select([upstream, downstream], [], [], 0.1)
                    if downstream in readable:
                        extra = downstream.recv(65536)
                        if extra:
                            self.stream_requests.append(extra.decode())
                        return
                    if upstream in readable:
                        data = upstream.recv(65536)
                        if not data:
                            return
                        downstream.sendall(data)
        except (OSError, ValueError):
            pass
        except Exception as error:
            self.errors.append(repr(error))

    def close(self):
        self.stop.set()
        self.thread.join(2)
        for thread in self.threads:
            thread.join(6)
        self.listener.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", required=True, action="store_true")
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="adapter-lead-live-", dir="/tmp"))
    root = directory / ".team"
    upstream = str(Path.home() / ".config/herdr/herdr.sock")
    label = "ADAPTER-LEAD-TEST-" + uuid.uuid4().hex[:8]
    token = uuid.uuid4().hex[:8]
    workspace, panes, proxy = None, [], None
    focus, commands, screens, turns = [], [], [], []
    schema = Schema()

    def save(name, value):
        (directory / name).write_text(json.dumps(value, indent=2) + "\n")

    def raw(method, params):
        schema.params(method, params)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(upstream)
            connection.sendall((json.dumps({"id": str(uuid.uuid4()), "method": method, "params": params}) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        assert "error" not in response, response
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
        return {"focus": {k: snapshot.get(k) for k in
                          ("focused_workspace_id", "focused_tab_id", "focused_pane_id")},
                "panes": [{k: p.get(k) for k in ("workspace_id", "tab_id", "pane_id", "terminal_id", "cwd")}
                          for p in snapshot["panes"]]}

    def check_focus(operation):
        current = identity(raw("session.snapshot", {})["snapshot"])["focus"]
        focus.append({"operation": operation, **current})
        assert current == before_identity["focus"]

    def wait_file(path, timeout=20):
        deadline = time.monotonic() + timeout
        while not path.exists():
            assert time.monotonic() < deadline, "missing " + str(path)
            time.sleep(0.05)
        return path.read_text()

    def wait_visible(member, fragment):
        deadline = time.monotonic() + 10
        while True:
            path = directory / member / "states.ndjson"
            if path.exists() and fragment in path.read_text():
                return
            assert time.monotonic() < deadline, "missing member state " + fragment
            time.sleep(0.05)

    def task(tid, owner, content, value, **extra):
        return {"id": tid, "owner": owner, "why": "A checked deduplicated contact list.",
                "outcome": f"Produce tasks/{tid}/result.txt from source.txt.",
                "value": value, "claims": {"artifact": {"path": f"tasks/{tid}/result.txt",
                                                       "sha256": hashlib.sha256(content.encode()).hexdigest()}},
                "stop_rule": "Stop after two attempts or 90 seconds; write an honest receipt.",
                "planned_commands": [], "files_changed": [f"tasks/{tid}/result.txt"], **extra}

    def run_job(payload):
        index = len(commands) + 1
        temporary = directory / f"job-{index}.tmp"
        temporary.write_text(json.dumps(payload))
        temporary.replace(directory / f"job-{index}.json")
        result = json.loads(wait_file(directory / f"result-{index}.json", timeout=35))
        commands.append(result)
        save("commands.json", commands)
        assert result["exit"] == 0, result
        check_focus("turn-" + str(index))
        entry = result["response"]["result"]
        turns.append(entry)
        save("turns.json", turns)
        # Passive scratch-only viewport snapshots are proof of visible contents,
        # not pixel tests or instructions to the renderer.
        for pane in panes:
            read = fixture("pane.read", {"pane_id": pane, "source": "visible", "format": "text"})["read"]
            screens.append({"turn": index, "pane": pane, "text": read["text"]})
        save("screens.json", screens)
        return entry

    def lead():
        return run_job({"command": "lead", "params": {"record_root": str(root), "wait_seconds": 0.5}})

    def git(*arguments, input=None):
        result = subprocess.run(["git", "-C", str(directory), *arguments], input=input,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def checkpoint(phase):
        git("add", "--", ".team", "source.txt", "AGENTS.md", ".gitignore")
        staged = git("diff", "--cached", "--name-only").splitlines()
        assert all(p.startswith(".team/") or p in {"source.txt", "AGENTS.md", ".gitignore"} for p in staged)
        assert not any(p.endswith(".lock") for p in staged)
        git("diff", "--cached", "--check")
        # Preserve the reviewed full staged diff and status, before committing only
        # this newly-created fixture repository with the configured Git identity.
        (directory / f"git-review-{phase}.diff").write_text(git("diff", "--cached"))
        (directory / f"git-status-{phase}.txt").write_text(git("status", "--short"))
        git("commit", "-F", "-", input=f"test(lead): {phase} micro-mission record\n\n"
            "Co-authored-by: factory-droid[bot] <138933559+factory-droid[bot]@users.noreply.github.com>\n")
        return git("rev-parse", "HEAD").strip()

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
        check_focus("create")
        for target, direction in ((0, "right"), (0, "down"), (1, "down")):
            split = fixture("pane.split", {"workspace_id": workspace, "target_pane_id": panes[target],
                                           "direction": direction, "cwd": str(directory),
                                           "ratio": 0.5, "focus": False})
            panes.append(split["pane"]["pane_id"])
            save("scratch.json", {"workspace_id": workspace, "panes": panes})
            check_focus("split")
        proxy = MissionProxy(directory / "proxy.sock", upstream, panes)
        (directory / "AGENTS.md").write_text("- Never run `git push --force`.\n")
        (directory / "source.txt").write_text(" Ada\nBea\nada\n")
        init = {"command": "init", "params": {
            "record_root": str(root), "project_root": str(directory), "workspace_id": workspace,
            "lead_pane_id": panes[0], "mission": "Normalize and deduplicate source.txt, count contacts, "
            "produce names=ada,bea; unique=2. Required tasks 7,8,9 must be receipt-checked.",
            "members": [{"name": name, "pane_id": panes[i],
                         "input_box": {"agent": "adapter-fake", "start": f"[INPUT {token}]",
                                       "end": f"[/INPUT {token}]"}} for i, name in
                        ((1, "normalizer"), (2, "counter"), (3, "reporter"))],
            "tasks": [task("10", "reporter", "", 1000, required=False,
                           planned_commands=["git push origin main --force"]),
                      task("7", "normalizer", "ada\nbea\n", 100,
                           help_answers={"duplicate policy": "Deduplicate after trimming and lowercasing; sort names."}),
                      task("8", "counter", "2\n", 90),
                      task("9", "reporter", "names=ada,bea; unique=2\n", 10, requests=[
                          {"question": "Switch the reporter's model?", "recommendation": "No; keep zero-cost fake."},
                          {"question": "Hire another reviewer?", "recommendation": "No; use the independent digest check."},
                      ])],
        }}
        result = subprocess.run([sys.executable, str(HERE.parent / "lead.py")],
                                input=json.dumps(init), capture_output=True, text=True, timeout=10)
        save("init.json", {"stdin": init, "stdout": result.stdout, "exit": result.returncode})
        assert result.returncode == 0, result.stdout + result.stderr
        git("init", "--quiet")
        (directory / ".gitignore").write_text(".team/.lead.lock\n")
        initial_commit = checkpoint("initial")
        time.sleep(2.5)
        for i, name in ((1, "normalizer"), (2, "counter"), (3, "reporter")):
            command = [sys.executable, str(HERE / "fake_mission.py"), "--workspace", workspace,
                       "--directory", str(directory / name), "--record", str(root), "--name", name,
                       "--token", token]
            fixture("pane.send_input", {"pane_id": panes[i], "text": shlex.join(command), "keys": ["enter"]})
            wait_file(directory / name / "ready.json")
            check_focus("start-" + name)
        command = [sys.executable, str(HERE / "lead_pane.py"), "--directory", str(directory),
                   "--socket", proxy.path]
        fixture("pane.send_input", {"pane_id": panes[0], "text": shlex.join(command), "keys": ["enter"]})
        wait_file(directory / "lead-ready.json")
        check_focus("start-lead")
        assert lead()["action"] == "compliance-refused"
        assert lead()["task"] == "7"
        assert lead()["task"] == "8"
        wait_visible("normalizer", "HELP 7")
        first = lead()
        assert first["action"] == "answer-help" and [c["priority"] for c in first["candidates"]] == [300, 200, 100]
        wait_visible("normalizer", "DONE 7")
        assert lead()["action"] == "accept"  # waiting task 8 before idle task 9
        assert lead()["action"] == "correct"
        correction = lead()
        assert correction["action"] == "dispatch" and correction["task"] == "7.2", correction
        wait_visible("normalizer", '"visible": "DONE 7 tasks/7.md"')
        late = lead()
        assert late["action"] == "reject-doorbell" and "7.2" in late["reason"], late
        state = json.loads((root / "lead-state.json").read_text())
        assert state["tasks"]["7"]["status"] == "sent"
        (directory / "release-correction").write_text("late DONE rejected; finish corrected attempt\n")
        wait_visible("normalizer", "DONE 7.2")
        assert lead()["action"] == "accept"
        assert lead()["task"] == "9"
        wait_visible("reporter", "HELD 9")
        escalation = lead()
        assert escalation["action"] == "escalate", escalation
        assert lead()["action"] == "wait-human"
        run_job({"command": "answer", "params": {
            "record_root": str(root), "escalation": escalation["escalation"], "by": "fixture-human",
            "decision": "continue", "answer": "Keep existing models and roster. Continue original scope.",
        }})
        assert lead()["action"] == "human-answer"
        wait_visible("reporter", "DONE 9")
        assert lead()["action"] == "accept"
        assert lead()["action"] == "complete"
        assert (root / "tasks/9/result.txt").read_text() == "names=ada,bea; unique=2\n"
        for tid in ("7", "7.2", "8", "9"):
            text = (root / f"tasks/{tid}.md").read_text()
            assert not contracts.validate_brief(text, tid)
            assert not contracts.validate_receipt(text, tid)
        assert not proxy.errors and not proxy.stream_requests, (proxy.errors, proxy.stream_requests)
        assert sum(r["method"] == "pane.send_input" for r in proxy.wire) == 4
        assert any(r["method"] == "events.subscribe" for r in proxy.wire)
        assert any("HELP 7" in s["text"] for s in screens)
        assert any("DONE 7.2" in s["text"] for s in screens)
        assert any("HELD 9" in s["text"] for s in screens)
        assert any("M9" in s["text"] for s in screens if s["pane"] == panes[0])
        final_commit = checkpoint("final")
        save("record-git.json", {"repository": str(directory), "initial": initial_commit,
                                 "final": final_commit, "tracked": git("ls-files").splitlines()})
        save("summary.json", {"outcome": "pass", "model_calls": 0, "turns": len(turns),
                              "dispatches": 4, "escalations": 1,
                              "result": (root / "tasks/9/result.txt").read_text()})
        print("PASS: micro-mission, leverage, HELP, checked receipts, correction, late DONE, human batch", flush=True)
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
        assert shape(before) == shape(after)
        assert before_identity == after_identity
        terminated = []
        paths = list(directory.glob("*/ready.json")) + list(directory.glob("lead-ready.json"))
        for path in paths:
            pid = json.loads(path.read_text())["pid"]
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    terminated.append(pid)
                    break
                assert time.monotonic() < deadline, ("survived workspace close", pid)
                time.sleep(0.1)
        save("teardown.json", {"closed_workspace": workspace, "terminated_fixture_pids": terminated,
                               "before": shape(before), "after": shape(after),
                               "identities_and_focus_unchanged": before_identity == after_identity})
        print("PASS: scratch closed, fixtures terminated, workspace order/counts and focus unchanged", flush=True)


if __name__ == "__main__":
    main()
