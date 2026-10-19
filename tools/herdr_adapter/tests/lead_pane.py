#!/usr/bin/env python3
"""Bounded test driver in the lead's own pane; each job is one JSON invocation."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    assert os.environ["HERDR_ENV"] == "1"
    directory = args.directory
    argv = [sys.executable, str(Path(__file__).resolve().parents[1] / "lead.py"),
            "--socket", args.socket, "--state-dir", str(directory / "adapter-state"),
            "--idle-seconds", "0.25", "--read-gap-seconds", "0.2"]
    (directory / "lead-ready.json").write_text(json.dumps({
        "pid": os.getpid(), "pane_id": os.environ["HERDR_PANE_ID"], "argv": argv,
    }))
    print("Lead ready: bounded turns, zero model calls.", flush=True)
    deadline = time.monotonic() + 240
    for index in range(1, 41):
        job = directory / f"job-{index}.json"
        while not job.exists():
            assert time.monotonic() < deadline, "lead fixture cap"
            time.sleep(0.05)
        payload = job.read_text()
        with (directory / f"turn-{index}.ndjson").open("w") as log:
            result = subprocess.run(argv, input=payload, text=True, stdout=subprocess.PIPE,
                                    stderr=log, timeout=30)
        response = json.loads(result.stdout)
        out = {"argv": argv, "stdin": json.loads(payload), "exit": result.returncode, "response": response}
        target = directory / f"result-{index}.json"
        temporary = directory / f"result-{index}.tmp"
        temporary.write_text(json.dumps(out, indent=2) + "\n")
        temporary.replace(target)
        entry = response.get("result", {})
        print(f"{index}: {entry.get('moment', '')} {entry.get('action', response)} "
              f"{entry.get('task', '')}", flush=True)
        if entry.get("action") == "complete" or result.returncode:
            return


if __name__ == "__main__":
    main()
