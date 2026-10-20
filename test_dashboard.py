"""Behavioral checks using disposable logs/databases and a loopback HTTP server."""

from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from server import Observer, Rollout, ThreadingHTTPServer, make_handler, read_metadata


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
        with sqlite3.connect(database) as conn:
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


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observer = Observer(Path("/not-read"), demo=True)
        cls.observer.refresh()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.observer))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_dashboard_assets_and_snapshot_are_served(self):
        for path in ("/", "/style.css", "/app.js", "/theme.js", "/theme.css", "/assets/inter-tight-800.ttf", "/api/snapshot"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)
            self.assertEqual(headers["Cache-Control"], "no-store")

    def test_no_filesystem_browsing_cross_origin_or_writes(self):
        for path in ("/server.py", "/../README.md", "/sessions", "/%2e%2e/README.md",
                     "/workflow.html", "/workflow.css", "/workflow.js", "/workflow-data.json"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/api/snapshot", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", method="POST")[0], 501)


if __name__ == "__main__":
    unittest.main()
