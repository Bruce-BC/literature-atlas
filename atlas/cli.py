"""Command line entry point for the Literature Atlas bridge."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .server import AtlasHTTPServer
from .storage import SQLiteStore
from .sync import SyncManager
from .zotero import ZoteroClient


def _load_demo(path: Path | None = None) -> dict[str, Any]:
    path = path or (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "snapshot.json")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the local loopback bridge")
    serve.add_argument("--data-dir", type=Path, default=Path.home() / ".literature-atlas")
    serve.add_argument("--web-root", type=Path, default=None)
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--poll-interval", type=float, default=30.0)
    serve.add_argument("--launch-id", default=None)
    serve.add_argument("--parent-origin", default=None)
    serve.add_argument("--embedding-provider", choices=("e5", "tfidf", "fake"), default=None)
    serve.add_argument("--demo", action="store_true", help="use the public synthetic fixture")
    serve.add_argument("--demo-fixture", type=Path, default=None, help="synthetic fixture path (only with --demo)")
    serve.add_argument("--zotero-url", default=os.environ.get("ATLAS_ZOTERO_URL", "http://127.0.0.1:23119/api"))
    serve.add_argument("--library-id", type=int, default=0)
    serve.add_argument("--watch-parent-stdin", action="store_true", help="shutdown when the plugin parent closes stdin")
    return parser


def serve(args: argparse.Namespace) -> int:
    if args.embedding_provider == "fake" and not args.demo:
        raise SystemExit("--embedding-provider fake is allowed only with --demo")
    if args.demo_fixture is not None and not args.demo:
        raise SystemExit("--demo-fixture requires --demo")
    if args.library_id != 0:
        raise SystemExit("only Zotero personal library 0 is supported")
    if not 5 <= args.poll_interval <= 300:
        raise SystemExit("--poll-interval must be between 5 and 300 seconds")
    try:
        store = SQLiteStore(args.data_dir)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    client = ZoteroClient(args.zotero_url)
    manager = SyncManager(store, client, cache_dir=args.data_dir / "models", embedding_provider=args.embedding_provider, poll_interval=args.poll_interval, library_id=args.library_id, demo_snapshot=_load_demo(args.demo_fixture) if args.demo else None)
    server = AtlasHTTPServer(("127.0.0.1", args.port), manager, web_root=args.web_root, launch_id=args.launch_id or os.urandom(12).hex(), parent_origin=args.parent_origin)
    bootstrap = {"port": server.server_port, "token": server.token, "pid": os.getpid(), "launchId": server.launch_id}
    sys.stdout.write(json.dumps(bootstrap, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    manager.start_watcher()
    stop = threading.Event()
    def _signal(_signum: int, _frame: Any) -> None:
        stop.set()
        # ``BaseServer.shutdown`` waits for serve_forever to leave its loop and
        # therefore must be called from a different thread than the signal
        # handler thread (the main thread running serve_forever).
        threading.Thread(target=server.shutdown, name="atlas-shutdown", daemon=True).start()
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    if args.watch_parent_stdin:
        def _watch_parent() -> None:
            try:
                sys.stdin.buffer.read()
            except (OSError, ValueError):
                return
            if stop.is_set():
                return
            stop.set()
            threading.Thread(target=server.shutdown, name="atlas-parent-shutdown", daemon=True).start()
            def _watchdog() -> None:
                time.sleep(5)
                if server._BaseServer__is_shut_down.is_set() is False:
                    os._exit(0)
            threading.Thread(target=_watchdog, name="atlas-parent-watchdog", daemon=True).start()
        threading.Thread(target=_watch_parent, name="atlas-parent-watch", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        manager.stop_watcher()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return serve(args)
    return 2

