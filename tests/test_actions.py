"""Session action checks against isolated disposable transcript trees."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from overwatch.actions import SessionActions
from overwatch.observer import Observer


def codex_event(kind, payload, ordinal=1):
    return {"timestamp": 1000, "ordinal": ordinal, "type": kind, "payload": payload}


def codex_meta(ident, parent=None):
    payload = {"id": ident, "cwd": "/tmp/example project", "source": "session"}
    if parent:
        payload["parent_thread_id"] = parent
    return codex_event("session_meta", payload)


class SessionActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "codex"
        self.sessions = self.home / "sessions" / "2026" / "10" / "22"
        self.sessions.mkdir(parents=True)
        self.parent = self.sessions / "parent.jsonl"
        self.child = self.sessions / "child.jsonl"
        self.write(self.parent, codex_meta("parent"), codex_event("event_msg", {"type": "task_complete"}, 2))
        self.write(self.child, codex_meta("child", "parent"), codex_event("event_msg", {"type": "task_complete"}, 2))
        self.observer = Observer(self.home, limit=2)
        self.observer.refresh()
        self.actions = SessionActions(self.observer)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, *records):
        path.write_text("".join(json.dumps(record) + "\n" for record in records))

    @patch("overwatch.actions._codex_uri_configured", return_value=False)
    def test_open_returns_safe_resume_command_without_spawning(self, _configured):
        result = self.actions.open("parent")
        self.assertIsNone(result["url"])
        self.assertIn("codex resume parent", result["command"])
        self.assertIn("cd -- '/tmp/example project'", result["command"])
        self.assertIn("No Codex link handler", result["note"])

    def test_delete_preview_covers_unobserved_duplicate_and_child_then_archives(self):
        duplicate = self.sessions / "duplicate.jsonl"
        self.write(duplicate, codex_meta("parent"), codex_event("event_msg", {"type": "task_complete"}, 2))
        os.utime(self.parent, None)
        preview = self.actions.preview("parent")
        self.assertEqual(preview, {"revision": preview["revision"], "files": 3, "sessions": 2})
        result = self.actions.delete("parent", preview["revision"])
        self.assertEqual(result["sessions"], 2)
        self.assertFalse(self.parent.exists())
        self.assertFalse(self.child.exists())
        self.assertFalse(duplicate.exists())
        trash = self.home / ".overwatch-trash"
        batches = list(trash.iterdir())
        self.assertEqual(len(batches), 1)
        manifest = json.loads((batches[0] / "manifest.json").read_text())
        self.assertEqual(len(manifest["files"]), 3)
        self.assertEqual(len(list(batches[0].glob("*.jsonl"))), 3)
        self.assertEqual(json.loads(self.observer.snapshot())["agents"], [])

    def test_revision_and_open_turn_are_required_to_delete(self):
        preview = self.actions.preview("parent")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.actions.delete("parent", "stale")
        with self.parent.open("a") as stream:
            stream.write(json.dumps(codex_event("event_msg", {"type": "task_started"}, 3)) + "\n")
        self.observer.refresh()
        with self.assertRaisesRegex(ValueError, "open recorded turn"):
            self.actions.preview("parent")

    def test_malformed_or_partial_logs_fail_closed_and_symlink_is_not_archived(self):
        with self.parent.open("a") as stream:
            stream.write('{"type":"event_msg"')
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.actions.preview("parent")
        self.parent.unlink()
        outside = Path(self.temp.name) / "outside.jsonl"
        self.write(outside, codex_meta("parent"), codex_event("event_msg", {"type": "task_complete"}, 2))
        self.parent.symlink_to(outside)
        self.observer.refresh()
        # Observer may display the symlinked log, but deletion must not read or move it.
        with self.assertRaises((LookupError, ValueError)):
            self.actions.preview("parent")
        self.assertTrue(outside.exists())

    def test_identity_replacement_between_discovery_and_parse_is_rejected(self):
        inventory = self.actions._inventory
        def replace_after_inventory():
            found = inventory()
            self.write(self.parent, codex_meta("replacement"), codex_event("event_msg", {"type": "task_complete"}, 2))
            return found
        with patch.object(self.actions, "_inventory", side_effect=replace_after_inventory):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.actions.preview("parent")

    def test_changed_file_during_rename_is_restored_without_losing_new_bytes(self):
        preview = self.actions.preview("parent")
        real_rename = self.actions._rename
        changed = []
        def append_after_move(*args, **kwargs):
            result = real_rename(*args, **kwargs)
            if not changed:
                changed.append(True)
                # The first moved transcript receives a live append before destination verification.
                name = args[1]
                fd = kwargs["dst_dir_fd"]
                descriptor = os.open(name, os.O_WRONLY | os.O_APPEND, dir_fd=fd)
                with os.fdopen(descriptor, "ab") as stream:
                    stream.write(b'{"type":"event_msg","payload":{"type":"task_started"}}\n')
            return result
        with patch.object(self.actions, "_rename", side_effect=append_after_move):
            with self.assertRaisesRegex(ValueError, "changed during deletion"):
                self.actions.delete("parent", preview["revision"])
        self.assertTrue(self.parent.exists())
        self.assertTrue(self.child.exists())
        self.assertTrue(any(b"task_started" in path.read_bytes() for path in (self.parent, self.child)))


class ClaudeSessionActionTests(unittest.TestCase):
    def test_subagent_open_targets_parent_and_parent_delete_archives_both_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "claude"
            project = home / "projects" / "project"
            child = project / "parent" / "subagents" / "agent-child.jsonl"
            child.parent.mkdir(parents=True)
            parent = project / "parent.jsonl"

            def assistant(session, agent=None):
                item = {"type": "assistant", "sessionId": session, "timestamp": "2026-10-22T10:00:00Z",
                        "cwd": "/tmp/project", "message": {"id": "msg", "model": "claude-test",
                        "role": "assistant", "content": [], "stop_reason": "end_turn", "usage": {}}}
                if agent:
                    item.update({"agentId": agent, "isSidechain": True})
                return item

            parent.write_text(json.dumps(assistant("parent")) + "\n")
            child.write_text(json.dumps(assistant("parent", "child")) + "\n")
            observer = Observer(home, provider="claude")
            observer.refresh()
            actions = SessionActions(observer)
            opened = actions.open("parent:child")
            self.assertIsNone(opened["url"])
            self.assertIn("claude --resume parent", opened["command"])
            preview = actions.preview("parent")
            self.assertEqual((preview["files"], preview["sessions"]), (2, 2))
            actions.delete("parent", preview["revision"])
            self.assertFalse(parent.exists())
            self.assertFalse(child.exists())
            self.assertEqual(len(list((home / ".overwatch-trash").glob("*/manifest.json"))), 1)


if __name__ == "__main__":
    unittest.main()
