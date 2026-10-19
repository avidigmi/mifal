#!/usr/bin/env python3
"""Fail-closed, local herdr control. One JSON command in, one JSON result out."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import sys
import time
import uuid


class Refusal(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def invalid(message):
    raise Refusal("schema_invalid", message)


def json_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                invalid("duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        invalid("non-finite JSON number: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


class Schema:
    """The subset used by the bundled schema, with closed objects by default.

    herdr accepts unknown fields and silently targets focus. Unlike its schema,
    omitted additionalProperties is therefore deliberately treated as false.
    """

    def __init__(self):
        self.document = json_loads(Path(__file__).with_name("api-schema.json").read_text())
        try:
            self.methods = {
                branch["properties"]["method"]["const"]: branch["properties"]["params"]
                for branch in self.document["schemas"]["request"]["oneOf"]
            }
        except (KeyError, TypeError) as error:
            raise Refusal("schema_corrupt", "invalid bundled request schema") from error

    def params(self, method, params):
        if not isinstance(method, str) or method not in self.methods:
            invalid("unknown method")
        if not isinstance(params, dict):
            invalid("params must be an object")
        try:
            self.check(params, self.methods[method], "params")
        except (KeyError, TypeError, ValueError, re.error, RecursionError) as error:
            raise Refusal("schema_corrupt", "invalid bundled parameter schema") from error

    def check(self, value, schema, path):
        if not isinstance(schema, dict):
            raise Refusal("schema_corrupt", "schema node is not an object at " + path)
        supported = {
            "$ref", "type", "properties", "required", "additionalProperties",
            "propertyNames", "maxProperties", "items", "anyOf", "oneOf", "const",
            "enum", "minimum", "maximum", "format", "pattern", "default",
            "description", "title", "$schema", "$defs",
        }
        if set(schema) - supported:
            raise Refusal("schema_unsupported", "unsupported schema keyword at " + path)
        if "$ref" in schema:
            reference = schema["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise Refusal("schema_corrupt", "expected a local schema reference at " + path)
            target = self.document
            try:
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as error:
                raise Refusal("schema_corrupt", "unresolvable schema reference at " + path) from error
            self.check(value, target, path)
        for kind in ("anyOf", "oneOf"):
            if kind in schema:
                count = 0
                for candidate in schema[kind]:
                    try:
                        self.check(value, candidate, path)
                        count += 1
                    except Refusal as error:
                        if error.code != "schema_invalid":
                            raise
                if not count or (kind == "oneOf" and count != 1):
                    invalid(path + " does not match " + kind)
        types = schema.get("type", [])
        if isinstance(types, str):
            types = [types]
        matches = {
            "null": value is None,
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) is int or (type(value) is float and math.isfinite(value)),
            "string": isinstance(value, str),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }
        if types and not any(matches.get(kind, False) for kind in types):
            invalid(path + " has the wrong type")
        if "const" in schema and value != schema["const"]:
            invalid(path + " has the wrong constant")
        if "enum" in schema and value not in schema["enum"]:
            invalid(path + " is not an allowed value")
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    invalid(path + " is missing " + key)
            if "maxProperties" in schema and len(value) > schema["maxProperties"]:
                invalid(path + " has too many properties")
            # A $ref-only node leaves object validation to its resolved schema.
            if "type" in schema or "properties" in schema:
                for key, item in value.items():
                    if "propertyNames" in schema:
                        self.check(key, schema["propertyNames"], path + "." + key)
                    child = properties.get(key, schema.get("additionalProperties", False))
                    if child is False:
                        invalid(path + " has unknown field " + key)
                    if child is not True:
                        self.check(item, child, path + "." + key)
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                self.check(item, schema["items"], f"{path}[{i}]")
        if type(value) in (int, float):
            if type(value) is float and not math.isfinite(value):
                invalid(path + " must be finite")
            if "minimum" in schema and value < schema["minimum"]:
                invalid(path + " is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                invalid(path + " is above maximum")
            fmt = schema.get("format")
            if fmt and fmt.startswith("uint"):
                bits = int(fmt[4:] or "64")
                if type(value) is not int or not 0 <= value < 2 ** bits:
                    invalid(path + " is outside " + fmt)
        if isinstance(value, str) and "pattern" in schema:
            if not re.search(schema["pattern"], value):
                invalid(path + " does not match pattern")


def object_fields(value, allowed, required=()):
    if not isinstance(value, dict):
        invalid("expected an object")
    if set(value) - set(allowed):
        invalid("unknown fields: " + ", ".join(sorted(set(value) - set(allowed))))
    for key in required:
        if key not in value:
            invalid("missing " + key)


def identifier(value, label):
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise Refusal("missing_target", label + " must be explicitly provided")
    return value


class Client:
    def __init__(self, options):
        self.path = options.socket
        self.schema = Schema()
        self.timeout = getattr(options, "request_timeout_seconds", 15)

    def open_request(self, method, params, deadline):
        self.schema.params(method, params)
        if method not in {"session.snapshot", "pane.read", "pane.send_input", "ping", "events.subscribe"}:
            raise Refusal("method_forbidden", "method is outside the core adapter")
        if method.startswith("pane."):
            identifier(params.get("pane_id"), "pane_id")
        if method == "events.subscribe":
            for entry in params["subscriptions"]:
                if entry["type"] in {"pane.agent_status_changed", "pane.scroll_changed"}:
                    identifier(entry.get("pane_id"), "pane_id")
        request_id = str(uuid.uuid4())
        packet = (json.dumps({
            "id": request_id, "method": method, "params": params,
        }, allow_nan=False) + "\n").encode()
        connection = socket.socket(socket.AF_UNIX)
        try:
            # Round 3 (R-4): the deadline spans connect, send and read; each phase
            # only gets the time the whole request has left, never a fresh allowance.
            # The refusal paths close here: the caller never receives the
            # connection, so the fd must not wait for garbage collection.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                connection.close()
                raise Refusal("transport_error",
                              "request exceeded the total request deadline") from None
            connection.settimeout(remaining)
            connection.connect(self.path)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                connection.close()
                raise Refusal("transport_error",
                              "request exceeded the total request deadline") from None
            connection.settimeout(remaining)
            connection.sendall(packet)
            return connection, request_id
        except OSError as error:
            connection.close()
            raise Refusal("transport_error", str(error)) from error

    def read_response_line(self, connection, deadline):
        """Read one NDJSON line under the request's total deadline (ticket 13).

        A per-read socket timeout alone lets a trickling peer stretch one request
        past its configured duration: every recv sees fresh bytes, so no single
        read ever blocks long enough to time out. The remaining-time deadline
        below cuts the read off at the configured request duration instead. The
        deadline is owned by request()/subscribe() so connect and sending share
        the same bound.
        """
        buffer = bytearray()
        while b"\n" not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refusal("transport_error", "response exceeded the total request deadline")
            connection.settimeout(remaining)
            try:
                chunk = connection.recv(65536)
            except TimeoutError:
                # remaining was the deadline, so this timeout is the total deadline.
                raise Refusal("transport_error",
                              "response exceeded the total request deadline") from None
            if not chunk:
                raise Refusal("transport_error", "connection closed before a full response")
            buffer += chunk
            if len(buffer) > 16 * 1024 * 1024:
                raise Refusal("transport_error", "missing or oversized NDJSON response")
        return bytes(buffer[:buffer.index(b"\n") + 1])

    def request(self, method, params):
        if method == "events.subscribe":
            raise Refusal("method_forbidden", "use the read-only subscription interface")
        try:
            # The socket is never cached, pooled, pipelined, or shared with a subscriber.
            deadline = time.monotonic() + self.timeout
            connection, request_id = self.open_request(method, params, deadline)
            with connection:
                line = self.read_response_line(connection, deadline)
            if not line.endswith(b"\n") or len(line) > 16 * 1024 * 1024:
                raise Refusal("transport_error", "missing or oversized NDJSON response")
            response = json_loads(line)
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise Refusal("transport_error", "response id mismatch")
            if "error" in response:
                raise Refusal("server_error", "herdr refused the request", server_error=response["error"])
            if not isinstance(response.get("result"), dict):
                raise Refusal("transport_error", "response has no result object")
            return response["result"]
        except (OSError, ValueError) as error:
            raise Refusal("transport_error", str(error)) from error

    def subscribe(self, params):
        # Round 3 (R-4): the ACK deadline spans connect, send and the wait for the
        # subscription ACK, matching request()'s whole-request bound.
        deadline = time.monotonic() + self.timeout
        connection, request_id = self.open_request("events.subscribe", params, deadline)
        stream = Subscription(connection, request_id)
        try:
            while time.monotonic() < deadline:
                frames = stream.read_frames(max_frames=1)
                if not frames:
                    select.select([connection], [], [], min(0.02, self.timeout))
                    continue
                ack = frames[0]
                if ack.get("id") != request_id:
                    raise Refusal("transport_error", "subscription ACK id mismatch")
                result = ack.get("result")
                if not isinstance(result, dict) or result.get("type") != "subscription_started":
                    raise Refusal("transport_error", "missing subscription_started ACK")
                return stream
            raise Refusal("transport_error", "subscription ACK timed out")
        except BaseException:
            stream.close()
            raise

    def snapshot(self):
        result = self.request("session.snapshot", {})
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("protocol") != 22:
            raise Refusal("unsupported_protocol", "expected a protocol-22 snapshot")
        for key in ("agents", "panes", "workspaces", "tabs"):
            if not isinstance(snapshot.get(key), list):
                raise Refusal("invalid_snapshot", "snapshot missing " + key)
        return snapshot


class Subscription:
    """A bounded NDJSON reader with no request/write operation after the ACK."""

    def __init__(self, connection, request_id):
        self.connection = connection
        self.request_id = request_id
        self.buffer = b""
        connection.setblocking(False)

    def read_frames(self, max_frames=256):
        try:
            if b"\n" not in self.buffer and select.select([self.connection], [], [], 0)[0]:
                chunk = self.connection.recv(65536)
                if not chunk:
                    raise Refusal("stream_closed", "subscription connection closed")
                self.buffer += chunk
            frames = []
            while b"\n" in self.buffer and len(frames) < max_frames:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if len(line) > 16 * 1024 * 1024:
                    raise Refusal("transport_error", "oversized event frame")
                frame = json_loads(line)
                if not isinstance(frame, dict):
                    raise Refusal("transport_error", "event frame must be an object")
                if "error" in frame:
                    error = frame["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    raise Refusal("events_lost" if code == "events_lost" else "server_error",
                                  "subscription refused or lost", server_error=error)
                frames.append(frame)
            if len(self.buffer) > 16 * 1024 * 1024:
                raise Refusal("transport_error", "oversized event frame")
            return frames
        except (OSError, ValueError) as error:
            raise Refusal("transport_error", str(error)) from error

    def close(self):
        self.connection.close()


def observe(client, params):
    object_fields(params, {"workspace_id"})
    workspace_id = params.get("workspace_id")
    if "workspace_id" in params:
        identifier(workspace_id, "workspace_id")
    snapshot = client.snapshot()
    result = {key: snapshot.get(key) for key in (
        "version", "protocol", "focused_pane_id", "focused_tab_id", "focused_workspace_id",
    )}
    for source, destination in (("agents", "members"), ("panes", "panes"),
                                ("workspaces", "workspaces"), ("tabs", "tabs")):
        result[destination] = [
            item for item in snapshot[source]
            if workspace_id is None or item.get("workspace_id") == workspace_id
        ]
    return result


def target(snapshot, pane_id, workspace_id):
    panes = [pane for pane in snapshot["panes"] if pane.get("pane_id") == pane_id]
    if len(panes) != 1 or panes[0].get("workspace_id") != workspace_id:
        raise Refusal("target_mismatch", "pane is absent or outside the explicit workspace")
    return panes[0]


def idle_identity(client, pane_id, workspace_id, box):
    snapshot = client.snapshot()
    pane = target(snapshot, pane_id, workspace_id)
    agents = [agent for agent in snapshot["agents"] if agent.get("pane_id") == pane_id]
    if len(agents) != 1:
        raise Refusal("guard_refused", "no uniquely identified agent in target pane")
    agent = agents[0]
    for key in ("terminal_id", "workspace_id", "tab_id", "cwd", "agent"):
        if pane.get(key) != agent.get(key) or not isinstance(agent.get(key), str):
            raise Refusal("guard_refused", "incomplete or inconsistent identity: " + key)
    # Protocol 22: done is idle-but-unseen. Focusing to clear it would steal focus.
    if pane.get("agent_status") not in {"idle", "done"} or agent.get("agent_status") not in {"idle", "done"}:
        raise Refusal("guard_refused", "agent is not idle")
    if agent.get("agent") != box["agent"]:
        raise Refusal("guard_refused", "input-box profile does not match agent")
    if agent.get("screen_detection_skipped") or agent.get("launch_pending"):
        raise Refusal("guard_refused", "agent readiness is not observable")
    if type(agent.get("state_change_seq")) is not int:
        raise Refusal("guard_refused", "idle transition sequence is unreadable")
    if (pane.get("scroll") or {}).get("offset_from_bottom") != 0:
        raise Refusal("guard_refused", "pane is not verifiably at the bottom")
    return {key: agent.get(key) for key in (
        "pane_id", "workspace_id", "tab_id", "terminal_id", "cwd", "agent", "name",
        "state_change_seq",
    )}


def empty_box(client, pane_id, box):
    # visible is passive. Recent/history reads may page an alternate-screen agent.
    result = client.request("pane.read", {
        "pane_id": pane_id, "source": "visible", "format": "text", "strip_ansi": True,
    })
    read = result.get("read", {})
    text = read.get("text")
    if read.get("pane_id") != pane_id or read.get("truncated") is not False or not isinstance(text, str):
        raise Refusal("guard_refused", "input box is unreadable")
    lines = [line.rstrip(" ") for line in text.splitlines()]
    starts = [i for i, line in enumerate(lines) if line == box["start"]]
    ends = [i for i, line in enumerate(lines) if line == box["end"]]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise Refusal("guard_refused", "input-box frame is missing or ambiguous")
    if any(line.strip() for line in lines[starts[0] + 1:ends[0]]):
        raise Refusal("guard_refused", "input box holds a draft")
    if any(line.strip() for line in lines[ends[0] + 1:]):
        raise Refusal("guard_refused", "input-box frame is not at the bottom")


def draft_guard(client, params, options):
    pane_id, workspace_id, box = params["pane_id"], params["workspace_id"], params["input_box"]
    identity = idle_identity(client, pane_id, workspace_id, box)
    started = time.monotonic()
    empty_box(client, pane_id, box)
    first_read = time.monotonic()
    deadline = max(started + options.idle_seconds, first_read + options.read_gap_seconds)
    while time.monotonic() < deadline:
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        if idle_identity(client, pane_id, workspace_id, box) != identity:
            raise Refusal("guard_refused", "identity or idle transition changed during guard")
    second_read = time.monotonic()
    empty_box(client, pane_id, box)
    if idle_identity(client, pane_id, workspace_id, box) != identity:
        raise Refusal("guard_refused", "identity or idle transition changed during guard")
    return {
        "identity": identity,
        "idle_seconds_observed": time.monotonic() - started,
        "empty_read_gap_seconds": second_read - first_read,
        "idle_seconds_required": options.idle_seconds,
        "empty_read_gap_seconds_required": options.read_gap_seconds,
    }


def durable_write(path, text):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def dispatch(client, params, options):
    object_fields(params, {
        "pane_id", "workspace_id", "dispatch_id", "text", "brief_file", "input_box",
    })
    identifier(params.get("pane_id"), "pane_id")
    identifier(params.get("workspace_id"), "workspace_id")
    dispatch_id = params.get("dispatch_id")
    if not isinstance(dispatch_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dispatch_id):
        invalid("dispatch_id must be a stable, path-safe id (1–128 characters)")
    box = params.get("input_box")
    object_fields(box, {"agent", "start", "end"}, {"agent", "start", "end"})
    for key, value in box.items():
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            invalid("input_box." + key + " must be a nonempty literal line")
    if box["start"] == box["end"]:
        invalid("input-box frame delimiters must be distinct")
    if ("text" in params) == ("brief_file" in params):
        invalid("provide exactly one of text or brief_file")
    if "brief_file" in params:
        identifier(params["brief_file"], "brief_file")
        text = Path(params["brief_file"]).read_text(encoding="utf-8")
    else:
        text = params["text"]
    if not isinstance(text, str) or not text.strip():
        invalid("brief must be nonempty text")
    if any((ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) < 160 for c in text):
        invalid("brief contains terminal control characters")
    send_params = {"pane_id": params["pane_id"], "text": text, "keys": ["enter"]}
    client.schema.params("pane.send_input", send_params)
    state_dir = Path(options.state_dir).expanduser().resolve()
    record_dir = state_dir / "dispatches" / dispatch_id
    record_path = record_dir / "record.json"
    if record_dir.exists():
        raise Refusal("duplicate_dispatch", "id already reserved; reconcile receipts, do not retry",
                      record_path=str(record_path))
    locks = state_dir / "locks"
    locks.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_key = hashlib.sha256((os.path.realpath(client.path) + "\0" + params["pane_id"]).encode()).hexdigest()
    with (locks / lock_key).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Refusal("dispatch_in_progress", "another dispatch holds this pane") from error
        # Also covers a pane spawned outside this process; no caller timestamp is trusted.
        time.sleep(options.shell_ready_ms / 1000)
        guard = draft_guard(client, params, options)
        record_dir.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            record_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            raise Refusal("duplicate_dispatch", "id already reserved; reconcile receipts",
                          record_path=str(record_path)) from error
        brief_path = record_dir / "brief.txt"
        durable_write(brief_path, text)
        record = {
            "dispatch_id": dispatch_id, "status": "prepared", "delivery": "unverified",
            "pane_id": params["pane_id"], "workspace_id": params["workspace_id"],
            "brief_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "brief_path": str(brief_path), "record_path": str(record_path),
            "prepared_at": datetime.now(timezone.utc).isoformat(), "guard": guard,
        }
        durable_write(record_path, json.dumps(record, indent=2) + "\n")
        try:
            client.request("pane.send_input", send_params)
            record["status"] = "sent"
            record["sent_at"] = datetime.now(timezone.utc).isoformat()
            durable_write(record_path, json.dumps(record, indent=2) + "\n")
        except (Refusal, OSError) as error:
            refused = isinstance(error, Refusal) and error.code == "server_error"
            record["status"] = "refused" if refused else "uncertain"
            record["error"] = str(error)
            if refused:
                record["server_error"] = error.details["server_error"]
            durable_write(record_path, json.dumps(record, indent=2) + "\n")
            if refused:
                raise Refusal("dispatch_refused", "herdr refused submission; dispatch id remains reserved",
                              record_path=str(record_path)) from error
            raise Refusal("dispatch_uncertain", "do not retry; reconcile durable member receipts",
                          record_path=str(record_path)) from error
        return record


def handle(payload, options):
    object_fields(payload, {"command", "params"}, {"command", "params"})
    if payload["command"] == "validate":
        params = payload["params"]
        object_fields(params, {"method", "params"}, {"method", "params"})
        Schema().params(params["method"], params["params"])
        return {"valid": True, "sent": False}
    if payload["command"] == "observe":
        return observe(Client(options), payload["params"])
    if payload["command"] == "dispatch":
        return dispatch(Client(options), payload["params"], options)
    if payload["command"] == "watch":
        from event_loop import watch
        return watch(Client(options), payload["params"])
    raise Refusal("unknown_command", "expected validate, observe, dispatch, or watch")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("invalid_options", message)


def main():
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--socket", default=os.environ.get(
            "HERDR_SOCKET_PATH", os.path.expanduser("~/.config/herdr/herdr.sock")))
        parser.add_argument("--state-dir", default=os.path.expanduser(
            "~/.local/state/herdr-adapter"))
        parser.add_argument("--idle-seconds", type=float, default=60,
                            help="minimum continuously observed idle time (default: 60)")
        parser.add_argument("--read-gap-seconds", type=float, default=10,
                            help="minimum gap between empty input reads (default: 10)")
        parser.add_argument("--shell-ready-ms", type=float, default=500,
                            help="shell-ready delay, 500–2500 ms (default: 500)")
        options = parser.parse_args()
        for name in ("idle_seconds", "read_gap_seconds"):
            if not math.isfinite(getattr(options, name)) or getattr(options, name) <= 0:
                raise Refusal("invalid_options", name + " must be finite and positive")
        if not 500 <= options.shell_ready_ms <= 2500:
            raise Refusal("invalid_options", "shell-ready-ms must be within 500–2500")
        result = handle(json_loads(sys.stdin.read()), options)
        print(json.dumps({"ok": True, "result": result}, allow_nan=False))
        return 0
    except (Refusal, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": {
            "code": getattr(error, "code", "invalid_input"),
            "message": str(error), **getattr(error, "details", {}),
        }}))
        return 1


if __name__ == "__main__":
    # Keep exception types identical when the sibling loop imports this module.
    from adapter import main
    sys.exit(main())
