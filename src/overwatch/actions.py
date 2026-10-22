"""Explicit open and delete actions bounded to observed provider transcripts."""

from contextlib import ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import time
import uuid
from urllib.parse import quote

from .providers.claude import ClaudeRollout
from .providers.codex import MAX_LINE, Rollout


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _regular_files(root):
    """Walk transcript names without following symlink directories or files."""
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(root_info.st_mode):
        return []
    result = []
    def raise_walk_error(error):
        raise error
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=raise_walk_error):
        safe_dirs = []
        for name in dirs:
            path = Path(current) / name
            try:
                if stat.S_ISDIR(path.lstat().st_mode):
                    safe_dirs.append(name)
            except FileNotFoundError:
                raise
        dirs[:] = safe_dirs
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = Path(current) / name
            try:
                if stat.S_ISREG(path.lstat().st_mode):
                    result.append(path)
            except FileNotFoundError:
                raise
    return result


def _codex_header(path):
    """Read only the bounded session_meta header needed to map a log to a thread."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for _ in range(64):
                line = stream.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE:
                    break
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(item, dict) and item.get("type") == "session_meta":
                    record = Rollout(path)
                    record.consume(item)
                    return record
        return None
    finally:
        os.close(descriptor)


def _codex_uri_configured():
    """Return true only when Linux has a resolvable desktop URI handler."""
    query = shutil.which("xdg-mime")
    if query is None:
        return False
    try:
        result = subprocess.run([query, "query", "default", "x-scheme-handler/codex"],
                                capture_output=True, text=True, timeout=1, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    desktop = result.stdout.strip()
    if result.returncode or not desktop or "/" in desktop or "\\" in desktop:
        return False
    roots = [Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"]
    roots.extend(Path(item) / "applications" for item in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))
    return any((root / desktop).is_file() for root in roots)


class SessionActions:
    def __init__(self, observer):
        self.observer = observer
        self.secret = os.urandom(32)

    def _selected(self, session_id):
        observer = self.observer
        if observer.demo:
            raise ValueError("Session actions are unavailable for sample agents.")
        if not isinstance(session_id, str) or not _IDENTIFIER.fullmatch(session_id):
            raise ValueError("Invalid session identifier.")
        observer.refresh()
        return next((record for record in observer.rollouts.values() if record.id == session_id), None)

    def open(self, session_id):
        with self.observer.operation_lock:
            selected = self._selected(session_id)
            if selected is None:
                raise LookupError("This session is no longer observed. Refresh the dashboard.")
            ident = selected.session_id if self.observer.provider == "claude" else selected.id
            if not isinstance(ident, str) or not _IDENTIFIER.fullmatch(ident):
                raise ValueError("This session has an invalid identifier and cannot be resumed.")
            if self.observer.provider == "codex":
                command = self._resume_command(selected.cwd, "CODEX_HOME", self.observer.home,
                                               "codex resume " + shlex.quote(ident))
                url = "codex://threads/" + quote(ident, safe="") if _codex_uri_configured() else None
                return {"url": url, "command": command,
                        "name": selected.name or "Codex session",
                        "note": None if url else "No Codex link handler is configured. Copy the command into a terminal to resume this chat."}
            command = self._resume_command(selected.cwd, "CLAUDE_CONFIG_DIR", self.observer.home,
                                           "claude --resume " + shlex.quote(ident))
            return {"url": None, "command": command,
                    "name": selected.name or "Claude session",
                    "note": "Claude Code does not provide a supported session link here. Copy the command into a terminal to resume this chat."}

    @staticmethod
    def _resume_command(cwd, variable, home, command):
        prefix = "cd -- " + shlex.quote(cwd) + " && " if isinstance(cwd, str) and cwd else ""
        return prefix + variable + "=" + shlex.quote(str(home)) + " " + command

    def _inventory(self):
        observer = self.observer
        records = []
        metadata = {}
        if observer.provider == "codex":
            from .metadata import read_metadata
            metadata, _ = read_metadata(observer.home)
        for path in _regular_files(observer.directory):
            if observer.provider == "claude":
                record = ClaudeRollout(path)
                record.id = record.session_id + (":" + record.agent_id if record.is_subagent else "")
            else:
                record = _codex_header(path)
                if record is None:
                    continue
                if not isinstance(record.id, str):
                    continue
                extra = metadata.get(record.id, {})
                record.parent = record.parent or (extra.get("parent_id") if isinstance(extra, dict) else None)
            if isinstance(record.id, str) and _IDENTIFIER.fullmatch(record.id):
                if not isinstance(record.parent, str) or not _IDENTIFIER.fullmatch(record.parent):
                    record.parent = None
                records.append(record)
        return records

    def _plan(self, session_id):
        selected = self._selected(session_id)
        if selected is None:
            raise LookupError("This session is no longer observed. Refresh the dashboard.")
        records = self._inventory()
        identities = {session_id}
        while True:
            children = {record.id for record in records if record.parent in identities}
            expanded = identities | children
            if expanded == identities:
                break
            identities = expanded
        chosen = [record for record in records if record.id in identities]
        if not chosen or not any(record.id == session_id for record in chosen):
            raise LookupError("The session transcripts are no longer available.")
        stamps, verified = [], []
        for candidate in chosen:
            # Validate all path components before opening or updating transcript data.
            relative = candidate.path.relative_to(self.observer.directory)
            current = self.observer.directory
            for component in relative.parts:
                current = current / component
                info = current.lstat()
                if current == candidate.path:
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("A transcript is a symbolic link or is no longer a regular file.")
                elif not stat.S_ISDIR(info.st_mode):
                    raise ValueError("A transcript path contains a symbolic link or changed directory.")
            before = candidate.path.lstat()
            initial = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if self.observer.provider == "claude":
                record = ClaudeRollout(candidate.path)
            else:
                record = Rollout(candidate.path)
            record.update()
            after = record.path.lstat()
            final = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if initial != final:
                raise ValueError("A transcript changed during inspection. Refresh before deleting.")
            if record.id != candidate.id or record.id not in identities:
                raise ValueError("A transcript identity changed during inspection. Refresh before deleting.")
            if record.skipped or record.offset != final[2]:
                raise ValueError("A transcript contains malformed or unfinished records. Wait for it to finish before deleting.")
            if record.state in ("running", "waiting") or (record.turn_started is not None and record.turn_ended is None):
                raise ValueError("This chat or a subagent has an open recorded turn. Stop or finish it in the original app before deleting.")
            info = after
            stamps.append((str(relative), record.id, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
            verified.append(record)
        stamps.sort()
        revision = hmac.new(self.secret, json.dumps(stamps).encode(), hashlib.sha256).hexdigest()
        return verified, stamps, revision

    def preview(self, session_id):
        with self.observer.operation_lock:
            try:
                records, _, revision = self._plan(session_id)
            except OSError as exc:
                raise ValueError("The session files changed or could not be read. Refresh the dashboard and try again.") from exc
            return {"revision": revision, "files": len(records), "sessions": len({record.id for record in records})}

    @staticmethod
    def _directory(stack, path, parent=None):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stack.callback(os.close, descriptor)
        return descriptor

    @staticmethod
    def _rename(source, target, **kwargs):
        return os.rename(source, target, **kwargs)

    def delete(self, session_id, revision):
        with self.observer.operation_lock:
            required = (os.open, os.mkdir, os.stat, os.rename, os.link, os.unlink)
            if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "getuid")
                    or not all(callable(fn) and fn in os.supports_dir_fd for fn in required)):
                raise ValueError("Safe transcript recovery is unavailable on this platform.")
            try:
                records, stamps, current = self._plan(session_id)
            except OSError as exc:
                raise ValueError("The session files changed or could not be read. Refresh the dashboard and try again.") from exc
            if not isinstance(revision, str) or not hmac.compare_digest(revision, current):
                raise ValueError("The session changed since confirmation. Review it again before deleting.")
            observer = self.observer
            try:
                with ExitStack() as stack:
                    root = self._directory(stack, str(observer.home))
                    source = self._directory(stack, observer.directory.name, root)
                    prepared = []
                    for relative, ident, dev, inode, size, modified in stamps:
                        parts = relative.split(os.sep)
                        parent = source
                        for part in parts[:-1]:
                            parent = self._directory(stack, part, parent)
                        info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                        expected = (dev, inode, size, modified)
                        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected:
                            raise ValueError("A transcript changed or is a symbolic link. Refresh before deleting.")
                        prepared.append((parent, parts[-1], relative, ident, expected))
                    try:
                        os.mkdir(".overwatch-trash", mode=0o700, dir_fd=root)
                    except FileExistsError:
                        pass
                    trash = self._directory(stack, ".overwatch-trash", root)
                    trash_info = os.fstat(trash)
                    if not stat.S_ISDIR(trash_info.st_mode) or trash_info.st_uid != os.getuid() or trash_info.st_mode & 0o077:
                        raise ValueError("The session recovery directory has unsafe permissions.")
                    batch = str(uuid.uuid4())
                    os.mkdir(batch, mode=0o700, dir_fd=trash)
                    destination = self._directory(stack, batch, trash)
                    manifest = {"provider": observer.provider, "session": session_id, "deleted_at": time.time(),
                                "files": [{"original": relative, "stored": f"{index}.jsonl", "session": ident}
                                          for index, (_, _, relative, ident, _) in enumerate(prepared)]}
                    descriptor = os.open("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
                    with os.fdopen(descriptor, "w") as stream:
                        json.dump(manifest, stream)
                    moved = []
                    try:
                        for index, (parent, name, _, _, expected) in enumerate(prepared):
                            # Recheck immediately before rename, then inspect the moved file through the destination fd.
                            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != expected:
                                raise ValueError("A transcript changed during deletion. Refresh before trying again.")
                            stored = f"{index}.jsonl"
                            self._rename(name, stored, src_dir_fd=parent, dst_dir_fd=destination)
                            moved.append((parent, name, stored, expected))
                            after = os.stat(stored, dir_fd=destination, follow_symlinks=False)
                            if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != expected:
                                raise ValueError("A transcript changed during deletion. A recovery copy was preserved.")
                    except (OSError, ValueError):
                        for parent, name, stored, expected in reversed(moved):
                            try:
                                info = os.stat(stored, dir_fd=destination, follow_symlinks=False)
                                if stat.S_ISREG(info.st_mode):
                                    os.link(stored, name, src_dir_fd=destination, dst_dir_fd=parent, follow_symlinks=False)
                                    os.unlink(stored, dir_fd=destination)
                            except OSError:
                                pass
                        raise
            except OSError as exc:
                raise ValueError("Could not remove the session logs. Check filesystem permissions; Docker session mounts are read-only. Any recovery copies remain in .overwatch-trash.") from exc
            finally:
                observer.refresh()
            return {"message": "Chat deleted from local session logs. A recovery copy is kept in .overwatch-trash.",
                    "sessions": len({record.id for record in records})}
