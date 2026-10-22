"""Discover and refresh session observations for each configured provider."""

import json
import os
from pathlib import Path
import stat
import threading
import time

from .providers.claude import ClaudeRollout
from .demo import demo_agents
from .metadata import read_metadata
from .providers.codex import Rollout


class Observer:
    def __init__(self, home, limit=120, stale_after=120, demo=False, interval=2, provider="codex"):
        self.home, self.limit, self.stale_after, self.demo = home, limit, stale_after, demo
        if provider not in ("codex", "claude"):
            raise ValueError("Unknown session provider")
        self.provider = provider
        self.directory = home / ("projects" if provider == "claude" else "sessions")
        self.interval = interval
        self.rollouts = {}
        self.lock = threading.Lock()
        self.operation_lock = threading.RLock()
        self.encoded = b'{"agents":[],"loading":true,"warnings":[]}'

    def refresh(self):
        with self.operation_lock:
            self._refresh()

    def _refresh(self):
        now, warnings = time.time(), []
        if self.demo:
            agents = demo_agents(now, self.provider)
            discovered = len(agents)
        else:
            files = []
            directory = self.directory
            try:
                try:
                    directory_info = directory.lstat()
                except FileNotFoundError:
                    directory_info = None
                if directory_info is not None and stat.S_ISDIR(directory_info.st_mode):
                    for current, dirs, names in os.walk(directory, topdown=True, followlinks=False):
                        dirs[:] = [name for name in dirs
                                   if _is_real_directory(Path(current) / name)]
                        for name in names:
                            if name.endswith(".jsonl"):
                                path = Path(current) / name
                                try:
                                    if stat.S_ISREG(path.lstat().st_mode):
                                        files.append(path)
                                except OSError:
                                    pass
                files.sort(key=lambda p: p.lstat().st_mtime, reverse=True)
                if not directory.is_dir():
                    warnings.append(f"No {self.provider} session directory found. Set --{self.provider}-home or use --demo.")
            except OSError:
                warnings.append("Some session files could not be discovered")
            discovered = len(files)
            chosen = set(files[:self.limit])
            self.rollouts = {p: r for p, r in self.rollouts.items() if p in chosen}
            metadata, warning = read_metadata(self.home) if self.provider == "codex" else ({}, None)
            if warning:
                warnings.append(warning)
            agents = []
            record_type = ClaudeRollout if self.provider == "claude" else Rollout
            for path in files[:self.limit]:
                record = self.rollouts.get(path)
                if record is None:
                    record = self.rollouts[path] = record_type(path)
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
                   "source": "Sample data" if self.demo else str(self.directory), "provider": self.provider,
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


def _is_real_directory(path):
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False
