"""Hybrid observation: event invalidations, serialized snapshots, independent floor."""

from concurrent.futures import ThreadPoolExecutor, wait
import json
import sys
import time

from adapter import Refusal, identifier, invalid, object_fields, observe


DEFAULTS = {
    "duration_seconds": 60,
    "floor_seconds": 3,
    "ping_seconds": 30,
    "expected_event_seconds": 2,
    "silence_periods": 60,
    "retry_seconds": 1,
    "request_timeout_seconds": 2,
}


def subscriptions(picture, include_updates):
    entries = [{"type": "pane.agent_status_changed", "pane_id": identifier(p["pane_id"], "pane_id")}
               for p in picture["panes"]]
    entries += [{"type": kind} for kind in (
        "pane.created", "pane.closed", "pane.moved", "pane.exited", "pane.agent_detected",
    )]
    if include_updates:
        entries.append({"type": "pane.updated"})
    return {"subscriptions": entries}


def member_states(picture):
    return {member["pane_id"]: {key: member.get(key) for key in (
        "pane_id", "workspace_id", "terminal_id", "agent", "name", "agent_status", "state_change_seq",
    )} for member in (picture or {}).get("members", [])}


def watch(client, params):
    object_fields(params, {*DEFAULTS, "workspace_id", "include_updates"})
    for name, default in DEFAULTS.items():
        value = params.get(name, default)
        if type(value) not in (int, float) or not 0 < value <= sys.float_info.max:
            invalid(name + " must be finite and positive")
    if "workspace_id" in params:
        identifier(params["workspace_id"], "workspace_id")
    if type(params.get("include_updates", False)) is not bool:
        invalid("include_updates must be a boolean")
    config = {**DEFAULTS, **params}
    if config["retry_seconds"] < 0.05:
        invalid("retry_seconds must be at least 0.05 seconds")
    if config["expected_event_seconds"] > sys.float_info.max / config["silence_periods"]:
        invalid("computed silence threshold must be finite")
    client.timeout = config["request_timeout_seconds"]
    scope = {key: params[key] for key in ("workspace_id",) if key in params}
    return Loop(client, scope, config).run()


