"""Read `.env` without sourcing it.

`. ./.env` looks like the obvious thing and is a trap: a Neon DSN contains
`&channel_binding=require`, and `&` is shell syntax, so sourcing silently
backgrounds half the line and sets nothing. That cost a debugging session
during the Neon migration. Separately, SOURCE_DIR / RCLONE_CONFIG /
RCLONE_REMOTE simply were not in `.env` for a long time, and every tool that
reads them degrades quietly to a local-only path when they are missing -- so
"it ran and printed nothing alarming" was not evidence that anything reached
R2.

Parse, do not source. Values already in the environment win, so an explicit
`FOO=bar python -m tools.x` still overrides the file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_env(path: str | Path = ".env") -> int:
    """Set any variable in `path` that is not already set. Returns how many."""
    p = Path(path).expanduser()
    if not p.is_file():
        return 0
    loaded = 0
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def require(*names: str) -> tuple[str, ...]:
    """Fetch variables, failing with the names rather than a stack trace."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"missing from the environment and .env: {', '.join(missing)}")
    return tuple(os.environ[n] for n in names)
