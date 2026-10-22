"""Read-only local Codex and Claude Code observer. Python standard library; no model/API calls."""

import argparse
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import threading

from .http import make_handler
from .observer import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
    parser.add_argument("--claude-home", type=Path, default=Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser())
    parser.add_argument("--limit", type=int, default=120, help="Most recently modified rollout files to observe")
    parser.add_argument("--stale-after", type=int, default=120, help="Open turns become unknown after this many seconds without activity")
    parser.add_argument("--interval", type=float, default=2, help="Log refresh interval in seconds")
    parser.add_argument("--demo", action="store_true", help="Show clearly labelled sample data; do not read session state")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.limit < 1 or args.stale_after < 1 or args.interval < 0.5:
        parser.error("Choose a valid port, positive limit/stale threshold and interval of at least 0.5 seconds")
    observers = {provider: Observer(home.expanduser().resolve(), args.limit, args.stale_after, args.demo, args.interval, provider)
                 for provider, home in (("codex", args.codex_home), ("claude", args.claude_home))}
    server = ThreadingHTTPServer((args.host, args.port), make_handler(observers))
    stop = threading.Event()

    def collect(observer):
        while not stop.is_set():
            try:
                observer.refresh()
            except Exception as exc:
                # Retain the last good view without exposing raw exceptions or prompts.
                print(f"{observer.provider} observation failed ({type(exc).__name__}); retaining last snapshot", flush=True)
            stop.wait(args.interval)

    workers = [threading.Thread(target=collect, args=(observer,), daemon=True) for observer in observers.values()]
    for worker in workers:
        worker.start()
    print(f"Poto.si | Overwatch: http://127.0.0.1:{args.port}", flush=True)
    for provider, observer in observers.items():
        print(f"{provider}: sample data" if args.demo else f"{provider} read-only logs: {observer.directory}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        for worker in workers:
            worker.join(timeout=2)