class Loop:
    def __init__(self, client, scope, config):
        self.client, self.scope, self.config = client, scope, config
        self.started = time.monotonic()
        self.deadline = self.started + config["duration_seconds"]
        self.stats = dict(events=0, pings=0, deaths=0, reconnects=0, subscriptions=0,
                          reconciliations=0, floor_reconciliations=0)
        self.picture = None
        self.stream = None
        self.generation = 0
        self.subscribed_panes = set()
        self.last_event = self.started
        self.last_snapshot = None
        self.snapshot_error = None
        self.next_floor = self.started + config["floor_seconds"]
        self.next_ping = self.started + config["ping_seconds"]
        self.retry_at = self.started
        self.refresh_required = True
        self.bootstrap_required = False
        self.dirty = False
        self.update_first = self.update_last = None
        self.snapshot_job = self.connect_job = self.ping_job = None

    def log(self, kind, **values):
        print(json.dumps({"kind": kind, "elapsed_seconds": time.monotonic() - self.started,
                          "generation": self.generation, **values}, allow_nan=False),
              file=sys.stderr, flush=True)

    def disconnect(self, reason, death=True):
        if self.stream:
            self.stream.close()
        self.stream = None
        self.bootstrap_required = False
        self.dirty = False
        self.update_first = self.update_last = None
        self.retry_at = time.monotonic() + (self.config["retry_seconds"] if death else 0)
        if death:
            self.stats["deaths"] += 1
        self.log("death" if death else "resubscribe", reason=reason,
                 silence_seconds=time.monotonic() - self.last_event)

    def poll_stream(self):
        if not self.stream:
            return
        try:
            for frame in self.stream.read_frames():
                kind, data = frame.get("event"), frame.get("data")
                if not isinstance(kind, str) or not isinstance(data, dict):
                    raise Refusal("transport_error", "invalid event envelope")
                pane = data.get("pane", data)
                if not isinstance(pane, dict):
                    raise Refusal("transport_error", "invalid event pane")
                workspace_id = pane.get("workspace_id")
                pane_id = pane.get("pane_id")
                # Lifecycle events may omit either id; a present id must be a string before scope membership.
                if any(value is not None and not isinstance(value, str) for value in (workspace_id, pane_id)):
                    raise Refusal("transport_error", "invalid event pane id")
                if self.scope and workspace_id != self.scope["workspace_id"] and pane_id not in self.subscribed_panes:
                    # Lifecycle envelopes may carry only ids; the floor also discovers topology.
                    continue
                now = time.monotonic()
                self.last_event = now
                self.stats["events"] += 1
                self.log("event", event=kind, pane_id=pane_id)
                if kind.replace(".", "_") in {"pane_updated", "pane_output_changed"}:
                    if self.update_first is None:
                        self.update_first = now
                    self.update_last = now
                else:
                    self.dirty = True
            silence = time.monotonic() - self.last_event
            if silence >= self.config["expected_event_seconds"] * self.config["silence_periods"]:
                self.disconnect("silence")
        except Refusal as error:
            self.disconnect(error.code)

    def finish_snapshot(self):
        if not self.snapshot_job or not self.snapshot_job[0].done():
            return
        future, reason, generation = self.snapshot_job
        self.snapshot_job = None
        try:
            picture = future.result()
        except Refusal as error:
            self.snapshot_error = error.code
            self.log("snapshot_error", reason=reason, code=error.code)
            self.retry_at = time.monotonic() + self.config["retry_seconds"]
            return
        before, after = member_states(self.picture), member_states(picture)
        changes = [{"pane_id": pane, "before": before.get(pane), "after": after.get(pane)}
                   for pane in sorted(before.keys() | after.keys()) if before.get(pane) != after.get(pane)]
        self.picture = picture
        self.last_snapshot = time.monotonic()
        self.snapshot_error = None
        self.stats["reconciliations"] += 1
        if reason == "floor":
            self.stats["floor_reconciliations"] += 1
        self.log("reconcile", reason=reason, changes=changes)
        if reason == "subscription_refresh":
            self.refresh_required = False
        if reason == "bootstrap" and generation == self.generation and self.stream:
            self.bootstrap_required = False
            # Apply the buffered *invalidation*, never its payload, to the new cache.
            self.log("bootstrap_applied", invalidated=self.dirty or self.update_first is not None)
        panes = {p["pane_id"] for p in picture["panes"]}
        if self.stream and panes != self.subscribed_panes:
            self.disconnect("topology_changed", death=False)

    def finish_connect(self):
        if not self.connect_job or not self.connect_job[0].done():
            return
        future, panes = self.connect_job
        self.connect_job = None
        try:
            self.stream = future.result()
        except Refusal as error:
            self.log("subscribe_error", code=error.code)
            self.refresh_required = True
            self.retry_at = time.monotonic() + self.config["retry_seconds"]
            return
        self.subscribed_panes = panes
        self.generation += 1
        self.stats["subscriptions"] += 1
        if self.generation > 1:
            self.stats["reconnects"] += 1
        self.last_event = time.monotonic()
        self.bootstrap_required = True
        self.log("subscribed", pane_ids=sorted(panes))

    def tick(self, snapshots, connections, pings):
        self.finish_connect()
        self.poll_stream()
        self.finish_snapshot()
        now = time.monotonic()
        if self.ping_job and self.ping_job.done():
            try:
                self.ping_job.result()
                self.stats["pings"] += 1
                self.log("ping", stream_connected=self.stream is not None)
            except Refusal as error:
                self.log("ping_error", code=error.code)
            self.ping_job = None
        if now >= self.next_ping and self.ping_job is None:
            self.ping_job = pings.submit(self.client.request, "ping", {})
            self.next_ping = now + self.config["ping_seconds"]
        if self.picture is not None and not self.refresh_required and not self.stream and not self.connect_job and now >= self.retry_at:
            sub = subscriptions(self.picture, self.config.get("include_updates", False))
            self.log("subscribe_start", pane_ids=[p["pane_id"] for p in self.picture["panes"]])
            self.connect_job = (connections.submit(self.client.subscribe, sub),
                                {p["pane_id"] for p in self.picture["panes"]})
        if self.snapshot_job is not None:
            return
        update_due = self.update_first is not None and (
            now - self.update_last >= 0.2 or now - self.update_first >= 1)
        reason = None
        if self.refresh_required and now >= self.retry_at:
            reason = "subscription_refresh"
        elif self.bootstrap_required and now >= self.retry_at:
            reason = "bootstrap"
        elif now >= self.next_floor:
            reason = "floor"
            self.next_floor = now + self.config["floor_seconds"]
        elif (self.dirty or update_due) and now >= self.retry_at:
            reason = "event"
        if reason:
            if reason != "bootstrap":
                self.dirty = False
                self.update_first = self.update_last = None
            self.log("snapshot_start", reason=reason)
            self.snapshot_job = (snapshots.submit(observe, self.client, self.scope), reason, self.generation)

    def drain_snapshot(self):
        """Bound the shutdown drain (ticket 13): a stalled snapshot is abandoned."""
        if not self.snapshot_job:
            return
        grace = 2 * self.config["request_timeout_seconds"] + 1
        wait([self.snapshot_job[0]], timeout=grace)
        if self.snapshot_job[0].done():
            self.finish_snapshot()
        else:
            self.snapshot_job[0].cancel()
            self.log("snapshot_abandoned", reason="drain exceeded the shutdown grace bound")
            self.snapshot_job = None

    def run(self):
        # Separate workers keep a stalled ACK or ping from blocking the floor or watchdog.
        # Round 3 (R-1): executor context exit joins a running worker, which would
        # quietly undo the bounded abandonment above; shutdown is explicit and
        # never waits for a worker the drain already gave up on.
        snapshots = ThreadPoolExecutor(1)
        connections = ThreadPoolExecutor(1)
        pings = ThreadPoolExecutor(1)
        try:
            while time.monotonic() < self.deadline:
                self.tick(snapshots, connections, pings)
                time.sleep(min(0.01, max(0, self.deadline - time.monotonic())))
            # Drain the single flight before flushing invalidations; an older
            # floor response must never overwrite the final picture.
            if self.snapshot_job:
                self.drain_snapshot()
            self.poll_stream()
            if self.dirty or self.update_first is not None or self.bootstrap_required:
                self.dirty = False
                self.update_first = self.update_last = None
                self.log("snapshot_start", reason="exit")
                self.snapshot_job = (
                    snapshots.submit(observe, self.client, self.scope), "exit", self.generation,
                )
                self.drain_snapshot()
        finally:
            for pool in (snapshots, connections, pings):
                pool.shutdown(wait=False, cancel_futures=True)
            if self.stream:
                self.stream.close()
            if self.connect_job:
                # Round 3 (R-1): bound the final connect fetch like the snapshot
                # drain; a stalled connect worker cannot hold shutdown open.
                wait([self.connect_job[0]], timeout=self.config["request_timeout_seconds"])
                if self.connect_job[0].done():
                    try:
                        self.connect_job[0].result().close()
                    except Refusal:
                        pass
        if self.picture is None:
            raise Refusal("observation_unavailable", "no authoritative snapshot received")
        result = {
            "picture": self.picture, "stats": self.stats,
            "snapshot_age_seconds": time.monotonic() - self.last_snapshot,
            "snapshot_error": self.snapshot_error,
            "silence_seconds": self.config["expected_event_seconds"] * self.config["silence_periods"],
        }
        self.log("stopped", stats=self.stats)
        return result
