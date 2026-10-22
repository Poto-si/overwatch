"""Read optional thread labels from Codex metadata in immutable mode."""

from contextlib import closing
import sqlite3


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
