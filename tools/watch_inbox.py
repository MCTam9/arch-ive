"""The inbox daemon: a watchdog observer plus a reconciliation sweep.

FSEvents (macOS) is used only to NOMINATE a sweep -- it is known to drop
notifications under load, and files land in the inbox while this daemon is
not even running. `tools.ingest_inbox.run_once` is the source of truth: it
re-derives everything from what is actually on disk and in the database, so
a missed or duplicated notification here is harmless. This module never
decides anything about a document itself.

Runs a sweep on start and on a timer (SWEEP_INTERVAL_SECONDS, default 300s).
"""
from __future__ import annotations

import argparse
import os
import signal
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from tools import ingest_inbox


def _sweep_interval() -> float:
    try:
        return float(os.environ.get("SWEEP_INTERVAL_SECONDS", "300"))
    except ValueError:
        return 300.0


class _Nominator(FileSystemEventHandler):
    """Any filesystem event under the inbox just wakes the sweep loop early.
    It never touches a file itself -- the sweep is what decides anything."""

    def __init__(self, wake: threading.Event):
        self._wake = wake

    def on_any_event(self, event) -> None:  # noqa: ARG002 - event content unused by design
        self._wake.set()


def _sweep(dry_run: bool) -> None:
    try:
        ingest_inbox.run_once(dry_run=dry_run, force_sha=None)
    except Exception as exc:  # noqa: BLE001 - a bad sweep must not kill the daemon
        print(f"watch_inbox: sweep failed: {type(exc).__name__}: {exc}")


def run(*, dry_run: bool = False) -> None:
    inbox_dir = ingest_inbox._inbox_dir()
    ingest_inbox.ensure_state_dirs(inbox_dir)

    interval = _sweep_interval()
    stop = threading.Event()
    wake = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ARG001
        print(f"watch_inbox: received signal {signum}, stopping")
        stop.set()
        wake.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    observer = Observer()
    observer.schedule(_Nominator(wake), str(inbox_dir), recursive=False)
    observer.start()

    print(f"watch_inbox: watching {inbox_dir}, sweep every {interval:.0f}s")
    try:
        _sweep(dry_run)  # always sweep once on start: the daemon may have been off for a while
        wake.clear()  # drop any events our own startup sweep just generated
        while not stop.is_set():
            wake.wait(timeout=interval)
            if stop.is_set():
                break
            # debounce: let a burst of writes/renames settle before sweeping
            time.sleep(min(2.0, ingest_inbox.STABILITY_SECONDS))
            wake.clear()
            _sweep(dry_run)
            wake.clear()  # drop events caused by our own moves during the sweep
    finally:
        observer.stop()
        observer.join(timeout=5)
        print("watch_inbox: stopped")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.watch_inbox")
    ap.add_argument("--dry-run", action="store_true", help="sweep in report-only mode")
    args = ap.parse_args(argv)
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
