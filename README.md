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

- **Welcome:** on your first visit, choose Codex or Claude in the welcome modal.
  Your choice is remembered in this browser; use the platform menu to switch later.
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
| `--host` | `127.0.0.1` | Address to bind; Docker uses `0.0.0.0` inside the container |
| `--codex-home` | `$CODEX_HOME` or `~/.codex` | Codex profile directory |
| `--claude-home` | `$CLAUDE_CONFIG_DIR` or `~/.claude` | Claude Code profile directory |
| `--limit` | `120` | Maximum recent transcript files per platform |
| `--stale-after` | `120` | Seconds without activity before an open turn becomes Unknown |
| `--interval` | `2` | Server log refresh interval in seconds; minimum `0.5` |
| `--demo` | Off | Show sample data for both platforms without reading session state |

The browser requests a snapshot every two seconds. `--interval` controls how
often the server reads the underlying logs.

## Docker

Build the image and open the sample dashboard:

```sh
make docker-build
make docker-demo
```

Open **http://127.0.0.1:8765**. For real observations, `make docker-run` mounts
any existing Codex and Claude directories read-only. Override their locations
or the port as needed:

```sh
make docker-run CODEX_HOME=/path/to/.codex CLAUDE_HOME=/path/to/.claude PORT=8766
```

The container listens on `0.0.0.0` internally, while Docker publishes the port
on `127.0.0.1`. Open the matching local port in the browser.

## How it works

Overwatch reads the Codex and Claude Code session logs on your computer and
refreshes the dashboard as new activity is recorded. Use the platform selector
to switch between them and inspect sessions, subagents, and recorded usage.

Read more about the [technical architecture](docs/architecture.md),
[status and usage limits](docs/status-and-usage.md), and
[privacy and boundaries](docs/privacy.md).

## Development

Use `make` or `make help` to list common development commands. `make install`
creates `.venv` and installs an editable copy; `make dev` starts the server and
`make demo` starts it with sample data. Pass options through `ARGS`.

Run the behavioral tests from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For an editable development install:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
overwatch --demo
```

Tests use disposable logs and databases plus a temporary loopback server. They
cover usage attribution, partial writes, stale status, data minimization,
immutable database access, provider isolation, Claude streaming usage and
subagent attribution, static asset serving, and read-only HTTP boundaries.
They never read real sessions or contact model providers.

## System service

To install Overwatch as a system-wide service for your account, run this from a
terminal with administrator authentication available:

```sh
make install-system
```

The installer places its virtual environment under `/opt/overwatch`, adds the
launcher at `/usr/local/bin/overwatch`, and installs
`/etc/systemd/system/overwatch.service`. The service runs as your user, reads
your Codex and Claude logs, and serves on `127.0.0.1:8765`. If an existing user
service is present, the installer removes it only after the new system service
starts and passes its health check.

Check or restart the system service with:

```sh
sudo systemctl status overwatch
sudo systemctl restart overwatch
```
