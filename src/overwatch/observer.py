"""Discover and refresh session observations for each configured provider."""

import json
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
        self.encoded = b'{"agents":[],"loading":true,"warnings":[]}'

    def refresh(self):
        now, warnings = time.time(), []
        if self.demo:
            agents = demo_agents(now, self.provider)
            discovered = len(agents)
        else:
            files = []
            directory = self.directory
            try:
                files = sorted(directory.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
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
