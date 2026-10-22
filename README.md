# Overwatch

A local, read-only dashboard for Codex and Claude Code sessions and their recorded subagents.
See agent activity, parent/child relationships, models, and token usage in one
place. Built with Python's standard library and vanilla HTML, CSS, and JavaScript.

## Quick start

Requires **Python 3.10+**. No packages, build step, API keys, or model calls are needed.

```sh
git clone https://github.com/Poto-si/overwatch.git
cd overwatch
python3 server.py
```

Open **http://127.0.0.1:8765**. Stop the server with **Ctrl+C**.

To explore the interface with sample data without reading your local sessions:

```sh
python3 server.py --demo
```

## Features

- **Codex and Claude:** switch platforms from the logo dropdown beside Appearance.
  Each platform has its own live observations; your choice persists across reloads
  and browser tabs. Switching platforms clears the current filters and resumes
  browser refresh.
- **Squares and table views:** inspect sessions grouped by project or arranged in
  collapsible parent/child trees.
- **Search and filters:** find agents by name, model, project, status, or open turn.
- **Agent details:** see recorded lifecycle events, recent activity, model,
  provider, and parent session.
- **Token accounting:** inspect input, cached input, output, reasoning output,
  and last-request size relative to the recorded context window.
- **Automatic refresh:** observations update every two seconds by default.
  Pause view stops browser refresh without interrupting agents.
- **Appearance:** dark, light, and system themes with Mint, Sky, Violet, Amber,
  and Rose accents. Preferences persist across reloads and browser tabs.

## Configuration

By default, Overwatch reads the most recently modified 120 JSONL rollout files
under `$CODEX_HOME/sessions`, or `~/.codex/sessions` when `CODEX_HOME` is unset.
Claude Code transcripts are read independently from `$CLAUDE_CONFIG_DIR/projects`,
or `~/.claude/projects`. The file limit applies separately to each platform,
including its subagent transcripts.

```sh
python3 server.py --codex-home /path/to/codex-profile
python3 server.py --claude-home /path/to/claude-profile
python3 server.py --port 8766 --limit 200
python3 server.py --stale-after 300 --interval 5
python3 server.py --help
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--port` | `8765` | Local HTTP port |
| `--codex-home` | `$CODEX_HOME` or `~/.codex` | Codex profile directory |
| `--claude-home` | `$CLAUDE_CONFIG_DIR` or `~/.claude` | Claude Code profile directory |
| `--limit` | `120` | Maximum recent transcript files per platform |
| `--stale-after` | `120` | Seconds without activity before an open turn becomes Unknown |
| `--interval` | `2` | Server log refresh interval in seconds; minimum `0.5` |
| `--demo` | Off | Show sample data for both platforms without reading session state |

The browser requests a snapshot every two seconds. `--interval` controls how
often the server reads the underlying logs.

## How it works

`server.py` incrementally reads session transcripts, extracts lifecycle and usage
events, and serves `/api/snapshot?provider=codex` and
`/api/snapshot?provider=claude` alongside an explicit list of static assets.
`/api/snapshot` defaults to Codex for compatibility. Independent refresh workers
keep the two sources separate.
Partial trailing records are retried after the producer finishes writing.
Codex labels and parent/child links come from an optional local `state_*.sqlite` database
opened in read-only immutable mode, without creating SQLite sidecar files.
Metadata in an uncheckpointed WAL may not yet be visible.

Claude Code sessions come from `projects/<project>/<session>.jsonl`; recorded
subagents come from `<session>/subagents/agent-<agent>.jsonl`. Subagents retain
separate identities and usage, linked to their parent session. The reader uses
only transcript metadata and event types, without retaining message contents.

`app.js` polls the snapshot endpoint and renders the dashboard. `theme.js` and
`theme.css` manage appearance. All assets, including the wordmark font, are
served locally.

### Status and usage limits

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

## Privacy and boundaries

The server binds to **127.0.0.1**, restricts requests to local same-origin access,
and accepts no mutation endpoints. It never spawns, resumes, messages, or
controls agents, and does not modify either platform's logs or databases.

Activity includes event labels and tool names, without prompt text, reasoning,
tool arguments, credentials, or tool results. Thread titles and local paths can
still be sensitive, so keep the dashboard on your own machine. There is no CDN,
external font loading, analytics, or telemetry.

## Development

Run the behavioral tests from the repository root:

```sh
python3 -m unittest -v test_dashboard
```

Tests use disposable logs and databases plus a temporary loopback server. They
cover usage attribution, partial writes, stale status, data minimization,
immutable database access, provider isolation, Claude streaming usage and
subagent attribution, static asset serving, and read-only HTTP boundaries.
They never read real sessions or contact model providers.
