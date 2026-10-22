# Repository Guidelines

## Project Structure & Module Organization

`src/overwatch/` contains the Python application: `cli.py` handles startup,
`http.py` serves requests, and `observer.py` coordinates discovery. Transcript
parsers live in `providers/codex.py` and `providers/claude.py`; shared accounting
lives in `usage.py`. Keep HTML, JavaScript, CSS, and bundled assets under
`src/overwatch/web/`. The root `server.py` is a thin checkout launcher.
Tests belong in `tests/`, technical documentation in `docs/`, and system
installation tooling in `scripts/`. Packaging is defined in `pyproject.toml`.

## Build, Test, and Development Commands

Use Python 3.10 or newer. The runtime uses only the standard library.

- `make install`: create `.venv` and install an editable checkout.
- `make dev`: run against local session logs; pass options with `ARGS`.
- `make demo PORT=8766`: run sample data on a chosen port.
- `make test` or `make check`: run the Python behavioral suite.
- `make docker-build`: build the container image.
- `make docker-demo`: run the image with sample data.

For a direct source run, use `python3 server.py --demo`. No frontend build
step is required.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and modules, and
`PascalCase` classes. Match the existing two-space JavaScript indentation and
`camelCase` names. Keep provider parsing separate from HTTP and presentation
logic. No formatter or linter is configured; follow surrounding style and
check JavaScript syntax with `node --check src/overwatch/web/app.js`
(and `theme.js` when changed).

## Testing Guidelines

Tests use `unittest`, `test_*.py` files, and `test_*` methods. Add behavioral
regressions for parser, usage, status, and HTTP changes. Use disposable
transcripts and databases; never depend on private sessions or provider calls.
There is no configured coverage percentage. Without an editable install, run
`PYTHONPATH=src python3 -m unittest discover -s tests -v`.

## Commit & Pull Request Guidelines

Follow the history's concise imperative titles, such as
“Move technical details and observation limits into docs.” Describe the
behavior change, validation, and relevant issues in pull requests. Include
screenshots for visible interface changes.

## Privacy & Security Boundaries

Keep observation read-only. Explicit, confirmed Delete actions may move known
transcripts to local recovery storage. Preserve loopback defaults, same-origin
checks, and the static-file allowlist. Never expose prompts, reasoning, tool
payloads, or credentials. Keep frontend resources local. Consult
`docs/privacy.md` and `docs/session-actions.md` before changing session actions.
