"""Parse Claude Code transcripts using the shared rollout interface."""

from .codex import Rollout
from ..usage import TOKEN_KEYS, timestamp, usage


class ClaudeRollout(Rollout):
    """Read Claude Code transcripts without retaining prompts or tool payloads."""

    def __init__(self, path):
        super().__init__(path)
        self.provider = "anthropic"
        self.is_subagent = path.parent.name == "subagents"
        self.session_id = path.parent.parent.name if self.is_subagent else path.stem
        self.agent_id = path.stem.removeprefix("agent-") if self.is_subagent else None
        self.source = "subagent" if self.is_subagent else "claude-code"
        self.parent = self.session_id if self.is_subagent else None
        self.name = "Agent " + self.agent_id if self.is_subagent else "Claude session"
        self.message_usage = {}

    def consume(self, item):
        # Subagent records share the parent's sessionId, but have their own agentId.
        session = item.get("sessionId") or item.get("session_id")
        if session != self.session_id:
            return
        if self.is_subagent:
            if item.get("agentId") not in (None, self.agent_id):
                return
        elif item.get("isSidechain") or item.get("agentId"):
            return
        self.id = self.session_id + ":" + self.agent_id if self.is_subagent else self.session_id
        kind = item.get("type")
        at = timestamp(item.get("timestamp"))
        if item.get("cwd"):
            self.cwd = str(item["cwd"])
        if at is not None:
            self.created = min(self.created or at, at)
        if kind in ("custom-title", "ai-title", "agent-name"):
            self.name = str(item.get("customTitle") or item.get("aiTitle") or item.get("agentName") or self.name)[:180]
            return
        message = item.get("message")
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if kind == "user":
            if item.get("isMeta") or item.get("isCompactSummary"):
                return
            tool_result = any(b.get("type") == "tool_result" for b in blocks)
            if not tool_result or self.turn_started is None:
                self.turn_started = at
            self.turn_ended = None
            self.state, self.state_reason = "running", "A user message or tool result was recorded; process liveness is inferred"
            self.note(at, "Tool result received" if tool_result else "Turn started", "tool" if tool_result else "lifecycle")
        elif kind == "assistant":
            if item.get("isApiErrorMessage"):
                self.state, self.state_reason = "failed", "An API error was recorded"
                self.turn_ended = at
                self.note(at, "API error recorded", "lifecycle")
                return
            model = message.get("model")
            if isinstance(model, str) and not model.startswith("<"):
                self.model = model
            recorded = message.get("usage")
            ident = message.get("id") or item.get("requestId") or item.get("uuid")
            seen_message = isinstance(ident, str) and ident in self.message_usage
            if isinstance(recorded, dict) and isinstance(ident, str):
                def count(key):
                    value = recorded.get(key)
                    return max(0, value) if type(value) is int else 0
                current = usage({"input_tokens": count("input_tokens") + count("cache_creation_input_tokens") + count("cache_read_input_tokens"),
                                 "cached_input_tokens": count("cache_read_input_tokens"),
                                 "output_tokens": count("output_tokens")})
                # Several content blocks can repeat/update one API message's usage.
                previous = self.message_usage.get(ident, usage({}))
                updated = {key: max(previous[key], current[key]) for key in TOKEN_KEYS}
                self.message_usage[ident] = updated
                if self.tokens is None:
                    self.tokens = usage({})
                for key in TOKEN_KEYS:
                    self.tokens[key] += updated[key] - previous[key]
                self.tokens["total_tokens"] = self.tokens["input_tokens"] + self.tokens["output_tokens"]
                self.last_request = updated
            if self.turn_started is None or (self.turn_ended is not None and not seen_message):
                self.turn_started, self.turn_ended = at, None
            tools = [b for b in blocks if b.get("type") == "tool_use"]
            for block in tools:
                self.note(at, "Tool · " + str(block.get("name") or "unnamed"), "tool")
            if not tools:
                self.note(at, "Assistant response", "message")
            if message.get("stop_reason") in ("end_turn", "stop_sequence", "refusal") and not tools:
                self.state, self.state_reason = "idle", "Last recorded assistant turn completed"
                self.turn_ended = at
            else:
                self.state, self.state_reason = "running", "An assistant response or tool call was recorded; the turn remains open"
        elif kind == "system" and item.get("subtype") == "turn_duration":
            self.state, self.state_reason = "idle", "Last recorded turn completed"
            self.turn_ended = at
            self.note(at, "Turn completed", "lifecycle")
        elif kind == "progress":
            self.note(at, "Tool progress recorded", "tool")

    def public(self, now, stale_after, metadata=None):
        result = super().public(now, stale_after, metadata)
        # Claude transcripts do not consistently separate reasoning token usage.
        for key in ("tokens", "last_request"):
            if result[key] is not None:
                result[key] = {**result[key], "reasoning_output_tokens": None}
        return result
