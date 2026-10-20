import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


CLI = Path(__file__).resolve().parents[1] / "adapter.py"

PANE = {
    "pane_id": "scratch:p1", "workspace_id": "scratch", "tab_id": "scratch:t1",
    "terminal_id": "terminal-unique", "agent": "fake", "cwd": "/tmp",
    "agent_status": "idle", "focused": False, "revision": 1,
    "scroll": {"offset_from_bottom": 0, "viewport_rows": 24, "max_offset_from_bottom": 0},
}
AGENT = {**PANE, "name": "alpha", "state_change_seq": 1}
SNAPSHOT = {
    "version": "0.9.3", "protocol": 22, "focused_pane_id": "human:p1",
    "workspaces": [{"workspace_id": "scratch", "label": "ADAPTER-TEST", "pane_count": 1}],
    "tabs": [], "panes": [PANE], "agents": [AGENT], "layouts": [],
}


class Peer:
    """A disposable wire peer; assertions exercise only the JSON CLI seam."""

    def __init__(self, directory):
        self.path = str(Path(directory) / "herdr.sock")
        self.requests = []
        self.times = []
        self.reply = lambda request: {"type": "pong"}
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while self.running:
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            with conn, conn.makefile("rb") as stream:
                line = stream.readline()
                if not line:
                    continue
                request = json.loads(line)
                self.requests.append(request)
                self.times.append(time.monotonic())
                result = self.reply(request)
                if result is None:
                    continue
                packet = result if isinstance(result, bytes) else (
                    json.dumps({"id": request["id"], "result": result}) + "\n").encode()
                conn.sendall(packet)

    def close(self):
        self.running = False
        self.thread.join(2)
        self.listener.close()


class CLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adapter-", dir="/tmp")
        self.peer = Peer(self.temp.name)

    def tearDown(self):
        self.peer.close()
        self.temp.cleanup()

    def cli(self, payload, *flags, cli_path=CLI):
        result = subprocess.run(
            [sys.executable, str(cli_path), "--socket", self.peer.path,
             "--state-dir", self.temp.name + "/state", *flags],
            input=json.dumps(payload), text=True, capture_output=True, timeout=30,
            env={**os.environ, "HERDR_PANE_ID": "human:focused"},
        )
        self.assertEqual(result.stderr, "", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_invalid_split_param_is_refused_without_connecting(self):
        status, response = self.cli({
            "command": "validate",
            "params": {"method": "pane.split",
                       "params": {"pane_id": "w1:p1", "direction": "right"}},
        })
        self.assertEqual(status, 1)
        self.assertEqual(response["error"]["code"], "schema_invalid")
        self.assertEqual(self.peer.requests, [])

    def test_observe_returns_members_and_panes_from_snapshot(self):
        self.peer.reply = lambda request: {"type": "session_snapshot", "snapshot": SNAPSHOT}
        status, response = self.cli({"command": "observe", "params": {"workspace_id": "scratch"}})
        self.assertEqual(status, 0, response)
        self.assertEqual(response["result"]["members"][0]["name"], "alpha")
        self.assertEqual(response["result"]["members"][0]["agent_status"], "idle")
        self.assertEqual(response["result"]["panes"][0]["pane_id"], "scratch:p1")
        self.assertEqual([r["method"] for r in self.peer.requests], ["session.snapshot"])

    def dispatch_payload(self, **changes):
        params = {
            "pane_id": "scratch:p1", "workspace_id": "scratch",
            "dispatch_id": "brief-7.2", "text": "First line\nSecond line",
            "input_box": {"agent": "fake", "start": "[INPUT]", "end": "[/INPUT]"},
        }
        params.update(changes)
        return {"command": "dispatch", "params": params}

    def ready_peer(self, text="hello\n[INPUT]\n\n[/INPUT]\n"):
        def reply(request):
            if request["method"] == "session.snapshot":
                return {"snapshot": SNAPSHOT}
            if request["method"] == "pane.read":
                return {"read": {"pane_id": "scratch:p1", "text": text, "truncated": False}}
            return {"type": "input_sent"}
        self.peer.reply = reply

    def dispatch(self, **changes):
        return self.cli(self.dispatch_payload(**changes),
                        "--idle-seconds", "0.06", "--read-gap-seconds", "0.04",
                        "--shell-ready-ms", "500")

    def test_dispatch_guards_then_records_sent_without_claiming_delivery(self):
        self.ready_peer()
        status, response = self.dispatch()
        self.assertEqual(status, 0, response)
        result = response["result"]
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["delivery"], "unverified")
        record = json.loads(Path(result["record_path"]).read_text())
        self.assertEqual(record["brief_sha256"], hashlib.sha256(b"First line\nSecond line").hexdigest())
        self.assertEqual(record["status"], "sent")
        self.assertEqual(Path(result["brief_path"]).read_text(), "First line\nSecond line")
        reads = [r for r in self.peer.requests if r["method"] == "pane.read"]
        self.assertEqual(len(reads), 2)
        writes = [r for r in self.peer.requests if r["method"] == "pane.send_input"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["params"], {
            "pane_id": "scratch:p1", "text": "First line\nSecond line", "keys": ["enter"],
        })
        self.assertGreaterEqual(result["guard"]["idle_seconds_observed"], 0.06)
        self.assertGreaterEqual(result["guard"]["empty_read_gap_seconds"], 0.04)
        read_times = [t for t, r in zip(self.peer.times, self.peer.requests) if r["method"] == "pane.read"]
        self.assertGreaterEqual(read_times[1] - read_times[0], 0.04)
        self.assertEqual(len({r["id"] for r in self.peer.requests}), len(self.peer.requests))

    def assert_no_writes(self):
        self.assertFalse(any(r["method"] not in {"pane.read", "session.snapshot"} for r in self.peer.requests))

    def test_missing_pane_does_not_fall_back_to_injected_or_focused_pane(self):
        payload = self.dispatch_payload()
        del payload["params"]["pane_id"]
        status, response = self.cli(payload)
        self.assertEqual(status, 1)
        self.assertEqual(response["error"]["code"], "missing_target")
        self.assertEqual(self.peer.requests, [])

    def test_draft_is_preserved(self):
        self.ready_peer("[INPUT]\nHuman's half-written draft\n[/INPUT]\n")
        status, response = self.dispatch()
        self.assertEqual(status, 1)
        self.assertIn("draft", response["error"]["message"])
        self.assert_no_writes()

    def test_unreadable_ambiguous_and_non_bottom_boxes_are_refused(self):
        for text in ("", "a generic prompt > ", "[INPUT]\n\n[/INPUT]\nother text",
                     "[INPUT]\n[/INPUT]\n[INPUT]\n[/INPUT]"):
            with self.subTest(text=text):
                self.ready_peer(text)
                status, response = self.dispatch()
                self.assertEqual(status, 1, response)
                self.assertEqual(response["error"]["code"], "guard_refused")
        self.assert_no_writes()

    def test_second_read_catches_a_new_draft(self):
        self.ready_peer()
        normal = self.peer.reply
        def reply(request):
            result = normal(request)
            if request["method"] == "pane.read" and sum(r["method"] == "pane.read" for r in self.peer.requests) == 2:
                result["read"]["text"] = "[INPUT]\nnew draft\n[/INPUT]"
            return result
        self.peer.reply = reply
        status, response = self.dispatch()
        self.assertEqual(status, 1, response)
        self.assertIn("draft", response["error"]["message"])
        self.assert_no_writes()

    def test_idle_sequence_change_is_detected_even_if_still_idle(self):
        self.ready_peer()
        normal = self.peer.reply
        def reply(request):
            result = json.loads(json.dumps(normal(request)))
            if request["method"] == "session.snapshot" and len(self.peer.requests) > 1:
                result["snapshot"]["agents"][0]["state_change_seq"] = 3
            return result
        self.peer.reply = reply
        status, response = self.dispatch()
        self.assertEqual(status, 1, response)
        self.assertIn("transition changed", response["error"]["message"])
        self.assert_no_writes()

    def test_busy_scrolled_unknown_and_replaced_panes_are_refused(self):
        for change in (
            {"agent_status": "working"},
            {"scroll": None},
            {"scroll": {"offset_from_bottom": 3}},
            {"terminal_id": "replacement"},
        ):
            with self.subTest(change=change):
                self.ready_peer()
                normal = self.peer.reply
                def reply(request):
                    result = json.loads(json.dumps(normal(request)))
                    if request["method"] == "session.snapshot":
                        result["snapshot"]["panes"][0].update(change)
                    return result
                self.peer.reply = reply
                status, response = self.dispatch()
                self.assertEqual(status, 1, response)
        self.assert_no_writes()

    def test_wrong_workspace_cannot_target_a_real_pane(self):
        self.ready_peer()
        status, response = self.dispatch(workspace_id="human")
        self.assertEqual(status, 1)
        self.assertEqual(response["error"]["code"], "target_mismatch")
        self.assert_no_writes()

    def test_file_content_is_sent_and_duplicate_id_is_not_resent(self):
        self.ready_peer()
        brief = Path(self.temp.name) / "brief.md"
        brief.write_text("Read this file's content.\n")
        payload = self.dispatch_payload(brief_file=str(brief))
        del payload["params"]["text"]
        flags = ("--idle-seconds", "0.01", "--read-gap-seconds", "0.01")
        status, response = self.cli(payload, *flags)
        self.assertEqual(status, 0, response)
        self.assertEqual(self.peer.requests[-1]["params"]["text"], brief.read_text())
        before = len(self.peer.requests)
        status, response = self.cli(payload, *flags)
        self.assertEqual(status, 1)
        self.assertEqual(response["error"]["code"], "duplicate_dispatch")
        self.assertEqual(len(self.peer.requests), before)

    def test_lost_ack_is_uncertain_never_automatically_retried(self):
        self.ready_peer()
        normal = self.peer.reply
        self.peer.reply = lambda r: None if r["method"] == "pane.send_input" else normal(r)
        status, response = self.dispatch()
        self.assertEqual(status, 1, response)
        self.assertEqual(response["error"]["code"], "dispatch_uncertain")
        record = json.loads(Path(response["error"]["record_path"]).read_text())
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(record["delivery"], "unverified")
        status, response = self.dispatch()
        self.assertEqual(response["error"]["code"], "duplicate_dispatch")
        self.assertEqual(sum(r["method"] == "pane.send_input" for r in self.peer.requests), 1)

    def test_corrupt_bundled_schema_returns_json_refusal(self):
        copied_cli = Path(self.temp.name) / "adapter.py"
        shutil.copy2(CLI, copied_cli)
        original = json.loads(CLI.with_name("api-schema.json").read_text())
        variants = [("missing_request", {"schemas": {}}), ("wrong_container", {"schemas": None})]
        for name, ref in (
            ("missing_ref", "#/schemas/request/$defs/Absent"),
            ("nonlocal_ref", "https://example.invalid/schema.json#/params"),
            ("wrong_ref_type", []),
            ("wrong_ref_target", "#/schemas/request/oneOf/missing"),
        ):
            schema = json.loads(json.dumps(original))
            branch = next(b for b in schema["schemas"]["request"]["oneOf"]
                          if b["properties"]["method"]["const"] == "pane.read")
            branch["properties"]["params"] = {"$ref": ref}
            variants.append((name, schema))
        for name, params in (
            ("invalid_regex", {"type": "object", "properties": {
                "pane_id": {"type": "string", "pattern": "["}, "source": {"type": "string"},
            }}),
            ("cyclic_ref", {"$ref": "#/corrupt_params"}),
            ("invalid_uint", {"type": "object", "properties": {
                "pane_id": {"type": "string"}, "source": {"type": "string"},
                "lines": {"type": "integer", "format": "uintXX"},
            }}),
        ):
            schema = json.loads(json.dumps(original))
            branch = next(b for b in schema["schemas"]["request"]["oneOf"]
                          if b["properties"]["method"]["const"] == "pane.read")
            branch["properties"]["params"] = params
            schema["corrupt_params"] = params
            variants.append((name, schema))
        for name, schema in variants:
            with self.subTest(corruption=name):
                copied_cli.with_name("api-schema.json").write_text(json.dumps(schema))
                status, response = self.cli({
                    "command": "validate", "params": {
                        "method": "pane.read", "params": {
                            "pane_id": "scratch:p1", "source": "visible",
                            **({"lines": 10} if name == "invalid_uint" else {}),
                        },
                    },
                }, cli_path=copied_cli)
                self.assertEqual(status, 1, response)
                self.assertEqual(response["error"]["code"], "schema_corrupt")
        self.assertEqual(self.peer.requests, [])

    def test_dispatch_wire_hazards_preserve_failure_classification_and_reservations(self):
        for hazard in ("server_error", "id_mismatch", "malformed", "non_ndjson", "truncated"):
            with self.subTest(hazard=hazard):
                self.ready_peer()
                normal = self.peer.reply
                def reply(request):
                    result = normal(request)
                    if hazard == "truncated" and request["method"] == "pane.read":
                        result["read"]["truncated"] = True
                    elif request["method"] == "pane.send_input":
                        if hazard == "malformed":
                            return b"{not-json}\n"
                        if hazard == "non_ndjson":
                            return json.dumps({"id": request["id"], "result": {}}).encode()
                        return (json.dumps({
                            "id": "unsolicited" if hazard == "id_mismatch" else request["id"],
                            "error": {"code": "pane_not_found", "message": "pane not found"},
                        }) + "\n").encode()
                    return result
                self.peer.reply = reply
                before = len(self.peer.requests)
                status, response = self.dispatch(dispatch_id=hazard)
                self.assertEqual(status, 1, response)
                if hazard == "truncated":
                    self.assertEqual(response["error"]["code"], "guard_refused")
                    self.assertFalse(any(r["method"] == "pane.send_input" for r in self.peer.requests[before:]))
                    self.assertFalse((Path(self.temp.name) / "state/dispatches" / hazard).exists())
                    continue
                expected = "refused" if hazard == "server_error" else "uncertain"
                self.assertEqual(response["error"]["code"], "dispatch_" + expected)
                record = json.loads(Path(response["error"]["record_path"]).read_text())
                self.assertEqual(record["status"], expected)
                self.assertEqual(record["delivery"], "unverified")
                before_retry = len(self.peer.requests)
                status, response = self.dispatch(dispatch_id=hazard)
                self.assertEqual(status, 1, response)
                self.assertEqual(response["error"]["code"], "duplicate_dispatch")
                self.assertEqual(len(self.peer.requests), before_retry)

    def test_connection_refused_after_guard_remains_uncertain(self):
        self.ready_peer()
        normal = self.peer.reply
        def reply(request):
            result = normal(request)
            reads = sum(r["method"] == "pane.read" for r in self.peer.requests)
            if request["method"] == "session.snapshot" and reads == 2:
                # Final guard response succeeds; the following send cannot connect.
                self.peer.running = False
                self.peer.listener.close()
            return result
        self.peer.reply = reply
        status, response = self.dispatch()
        self.assertEqual(status, 1, response)
        self.assertEqual(response["error"]["code"], "dispatch_uncertain")
        record = json.loads(Path(response["error"]["record_path"]).read_text())
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(record["delivery"], "unverified")
        self.assertIn("Connection refused", record["error"])
        self.assert_no_writes()
        status, response = self.dispatch()
        self.assertEqual(response["error"]["code"], "duplicate_dispatch")

    def test_schema_checks_refs_enums_nested_fields_types_and_ranges(self):
        cases = [
            ("pane.read", {"pane_id": "p", "source": "recent-unwrapped"}, False),
            ("pane.read", {"pane_id": "p", "source": "recent_unwrapped"}, True),
            ("pane.read", {"pane_id": "p", "source": "visible", "lines": True}, False),
            ("pane.read", {"pane_id": "p", "source": "visible", "lines": -1}, False),
            ("pane.read", {"pane_id": "p", "source": "visible", "lines": 2 ** 32}, False),
            ("pane.read", {"pane_id": "p", "source": "visible", "lines": 10 ** 1000}, False),
            ("pane.send_input", {"pane_id": "p", "keys": [1]}, False),
            ("pane.split", {"target_pane_id": "p", "direction": "right", "focus": False}, True),
            ("pane.split", {"target_pane_id": "p", "direction": "rgiht"}, False),
            ("agent.prompt", {"target": "p", "text": "hi", "wait": {"until": "idle", "typo": 1}}, False),
            ("events.subscribe", {"subscriptions": [{"type": "pane.agent_status_changed"}]}, False),
            ("events.subscribe", {"subscriptions": [{"type": "pane.agent_status_changed", "pane_id": "p"}]}, True),
        ]
        for method, params, valid in cases:
            with self.subTest(method=method, params=params):
                status, response = self.cli({"command": "validate", "params": {"method": method, "params": params}})
                self.assertEqual(status, 0 if valid else 1, response)
        self.assertEqual(self.peer.requests, [])

    def test_arbitrary_rpc_focus_and_terminal_controls_are_not_forwarded(self):
        for payload in (
            {"command": "pane.send_text", "params": {"pane_id": "human", "text": "bad"}},
            self.dispatch_payload(focus=True),
            self.dispatch_payload(text="malicious\x1b[201~\n"),
            self.dispatch_payload(input_box=None),
        ):
            with self.subTest(payload=payload):
                status, response = self.cli(payload)
                self.assertEqual(status, 1, response)
        self.assertEqual(self.peer.requests, [])

    def test_invalid_timing_values_refuse_before_connecting(self):
        for flags in (("--idle-seconds", "nan"), ("--idle-seconds", "0"),
                      ("--read-gap-seconds", "-1"), ("--shell-ready-ms", "0"),
                      ("--shell-ready-ms", "2501")):
            status, response = self.cli(self.dispatch_payload(), *flags)
            self.assertEqual(status, 1, response)
        self.assertEqual(self.peer.requests, [])

    def test_concurrent_dispatchers_cannot_type_into_the_same_pane(self):
        self.ready_peer()
        # Hold the first dispatch inside its locked section until the competitor has been answered, so the
        # outcome does not depend on how long the competitor's interpreter takes to start.
        competitor_answered = threading.Event()
        self.addCleanup(competitor_answered.set)
        answer = self.peer.reply
        def reply(request):
            if request["method"] == "pane.read" and not competitor_answered.is_set():
                competitor_answered.wait(30)
            return answer(request)
        self.peer.reply = reply
        results = []
        first = threading.Thread(target=lambda: results.append(self.cli(
            self.dispatch_payload(), "--idle-seconds", "0.06", "--read-gap-seconds", "0.04",
        )))
        first.start()
        try:
            deadline = time.monotonic() + 30
            while not self.peer.requests and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.peer.requests)
            status, response = self.dispatch(dispatch_id="competing-id")
            self.assertEqual(status, 1, response)
            self.assertEqual(response["error"]["code"], "dispatch_in_progress")
        finally:
            competitor_answered.set()
            first.join(30)
        self.assertEqual(results[0][0], 0, results)
        self.assertEqual(sum(r["method"] == "pane.send_input" for r in self.peer.requests), 1)


