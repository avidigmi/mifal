#!/usr/bin/env python3
"""Zero-LLM, framed-input terminal fixture. Run only in the live test's owned panes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import sys
import termios
import time
import tty
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--cycle", action="store_true")
    args = parser.parse_args()
    pane_id = os.environ["HERDR_PANE_ID"]
    assert os.environ["HERDR_WORKSPACE_ID"] == args.workspace
    directory = Path(args.directory)
    directory.mkdir(exist_ok=True)

    def rpc(method, params):
        packet = {"id": str(uuid.uuid4()), "method": method, "params": params}
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(os.environ["HERDR_SOCKET_PATH"])
            connection.sendall((json.dumps(packet) + "\n").encode())
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
                assert "error" not in response, response
                return response["result"]

    def report(state):
        rpc("pane.report_agent", {
            "pane_id": pane_id, "source": "custom:adapter-test",
            "agent": "adapter-fake", "state": state, "message": args.name,
        })
        if args.cycle:
            snapshot = rpc("session.snapshot", {})["snapshot"]
            agent = next(a for a in snapshot["agents"] if a["pane_id"] == pane_id)
            assert agent["agent_status"] in ({state, "done"} if state == "idle" else {state})
            with (directory / "states.ndjson").open("a") as stream:
                stream.write(json.dumps({
                    "reported_at": time.time(), "pane_id": pane_id,
                    "reported_state": state, "state": agent["agent_status"],
                    "state_change_seq": agent["state_change_seq"],
                }) + "\n")
                stream.flush()

    text = ""
    marker = "ready"
    start = "[INPUT " + args.token + "]"
    end = "[/INPUT " + args.token + "]"

    def render():
        sys.stdout.write("\x1b[2J\x1b[H" + args.name + ": " + marker + "\r\n"
                         + start + "\r\n" + text.replace("\n", "\r\n")
                         + "\r\n" + end + "\r\n")
        sys.stdout.flush()

    old = termios.tcgetattr(sys.stdin)
    count = 0
    try:
        tty.setraw(sys.stdin)
        sys.stdout.write("\x1b[?2004h")
        report("idle")
        render()
        (directory / "ready.json").write_text(json.dumps({"pane_id": pane_id, "pid": os.getpid()}))
        pending = b""
        pasted = False
        next_cycle = time.monotonic() + 2
        states = iter(())
        while True:
            if args.cycle and time.monotonic() >= next_cycle:
                state = next(states, None)
                if state is None:
                    states = iter(("working", "blocked", "idle"))
                    state = next(states)
                report(state)
                next_cycle = time.monotonic() + 2
            readable, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not readable:
                continue
            data = os.read(sys.stdin.fileno(), 65536)
            if not data:
                return
            pending += data
            while pending:
                if pending.startswith(b"\x1b[200~"):
                    pasted = True
                    pending = pending[6:]
                    continue
                if pending.startswith(b"\x1b[201~"):
                    pasted = False
                    pending = pending[6:]
                    continue
                if pending.startswith(b"\x1b") and len(pending) < 6:
                    break
                byte, pending = pending[:1], pending[1:]
                if byte == b"\x03":
                    return
                if byte in (b"\r", b"\n") and not pasted:
                    count += 1
                    report("working")
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    marker = "RC" + str(sum(text.encode()))
                    receipt = {
                        "pane_id": pane_id, "text": text, "sha256": digest,
                        "marker": marker, "received_at": time.time(),
                    }
                    with (directory / f"receipt-{count}.json").open("x") as stream:
                        json.dump(receipt, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    text = ""
                    report("idle")
                elif byte in (b"\x7f", b"\x08"):
                    text = text[:-1]
                else:
                    # The live test deliberately uses ASCII briefs and drafts.
                    text += byte.decode("ascii")
                render()
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
