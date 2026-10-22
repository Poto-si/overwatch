# Status and usage limits

**Status is inferred from logs, not authoritative process liveness.** Working
means a turn started with recent recorded activity. Idle means the last recorded
turn completed, not that the process exited. An open turn becomes Unknown after
120 seconds without activity by default. Waiting requires an explicit recorded
waiting event. Long tool calls, exited processes, and lost connections cannot
always be distinguished from these logs.

Displayed token totals are input plus output. Cached input is included in input;
reasoning output is included in output. Attributed thread usage takes precedence
over legacy counters. Known inherited history and foreign-thread usage are
excluded, but unattributed or runtime-aggregated counters may overlap. These
observations are not billing, subscription quota, or a live context measurement.

Claude usage is accumulated per API message ID, so repeated streamed content
blocks do not multiply the same request's usage. Total input includes uncached
input, cache creation, and cache reads, following [Anthropic's token accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
The cached-input subset represents cache reads. Separate reasoning usage and
context-window capacity display as unknown when not recorded. A user message or
tool result opens a turn; an assistant end-of-turn marker or turn-duration event
closes it. Tool calls keep the turn open. Claude waiting/approval states are not
inferred from tool contents; an inactive open turn becomes Unknown.

Overwatch observes only the selected transcript files. It is not a global
inventory of every agent process or account. Transcript and SQLite formats are
internal; unfamiliar versions may yield incomplete observations.
