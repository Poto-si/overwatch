"""Provide clearly labelled sample agent observations."""

from .usage import usage


def demo_agents(now, provider="codex"):
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
                       "model": "claude-demo" if provider == "claude" else model, "provider": "anthropic" if provider == "claude" else "openai", "source": "subagent" if parent else "demo",
                       "status": status, "status_reason": "Sample data — no real agent is connected", "status_source": "demo",
                       "created_at": now - elapsed, "last_seen": now - (2 if status == "running" else 80),
                       "turn_started_at": now - elapsed, "turn_ended_at": None if status in ("running", "waiting") else now - 80,
                       "tokens": usage({"input_tokens": incoming, "cached_input_tokens": cached, "output_tokens": outgoing}),
                       "last_request": usage({"input_tokens": 32000, "cached_input_tokens": 18000, "output_tokens": 1200}),
                       "context_window": 200000, "activity": [{"at": now - 2, "text": "Sample activity", "kind": "tool"}],
                       "skipped_records": 0})
    return result
