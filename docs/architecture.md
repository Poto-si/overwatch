# Technical architecture

Python lives in `src/overwatch/`; the dashboard is packaged from
`src/overwatch/web/`, including its local fonts and icons. `server.py` remains a
repository-root launcher for existing workflows, while the installed
`overwatch` command starts the same application.

The Python modules separate usage accounting (`usage.py`), Codex and Claude
transcript readers (`providers/codex.py`, `providers/claude.py`), metadata
(`metadata.py`), sample observations (`demo.py`), refresh coordination
(`observer.py`), HTTP serving (`http.py`), and command-line startup (`cli.py`).

The HTTP server incrementally reads session transcripts, extracts lifecycle and usage
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

`web/app.js` polls the snapshot endpoint and renders the dashboard. `web/theme.js`
and `web/theme.css` manage appearance. All assets, including the wordmark font,
are served locally.
