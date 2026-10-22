"""Behavioral checks using disposable logs/databases and a loopback HTTP server."""

from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from overwatch.providers.claude import ClaudeRollout
from overwatch.http import make_handler
from overwatch.metadata import read_metadata
from overwatch.observer import Observer
from overwatch.providers.codex import Rollout


def event(kind, payload, at=1000, ordinal=1):
    return {"timestamp": at, "ordinal": ordinal, "type": kind, "payload": payload}


def meta(ident="root", **extra):
    return event("session_meta", {"id": ident, "cwd": "/example/project", "source": "vscode", **extra})


def counts(incoming=100, cached=60, outgoing=20):
    return {"input_tokens": incoming, "cached_input_tokens": cached,
            "output_tokens": outgoing, "reasoning_output_tokens": 7, "total_tokens": incoming + outgoing}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.logs = self.root / "sessions"
        self.logs.mkdir()
        self.path = self.logs / "rollout.jsonl"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, *events):
        with self.path.open("a") as stream:
            for item in events:
                stream.write(json.dumps(item) + "\n")

    def load(self):
        record = Rollout(self.path)
        record.update()
        return record

    def test_owned_counters_not_deltas_and_no_subset_double_counting(self):
        self.write(meta(), event("token_usage_record", {"thread_id": "root", "thread_token_usage": counts()}),
                   event("token_usage_record", {"thread_id": "root", "thread_token_usage": counts(200, 100, 30)}),
                   event("event_msg", {"type": "token_count", "info": {"total_token_usage": counts(9999)}}))
        view = self.load().public(1001, 120)
        self.assertEqual(view["tokens"]["total_tokens"], 230)
        self.assertEqual(view["tokens"]["cached_input_tokens"], 100)

    def test_inherited_parent_history_and_foreign_usage_are_not_child_usage(self):
        self.write(meta("child", parent_thread_id="parent", subagent_history_start_ordinal=10),
                   event("event_msg", {"type": "token_count", "info": {"total_token_usage": counts(9000)}}, ordinal=2),
                   event("token_usage_record", {"thread_id": "parent", "thread_token_usage": counts(9000)}, ordinal=11),
                   event("token_usage_record", {"thread_id": "child", "thread_token_usage": counts()}, ordinal=12))
        view = self.load().public(1001, 120)
        self.assertEqual(view["tokens"]["total_tokens"], 120)
        self.assertEqual(view["parent_id"], "parent")

    def test_legacy_usage_and_null_updates_preserve_last_known_values(self):
        self.write(meta(), event("event_msg", {"type": "token_count", "info": {"total_token_usage": counts(), "last_token_usage": counts(), "model_context_window": 200000}}),
                   event("event_msg", {"type": "token_count", "info": None}))
        view = self.load().public(1001, 120)
        self.assertEqual(view["tokens"]["total_tokens"], 120)
        self.assertEqual(view["context_window"], 200000)

    def test_open_turn_becomes_unknown_and_terminal_event_stays_idle(self):
        self.write(meta(), event("event_msg", {"type": "task_started", "turn_id": "new", "started_at": 1000}))
        record = self.load()
        self.assertEqual(record.public(1050, 120)["status"], "running")
        self.assertEqual(record.public(1201, 120)["status"], "unknown")
        self.write(event("event_msg", {"type": "task_complete", "turn_id": "old"}, 1050))
        record.update()
        self.assertEqual(record.public(1051, 120)["status"], "running")
        self.write(event("event_msg", {"type": "task_complete", "turn_id": "new"}, 1060))
        record.update()
        self.assertEqual(record.public(9999, 120)["status"], "idle")

    def test_partial_record_is_retried_and_truncated_rollout_reloads(self):
        self.write(meta())
        pending = json.dumps(event("event_msg", {"type": "task_started"})).encode()
        with self.path.open("ab") as stream:
            stream.write(pending[:15])
        record = self.load()
        self.assertEqual(record.public(1001, 120)["status"], "unknown")
        with self.path.open("ab") as stream:
            stream.write(pending[15:] + b"\n")
        record.update()
        self.assertEqual(record.public(1001, 120)["status"], "running")
        self.path.write_text(json.dumps(meta("replacement")) + "\n")
        record.update()
        self.assertEqual(record.id, "replacement")

    def test_snapshot_omits_prompts_tool_arguments_outputs_and_credentials(self):
        self.write(meta(base_instructions="PRIVATE INSTRUCTIONS"),
                   event("response_item", {"type": "message", "role": "user", "content": "PRIVATE PROMPT"}),
                   event("response_item", {"type": "function_call", "name": "test", "arguments": "SECRET CREDENTIAL"}),
                   event("response_item", {"type": "function_call_output", "output": "PRIVATE RESULT"}))
        observer = Observer(self.root)
        observer.refresh()
        snapshot = observer.snapshot().decode()
        for secret in ("PRIVATE INSTRUCTIONS", "PRIVATE PROMPT", "SECRET CREDENTIAL", "PRIVATE RESULT"):
            self.assertNotIn(secret, snapshot)
        self.assertIn("Tool result received", snapshot)

    def test_optional_metadata_database_is_read_without_creating_sidecars(self):
        database = self.root / "state_5.sqlite"
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("create table threads (id text, title text, tokens_used integer)")
            conn.execute("insert into threads values ('child', 'Safe title', 999999)")
            conn.execute("create table thread_spawn_edges (parent_thread_id text, child_thread_id text, status text)")
            conn.execute("insert into thread_spawn_edges values ('parent', 'child', 'done')")
        before = database.read_bytes()
        files_before = set(self.root.iterdir())
        labels, warning = read_metadata(self.root)
        self.assertIsNone(warning)
        self.assertEqual(labels["child"]["parent_id"], "parent")
        self.assertNotIn("tokens_used", labels["child"])
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), files_before)

    def test_empty_home_and_demo_are_explicit(self):
        observer = Observer(self.root / "missing")
        observer.refresh()
        empty = json.loads(observer.snapshot())
        self.assertEqual(empty["agents"], [])
        self.assertTrue(empty["warnings"])
        demo = Observer(self.root / "missing", demo=True)
        demo.refresh()
        view = json.loads(demo.snapshot())
        self.assertEqual(view["mode"], "demo")
        self.assertEqual(len(view["agents"]), 5)