class TricklePeer:
    """Sends one valid NDJSON reply one byte at a time, never idling long."""

    def __init__(self, directory, delay=0.02):
        self.path = str(Path(directory) / "trickle.sock")
        self.delay = delay
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while self.running:
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            with conn, conn.makefile("rb") as stream:
                request = json.loads(stream.readline())
                line = (json.dumps({"id": request["id"], "result": {"type": "pong"}})
                        + "\n").encode()
                try:
                    for byte in line:
                        conn.sendall(bytes([byte]))
                        time.sleep(self.delay)
                except OSError:
                    break  # the client cut us off at its deadline, as intended

    def close(self):
        self.running = False
        self.thread.join(2)
        self.listener.close()


class RequestDeadlineTest(unittest.TestCase):
    """Ticket 13: an active trickle cannot outlast the total request deadline."""

    def client(self, path, timeout):
        root = str(Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        import adapter

        class Options:
            socket = path
            request_timeout_seconds = timeout

        return adapter.Client(Options())

    def test_slow_trickle_is_cut_off_at_the_total_deadline(self):
        # Old code: per-read timeouts reset on every fresh byte, so one reply at
        # 50ms per byte sailed past a 0.5s request duration.
        with tempfile.TemporaryDirectory(prefix="trickle-", dir="/tmp") as directory:
            peer = TricklePeer(directory, delay=0.05)
            try:
                client = self.client(peer.path, 0.5)
                started = time.monotonic()
                with self.assertRaises(Exception) as caught:
                    client.request("ping", {})
                elapsed = time.monotonic() - started
                self.assertEqual(getattr(caught.exception, "code", None), "transport_error")
                self.assertIn("total request deadline", str(caught.exception))
                self.assertLess(elapsed, 2.0)
            finally:
                peer.close()

    def test_deadline_expiring_inside_a_read_is_reported_as_the_deadline(self):
        # Python 3.9's socket.timeout is not a TimeoutError; a stall after the
        # first byte makes the deadline land inside recv on every run.
        with tempfile.TemporaryDirectory(prefix="stall-", dir="/tmp") as directory:
            peer = TricklePeer(directory, delay=1.0)
            try:
                client = self.client(peer.path, 0.3)
                with self.assertRaises(Exception) as caught:
                    client.request("ping", {})
                self.assertEqual(getattr(caught.exception, "code", None), "transport_error")
                self.assertIn("total request deadline", str(caught.exception))
            finally:
                peer.close()

    def test_fast_pong_still_returns_within_the_deadline(self):
        with tempfile.TemporaryDirectory(prefix="fast-", dir="/tmp") as directory:
            peer = Peer(directory)
            try:
                peer.reply = lambda request: {"type": "pong"}
                client = self.client(peer.path, 5)
                self.assertEqual(client.request("ping", {}), {"type": "pong"})
            finally:
                peer.close()

    def test_expired_deadline_refuses_before_any_socket_phase(self):
        # Round 3 (R-4): the deadline spans connect, send and read; an already
        # expired deadline refuses before a socket is even opened, so no phase
        # can claim a fresh timeout allowance of its own.
        with tempfile.TemporaryDirectory(prefix="gone-", dir="/tmp") as directory:
            client = self.client(str(Path(directory) / "nothing.sock"), 0.5)
            with self.assertRaises(Exception) as caught:
                client.open_request("ping", {}, time.monotonic() - 1)
            self.assertEqual(getattr(caught.exception, "code", None), "transport_error")
            self.assertIn("total request deadline", str(caught.exception))

    def test_connect_that_consumes_the_deadline_refuses_with_the_socket_closed(self):
        # A refusal after a successful connect must not leak the created fd:
        # nothing is sent, and the socket is closed even while the raised
        # traceback still holds a reference to it. The later request proves
        # successful ownership transfer is untouched.
        with tempfile.TemporaryDirectory(prefix="spend-", dir="/tmp") as directory:
            peer = Peer(directory)
            try:
                client = self.client(peer.path, 5)
                root = str(Path(__file__).resolve().parents[1])
                if root not in sys.path:
                    sys.path.insert(0, root)
                import adapter

                real_monotonic, calls = time.monotonic, {"count": 0}

                class SpentClock:
                    def monotonic(self):
                        calls["count"] += 1
                        return real_monotonic() + (0 if calls["count"] == 1 else 60)

                deadline = real_monotonic() + 5
                adapter.time = SpentClock()
                try:
                    client.open_request("ping", {}, deadline)
                    raised = None
                except Exception as error:
                    raised = error
                finally:
                    adapter.time = time
                self.assertEqual(getattr(raised, "code", None), "transport_error")
                self.assertIn("total request deadline", str(raised))
                self.assertEqual(peer.requests, [])
                frame = raised.__traceback__
                while frame is not None and frame.tb_frame.f_code.co_name != "open_request":
                    frame = frame.tb_next
                self.assertIsNotNone(frame)
                self.assertEqual(frame.tb_frame.f_locals["connection"].fileno(), -1)
                self.assertEqual(client.request("ping", {}), {"type": "pong"})
            finally:
                peer.close()


if __name__ == "__main__":
    unittest.main()
