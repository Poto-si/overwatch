#!/usr/bin/env python3
"""Read-only local Codex observer. Python standard library; no model/API calls."""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent
MAX_LINE = 8 * 1024 * 1024
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def usage(value):
    if not isinstance(value, dict):
        return None
    result = {key: max(0, value.get(key, 0)) if type(value.get(key, 0)) is int else 0 for key in TOKEN_KEYS}
    # Cached input and reasoning output are subsets, not additional tokens.
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


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


def read_metadata(home):
    """Optional labels only. Immutable mode cannot create DB/WAL/SHM files.

    Uncheckpointed WAL metadata may be absent; JSONL remains the observation source.
    """
    candidates = sorted(home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {}, None
    try:
        with closing(sqlite3.connect(candidates[0].as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            allowed = ("id", "title", "name", "model", "agent_path", "agent_nickname")
            columns = {r[1] for r in conn.execute("pragma table_info(threads)")}
            fields = [field for field in allowed if field in columns]
            if "id" not in fields:
                return {}, "Thread labels unavailable for this database format"
            rows = conn.execute("select " + ",".join(fields) + " from threads").fetchall()
            result = {row["id"]: dict(row) for row in rows}
            tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
            if "thread_spawn_edges" in tables:
                for parent, child in conn.execute("select parent_thread_id, child_thread_id from thread_spawn_edges"):
                    result.setdefault(child, {})["parent_id"] = parent
            return result, None
    except (sqlite3.Error, OSError):
        return {}, "Optional thread labels unavailable; using rollout metadata"


def demo_agents(now):
    specs = [
        ("demo-root", None, "Machinery · implementation", "running", "gpt-6.1-sol", 184200, 132000, 18200, 540),
        ("demo-data", "demo-root", "Base · validate revision history", "running", "gpt-6.1-sol", 62500, 41000, 8200, 220),
        ("demo-ecu", "demo-root", "ECU · review tool boundaries", "waiting", "gpt-6.1-sol", 41000, 28000, 5100, 300),
        ("demo-ui", "demo-root", "Manifold · inspect event recovery", "idle", "gpt-6.1-sol", 80200, 67000, 9200, 680),
        ("demo-other", None, "Research · source capture contracts", "interrupted", "gpt-6-sol", 28500, 11000, 4800, 1800),
    ]
    result = []
    for ident, parent, name, status, model, incoming, cached, outgoing, elapsed in specs:
        result.append({"id": ident, "parent_id": parent, "name": name, "cwd": "/example/potosi/intake",
                       "model": model, "provider": "openai", "source": "subagent" if parent else "demo",
                       "status": status, "status_reason": "Sample data — no real agent is connected", "status_source": "demo",
                       "created_at": now - elapsed, "last_seen": now - (2 if status == "running" else 80),
                       "turn_started_at": now - elapsed, "turn_ended_at": None if status in ("running", "waiting") else now - 80,
                       "tokens": usage({"input_tokens": incoming, "cached_input_tokens": cached, "output_tokens": outgoing}),
                       "last_request": usage({"input_tokens": 32000, "cached_input_tokens": 18000, "output_tokens": 1200}),
                       "context_window": 200000, "activity": [{"at": now - 2, "text": "Sample activity", "kind": "tool"}],
                       "skipped_records": 0})
    return result


class Observer:
    def __init__(self, home, limit=120, stale_after=120, demo=False, interval=2):
        self.home, self.limit, self.stale_after, self.demo = home, limit, stale_after, demo
        self.interval = interval
        self.rollouts = {}
        self.lock = threading.Lock()
        self.encoded = b'{"agents":[],"loading":true,"warnings":[]}'

    def refresh(self):
        now, warnings = time.time(), []
        if self.demo:
            agents = demo_agents(now)
            discovered = len(agents)
        else:
            files = []
            directory = self.home / "sessions"
            try:
                files = sorted(directory.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not directory.is_dir():
                    warnings.append("No sessions directory found. Set --codex-home or use --demo.")
            except OSError:
                warnings.append("Some session files could not be discovered")
            discovered = len(files)
            chosen = set(files[:self.limit])
            self.rollouts = {p: r for p, r in self.rollouts.items() if p in chosen}
            metadata, warning = read_metadata(self.home)
            if warning:
                warnings.append(warning)
            agents = []
            for path in files[:self.limit]:
                record = self.rollouts.setdefault(path, Rollout(path))
                try:
                    record.update()
                    if record.id:
                        agents.append(record.public(now, self.stale_after, metadata.get(record.id)))
                except OSError:
                    warnings.append("A session became unavailable during observation")
            if any(a["skipped_records"] for a in agents):
                warnings.append("Some malformed or oversized log records were skipped; observations may be incomplete")
        # Deduplicate thread IDs when a runtime retains multiple rollout files.
        unique = {}
        for agent in agents:
            previous = unique.get(agent["id"])
            if previous is None or (agent["last_seen"] or 0) > (previous["last_seen"] or 0):
                unique[agent["id"]] = agent
        payload = {"agents": sorted(unique.values(), key=lambda a: a["last_seen"] or 0, reverse=True),
                   "generated_at": now, "loading": False, "mode": "demo" if self.demo else "logs",
                   "source": "Sample data" if self.demo else str(self.home / "sessions"),
                   "discovered": discovered, "limit": self.limit, "stale_after": self.stale_after,
                   "refresh_interval": self.interval,
                   "warnings": list(dict.fromkeys(warnings)),
                   "note": "Read-only observations, not authoritative process status. Cached input and reasoning output are subsets. Token counts are not billing or subscription quota."}
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        with self.lock:
            self.encoded = encoded

    def snapshot(self):
        with self.lock:
            return self.encoded


def make_handler(observer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def respond(self, head):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin != "http://" + host):
                self.send_error(403, "Local same-origin access only")
                return
            path = urlsplit(self.path).path
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/index.html": ("index.html", "text/html; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
                      "/theme.css": ("theme.css", "text/css; charset=utf-8"),
                      "/assets/inter-tight-800.ttf": ("assets/inter-tight-800.ttf", "font/ttf")}
            if path == "/api/snapshot":
                data, content_type = observer.snapshot(), "application/json; charset=utf-8"
            elif path in assets:
                filename, content_type = assets[path]
                data = (ASSETS / filename).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def log_message(self, format, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
    parser.add_argument("--limit", type=int, default=120, help="Most recently modified rollout files to observe")
    parser.add_argument("--stale-after", type=int, default=120, help="Open turns become unknown after this many seconds without activity")
    parser.add_argument("--interval", type=float, default=2, help="Log refresh interval in seconds")
    parser.add_argument("--demo", action="store_true", help="Show clearly labelled sample data; do not read Codex state")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.limit < 1 or args.stale_after < 1 or args.interval < 0.5:
        parser.error("Choose a valid port, positive limit/stale threshold and interval of at least 0.5 seconds")
    observer = Observer(args.codex_home.expanduser().resolve(), args.limit, args.stale_after, args.demo, args.interval)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(observer))
    stop = threading.Event()

    def collect():
        while not stop.is_set():
            try:
                observer.refresh()
            except Exception as exc:
                # Retain the last good view without exposing raw exceptions or prompts.
                print(f"Observation failed ({type(exc).__name__}); retaining last snapshot", flush=True)
            stop.wait(args.interval)

    worker = threading.Thread(target=collect, daemon=True)
    worker.start()
    print(f"Poto.si | Overwatch: http://127.0.0.1:{args.port}", flush=True)
    print("Sample data" if args.demo else f"Read-only logs: {observer.home / 'sessions'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