def claude_event(kind, at=1000, session="parent", **extra):
    return {"type": kind, "timestamp": at, "sessionId": session, "cwd": "/example/project", **extra}


def claude_message(ident="msg-1", outgoing=5, stop="tool_use", **extra):
    return {"id": ident, "model": "claude-test", "role": "assistant", "content": [],
            "stop_reason": stop, "usage": {"input_tokens": 10, "cache_creation_input_tokens": 20,
                                           "cache_read_input_tokens": 70, "output_tokens": outgoing}, **extra}


class ClaudeObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "projects" / "project" / "parent.jsonl"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, *items, path=None):
        with (path or self.path).open("a") as stream:
            for item in items:
                stream.write(json.dumps(item) + "\n")

    def test_cache_usage_and_streamed_message_updates_are_counted_once(self):
        self.write(claude_event("assistant", message=claude_message()),
                   claude_event("assistant", message=claude_message(), uuid="duplicate-block"))
        record = ClaudeRollout(self.path)
        record.update()
        self.assertEqual(record.public(1001, 120)["tokens"]["total_tokens"], 105)
        self.write(claude_event("assistant", message=claude_message(outgoing=12)),
                   claude_event("assistant", message=claude_message("msg-2", outgoing=8, stop="end_turn")))
        record.update()
        view = record.public(1001, 120)
        self.assertEqual(view["tokens"]["input_tokens"], 200)
        self.assertEqual(view["tokens"]["cached_input_tokens"], 140)
        self.assertEqual(view["tokens"]["output_tokens"], 20)
        self.assertEqual(view["tokens"]["total_tokens"], 220)
        self.assertEqual(view["last_request"]["total_tokens"], 108)
        self.assertIsNone(view["tokens"]["reasoning_output_tokens"])
        self.assertIsNone(view["context_window"])
        self.assertEqual(view["provider"], "anthropic")
        self.assertEqual(view["model"], "claude-test")

    def test_lifecycle_distinguishes_tool_results_completed_turns_and_stale_work(self):
        self.write(claude_event("user", message={"content": "Private prompt"}),
                   claude_event("assistant", at=1010, message=claude_message(content=[{"type": "tool_use", "name": "Read", "input": {}}])),
                   claude_event("user", at=1020, message={"content": [{"type": "tool_result", "content": "Private result"}]}))
        record = ClaudeRollout(self.path)
        record.update()
        view = record.public(1021, 120)
        self.assertEqual(view["status"], "running")
        self.assertEqual(view["turn_started_at"], 1000)
        self.assertEqual(record.public(1141, 120)["status"], "unknown")
        self.write(claude_event("assistant", at=1030, message=claude_message("msg-2", stop="end_turn")),
                   claude_event("user", at=1040, isMeta=True, message={"content": "metadata"}))
        record.update()
        self.assertEqual(record.public(9999, 120)["status"], "idle")
        self.write(claude_event("assistant", at=1031, message=claude_message("msg-2", stop="end_turn")))
        record.update()
        self.assertEqual(record.public(1032, 120)["turn_started_at"], 1000)
        self.write(claude_event("user", at=1050, message={"content": "Next prompt"}),
                   claude_event("assistant", at=1060, isApiErrorMessage=True, message=claude_message("synthetic")))
        record.update()
        self.assertEqual(record.public(1061, 120)["status"], "failed")
        self.assertEqual(record.public(1061, 120)["tokens"]["total_tokens"], 210)
        self.write(claude_event("system", at=1070, subtype="turn_duration"))
        record.update()
        self.assertEqual(record.public(1071, 120)["status"], "idle")

    def test_subagents_keep_separate_identity_and_ignore_foreign_history(self):
        self.write(claude_event("assistant", message=claude_message()),
                   claude_event("assistant", isSidechain=True, agentId="child", message=claude_message("copied", outgoing=999)),
                   claude_event("assistant", session="foreign", message=claude_message("foreign", outgoing=999)))
        sub = self.path.parent / "parent" / "subagents" / "agent-child.jsonl"
        sub.parent.mkdir(parents=True)
        self.write(claude_event("assistant", agentId="child", isSidechain=True, message=claude_message(outgoing=10)),
                   claude_event("assistant", agentId="other", message=claude_message("wrong-agent", outgoing=999)), path=sub)
        observer = Observer(self.root, provider="claude")
        observer.refresh()
        agents = {a["id"]: a for a in json.loads(observer.snapshot())["agents"]}
        self.assertEqual(set(agents), {"parent", "parent:child"})
        self.assertEqual(agents["parent"]["tokens"]["total_tokens"], 105)
        self.assertEqual(agents["parent:child"]["tokens"]["total_tokens"], 110)
        self.assertEqual(agents["parent:child"]["parent_id"], "parent")
        self.assertEqual(agents["parent:child"]["source"], "subagent")

    def test_transcript_contents_and_request_blobs_are_never_exposed(self):
        self.write(claude_event("user", message={"content": "PRIVATE PROMPT"}),
                   claude_event("assistant", message=claude_message(content=[{"type": "thinking", "thinking": "PRIVATE REASONING"},
                                {"type": "tool_use", "name": "Read", "input": "SECRET CREDENTIAL"}])),
                   claude_event("user", message={"content": [{"type": "tool_result", "content": "PRIVATE RESULT"}]}),
                   claude_event("api-request-blob", message={"content": "PRIVATE REQUEST"}),
                   claude_event("last-prompt", lastPrompt="PRIVATE LAST PROMPT"))
        observer = Observer(self.root, provider="claude")
        observer.refresh()
        encoded = observer.snapshot().decode()
        for secret in ("PRIVATE PROMPT", "PRIVATE REASONING", "SECRET CREDENTIAL", "PRIVATE RESULT", "PRIVATE REQUEST", "PRIVATE LAST PROMPT"):
            self.assertNotIn(secret, encoded)
        self.assertIn("Tool result received", encoded)
        self.assertIn("Tool · Read", encoded)

    def test_partial_writes_and_truncation_reset_claude_usage(self):
        self.write(claude_event("user", message={"content": "prompt"}))
        pending = json.dumps(claude_event("assistant", message=claude_message())).encode()
        with self.path.open("ab") as stream:
            stream.write(pending[:20])
        record = ClaudeRollout(self.path)
        record.update()
        self.assertIsNone(record.tokens)
        with self.path.open("ab") as stream:
            stream.write(pending[20:] + b"\n")
        record.update()
        self.assertEqual(record.tokens["total_tokens"], 105)
        self.path.write_text(json.dumps(claude_event("assistant", message=claude_message(outgoing=1))) + "\n")
        record.update()
        self.assertEqual(record.tokens["total_tokens"], 101)

    def test_missing_claude_home_and_demo_are_explicit(self):
        observer = Observer(self.root / "missing", provider="claude")
        observer.refresh()
        view = json.loads(observer.snapshot())
        self.assertEqual(view["agents"], [])
        self.assertEqual(view["provider"], "claude")
        self.assertIn("--claude-home", view["warnings"][0])
        demo = Observer(self.root / "missing", provider="claude", demo=True)
        demo.refresh()
        sample = json.loads(demo.snapshot())
        self.assertEqual(sample["mode"], "demo")
        self.assertEqual(len(sample["agents"]), 5)
        self.assertTrue(all(a["provider"] == "anthropic" for a in sample["agents"]))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observer = Observer(Path("/not-read"), demo=True)
        cls.observer.refresh()
        cls.claude = Observer(Path("/not-read"), demo=True, provider="claude")
        cls.claude.refresh()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler({"codex": cls.observer, "claude": cls.claude}))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, method="GET", headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_dashboard_assets_and_snapshot_are_served(self):
        for path in ("/", "/style.css", "/app.js", "/theme.js", "/theme.css", "/assets/inter-tight-800.ttf", "/assets/openai.svg", "/assets/claude.svg", "/api/snapshot"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)
            self.assertEqual(headers["Cache-Control"], "no-store")

    def test_provider_snapshots_are_separate_and_unknown_providers_are_rejected(self):
        default = self.request("/api/snapshot")[2]
        self.assertEqual(default, self.request("/api/snapshot?provider=codex")[2])
        status, _, body = self.request("/api/snapshot?provider=claude")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["provider"], "claude")
        self.assertNotEqual(default, body)
        for query in ("provider=unknown", "provider=", "provider=../../secret", "provider=codex&provider=claude"):
            self.assertEqual(self.request("/api/snapshot?" + query)[0], 400)

    def test_no_filesystem_browsing_cross_origin_or_writes(self):
        for path in ("/server.py", "/../README.md", "/sessions", "/%2e%2e/README.md",
                     "/workflow.html", "/workflow.css", "/workflow.js", "/workflow-data.json"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/api/snapshot", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", method="POST")[0], 403)

    def test_session_action_failures_are_json_and_demo_logs_cannot_be_changed(self):
        for path in ("/api/session?provider=codex&id=codex-demo-1",
                     "/api/session/delete-preview?provider=codex&id=codex-demo-1"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 409)
            self.assertIn("application/json", headers["Content-Type"])
            self.assertIn("error", json.loads(body))
        body = json.dumps({"provider": "codex", "id": "codex-demo-1", "revision": "x"})
        headers = {"Host": "127.0.0.1:" + str(self.server.server_port),
                   "Origin": "http://127.0.0.1:" + str(self.server.server_port),
                   "X-Overwatch-Action": "1", "Content-Type": "application/json"}
        status, _, response = self.request("/api/session/delete", method="POST", headers=headers, body=body)
        self.assertEqual(status, 409)
        self.assertIn("error", json.loads(response))
        self.assertEqual(self.request("/api/session/delete", method="POST", headers={**headers, "Origin": "https://evil.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
