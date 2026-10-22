"""Incrementally parse Codex JSONL rollout records and expose snapshots."""

from collections import deque
import json

from ..usage import TOKEN_KEYS, timestamp, usage

MAX_LINE = 8 * 1024 * 1024


class Rollout:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.identity = None
        self.id = None
        self.parent = None
        self.model = "Unknown"
        self.provider = "Unknown"
        self.cwd = ""
        self.name = ""
        self.source = "session"
        self.created = None
        self.last_seen = None
        self.turn_started = None
        self.turn_ended = None
        self.turn_id = None
        self.state = "unknown"
        self.state_reason = "No lifecycle event recorded"
        self.context_window = None
        self.tokens = None
        self.last_request = None
        self.modern_usage = False
        self.history_start = None
        self.activity = deque(maxlen=24)
        self.skipped = 0

    def note(self, at, text, kind="event"):
        if at is not None:
            self.last_seen = max(self.last_seen or at, at)
        self.activity.append({"at": at, "text": text[:160], "kind": kind})

    def consume(self, item):
        kind = item.get("type")
        p = item.get("payload")
        if not isinstance(p, dict):
            return
        at = timestamp(item.get("timestamp"))
        if kind == "session_meta":
            # Do not overwrite the current thread with inherited parent metadata.
            if self.id is not None:
                return
            self.id = p.get("id") or p.get("session_id")
            self.cwd = str(p.get("cwd") or "")
            self.provider = str(p.get("model_provider") or "Unknown")
            self.parent = p.get("parent_thread_id")
            self.history_start = p.get("subagent_history_start_ordinal")
            self.created = timestamp(p.get("timestamp")) or at
            self.context_window = p.get("context_window")
            source = p.get("source", "session")
            if isinstance(source, dict):
                self.source = "subagent"
                sub = source.get("subagent", {})
                spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
                if isinstance(spawn, dict):
                    self.parent = self.parent or spawn.get("parent_thread_id")
                    self.name = str(spawn.get("agent_nickname") or spawn.get("agent_role") or "")
            else:
                self.source = str(source)
            return
        ordinal = item.get("ordinal")
        if isinstance(self.history_start, int) and isinstance(ordinal, int) and ordinal < self.history_start:
            return
        if kind == "turn_context":
            self.model = str(p.get("model") or self.model)
            return
        if kind == "token_usage_record":
            # Attribution is mandatory here: copied parent accounting is not child usage.
            if p.get("thread_id") != self.id:
                return
            recorded = usage(p.get("thread_token_usage"))
            if recorded is not None:
                self.tokens = recorded
                self.last_request = usage(p.get("usage"))
                self.modern_usage = True
                self.note(at, "Token usage updated", "usage")
            return
        if kind == "response_item":
            response_type = p.get("type")
            if response_type in ("function_call", "custom_tool_call"):
                self.note(at, "Tool · " + str(p.get("name") or "unnamed"), "tool")
            elif response_type in ("function_call_output", "custom_tool_call_output"):
                self.note(at, "Tool result received", "tool")
            elif response_type == "message" and p.get("role") == "assistant":
                self.note(at, "Assistant response", "message")
            return
        if kind != "event_msg":
            return
        event = p.get("type")
        if event == "token_count":
            info = p.get("info") or {}
            if isinstance(info, dict):
                self.context_window = info.get("model_context_window") or self.context_window
                if not self.modern_usage:
                    recorded = usage(info.get("total_token_usage"))
                    if recorded is not None:
                        self.tokens = recorded
                        self.last_request = usage(info.get("last_token_usage"))
                        self.note(at, "Token usage updated", "usage")
            return
        if event == "task_started":
            self.turn_id = p.get("turn_id")
            self.turn_started = timestamp(p.get("started_at")) or at
            self.turn_ended = None
            self.context_window = p.get("model_context_window") or self.context_window
            self.state, self.state_reason = "running", "A turn started; process liveness is inferred from recent log activity"
            self.note(at, "Turn started", "lifecycle")
        elif event in ("task_complete", "task_completed", "turn_aborted", "task_failed"):
            if p.get("turn_id") and self.turn_id and p["turn_id"] != self.turn_id:
                return
            self.turn_ended = timestamp(p.get("completed_at")) or at
            self.state = {"task_complete": "idle", "task_completed": "idle", "turn_aborted": "interrupted", "task_failed": "failed"}[event]
            self.state_reason = "Last recorded turn " + {"idle": "completed", "interrupted": "was interrupted", "failed": "failed"}[self.state]
            self.note(at, self.state_reason, "lifecycle")
        elif event in ("approval_requested", "user_input_requested"):
            self.state, self.state_reason = "waiting", "An explicit waiting event was recorded"
            self.note(at, "Waiting for input or approval", "lifecycle")
        elif event in ("approval_resolved", "user_input_received"):
            self.state, self.state_reason = "running", "Recorded wait resolved; turn remains open"
            self.note(at, "Input received", "lifecycle")

    def update(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity is not None and (self.identity != identity or stat.st_size < self.offset):
            self.__init__(self.path)
        self.identity = identity
        if stat.st_size == self.offset:
            return
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            while True:
                start = stream.tell()
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if len(line) <= MAX_LINE:
                        # The producer may still be writing this JSON record.
                        stream.seek(start)
                        break
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_LINE + 1)
                    if not line:
                        stream.seek(start)
                        break
                    self.skipped += 1
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        self.consume(item)
                except (ValueError, UnicodeError):
                    self.skipped += 1
            self.offset = stream.tell()

    def public(self, now, stale_after, metadata=None):
        meta = metadata or {}
        state, reason = self.state, self.state_reason
        if state in ("running", "waiting") and (self.last_seen is None or now - self.last_seen > stale_after):
            state, reason = "unknown", "Open turn with no recent log activity; it may still be running or waiting"
        parent = self.parent or meta.get("parent_id")
        name = meta.get("name") or (meta.get("agent_path") if parent else meta.get("title")) or meta.get("agent_nickname") or self.name or meta.get("title")
        return {
            "id": self.id, "parent_id": parent,
            "name": str(name or "Untitled session")[:180], "cwd": self.cwd,
            "model": self.model if self.model != "Unknown" else meta.get("model") or "Unknown",
            "provider": self.provider, "source": self.source,
            "status": state, "status_reason": reason, "status_source": "log observation",
            "created_at": self.created, "last_seen": self.last_seen,
            "turn_started_at": self.turn_started, "turn_ended_at": self.turn_ended,
            "tokens": self.tokens, "last_request": self.last_request,
            "context_window": self.context_window, "activity": list(reversed(self.activity)),
            "skipped_records": self.skipped,
        }
