#!/usr/bin/env python3
"""Deterministic local micro-mission; never launches or calls an LLM."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import sys
import termios
import time
import tty
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lead import contracts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    assert os.environ["HERDR_WORKSPACE_ID"] == args.workspace
    pane = os.environ["HERDR_PANE_ID"]
    args.directory.mkdir()
    root = args.record
    text, message = "", "ready"

    def rpc(method, params):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(5)
            connection.connect(os.environ["HERDR_SOCKET_PATH"])
            connection.sendall((json.dumps({"id": str(uuid.uuid4()), "method": method, "params": params}) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        assert "error" not in response, response
        return response["result"]

    def render():
        sys.stdout.write("\x1b[2J\x1b[H" + args.name + "\r\n" + message +
                         f"\r\n[INPUT {args.token}]\r\n" + text.replace("\n", "\r\n") +
                         f"\r\n[/INPUT {args.token}]\r\n")
        sys.stdout.flush()

    def report(status, visible):
        nonlocal message
        message = visible
        render()
        rpc("pane.report_agent", {"pane_id": pane, "source": "custom:lead-test",
                                 "agent": "adapter-fake", "state": status, "message": args.name})
        snap = rpc("session.snapshot", {})["snapshot"]
        live = next(a for a in snap["agents"] if a["pane_id"] == pane)
        with (args.directory / "states.ndjson").open("a") as stream:
            stream.write(json.dumps({"at": time.time(), "reported_state": status,
                                     "state": live["agent_status"], "pane_id": pane,
                                     "state_change_seq": live["state_change_seq"],
                                     "visible": visible}) + "\n")

    def wait_file(path, seconds=90):
        deadline = time.monotonic() + seconds
        while not path.exists():
            if time.monotonic() > deadline:
                raise RuntimeError("fixture cap waiting for " + str(path))
            time.sleep(0.05)
        return path.read_text()

    def receipt(path, active, content):
        base = active.split(".")[0]
        sidecar = root / f"tasks/{base}/result.txt"
        sidecar.parent.mkdir(exist_ok=True, parents=True)
        sidecar.write_text(content)
        digest = hashlib.sha256(content.encode()).hexdigest()
        returned = (f"\n## Return — attempt {active}\n\nCloses: {active}\n\n"
                    f"### Result\nProduced tasks/{base}/result.txt\n\n### Claims\n"
                    f"- artifact: passed (sha256 {digest})\n\n### Evidence\n"
                    f"Computed from source.txt; output digest {digest}.\n\n"
                    f"### Sidecars\n- tasks/{base}/result.txt\n\n"
                    "### What I wish the brief had said\nNothing; fixture intentionally demonstrates review.\n")
        assert not contracts.validate_receipt(returned, "fixture")
        with path.open("a") as stream:
            stream.write(returned)
            stream.flush()
            os.fsync(stream.fileno())
        line = f"DONE {active} tasks/{active}.md"
        assert not contracts.parse_doorbell(line)[1]
        report("idle", line)

    def run_brief(prompt):
        match = re.fullmatch(r"Read brief (\S+)\. Return there; honor done-means and stop rule\.", prompt)
        assert match, prompt
        path = Path(match[1]).resolve()
        assert root.resolve() in path.parents
        brief = path.read_text()
        assert not contracts.validate_brief(brief, str(path))
        fields, _, _ = contracts.parse_frontmatter(brief)
        active = fields.get("attempt", fields["task"])
        with (args.directory / "received.ndjson").open("a") as stream:
            stream.write(json.dumps({"prompt": prompt, "brief": brief, "active": active,
                                     "sha256": hashlib.sha256(brief.encode()).hexdigest()}) + "\n")
        report("working", "working " + active)
        raw = (root.parent / "source.txt").read_text().splitlines()
        names = [n.strip().lower() for n in raw if n.strip()]
        if active == "7":
            wait_file(root.parent / "ring-help")
            report("blocked", "HELP 7 duplicate policy")
            answer = wait_file(root / "answers/7.md")
            assert "Deduplicate" in answer
            report("working", "using lead answer")
            # Deliberately faulty first result proves the lead checks evidence,
            # rather than accepting the fixture's confident passed claim.
            receipt(path, active, "\n".join(names) + "\n")
        elif active == "7.2":
            report("working", "DONE 7 tasks/7.md")
            wait_file(root.parent / "release-correction")
            receipt(path, active, "\n".join(sorted(set(names))) + "\n")
        elif active == "8":
            receipt(path, active, str(len(set(names))) + "\n")
            (root.parent / "ring-help").write_text("task 8 report ready\n")
        elif active == "9":
            wait_file(root / "reviews/7.2.json")
            report("blocked", "HELD 9 model and hiring")
            answer = wait_file(root / "answers/9-human.md")
            assert "Decision: continue" in answer
            names_out = wait_file(root / "tasks/7/result.txt").splitlines()
            count = wait_file(root / "tasks/8/result.txt").strip()
            receipt(path, active, f"names={','.join(names_out)}; unique={count}\n")
        else:
            raise AssertionError("unexpected task: " + active)

    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin)
        sys.stdout.write("\x1b[?2004h")
        report("idle", "ready")
        (args.directory / "ready.json").write_text(json.dumps({"pane_id": pane, "pid": os.getpid()}))
        pending, pasted = b"", False
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if not select.select([sys.stdin], [], [], 0.1)[0]:
                continue
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                return
            pending += chunk
            while pending:
                if pending.startswith(b"\x1b[200~"):
                    pasted, pending = True, pending[6:]
                    continue
                if pending.startswith(b"\x1b[201~"):
                    pasted, pending = False, pending[6:]
                    continue
                if pending.startswith(b"\x1b") and len(pending) < 6:
                    break
                byte, pending = pending[:1], pending[1:]
                if byte == b"\x03":
                    return
                if byte in (b"\r", b"\n") and not pasted:
                    prompt, text = text, ""
                    run_brief(prompt)
                elif byte in (b"\x7f", b"\x08"):
                    text = text[:-1]
                else:
                    text += byte.decode("ascii")
                render()
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
