#!/usr/bin/env python3
"""Block confidential names from ever entering the public repo.

The repo is public; the corpus it indexes is not. This scans content, paths and
commit messages for terms that must never be published. The term list itself is
never committed -- it is read from SCAN_DENYLIST (a path, or the literal list)
so CI can enforce the rule via a repository secret without stating it.

Usage:
    scan_forbidden.py --staged          # pre-commit: staged content + paths
    scan_forbidden.py --message FILE    # commit-msg: the message
    scan_forbidden.py --range A..B      # CI: every commit in a push
    scan_forbidden.py --paths P [P...]  # ad-hoc: specific files
    scan_forbidden.py --stdin           # pipe anything in

Exit 0 clean, 1 on a hit, 2 on a usage/config error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DENYLIST = Path(__file__).resolve().parent.parent / "private" / "denylist.txt"

# Files whose bytes are meaningless to scan. Their *paths* are still checked.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mov", ".xlsx", ".xls",
}

MODES = {"ci", "cs", "word"}

# .gitignore is advisory: `git add -f` walks straight through it. These are the
# backstop, and unlike .gitignore they cannot be overridden from the command line.
NEVER_COMMIT_SUFFIXES = {
    ".pdf", ".xlsx", ".xls", ".docx", ".pptx", ".key", ".pem", ".p12",
}
MAX_BLOB_BYTES = 5_000_000


@dataclass(frozen=True)
class Rule:
    term: str
    mode: str
    pattern: re.Pattern

    @staticmethod
    def build(term: str, mode: str) -> "Rule":
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r} for term {term!r}")
        esc = re.escape(term)
        if mode == "ci":
            pattern = re.compile(esc, re.IGNORECASE)
        elif mode == "word":
            pattern = re.compile(rf"(?<!\w){esc}(?!\w)", re.IGNORECASE)
        else:  # cs
            pattern = re.compile(rf"(?<!\w){esc}(?!\w)")
        return Rule(term, mode, pattern)


def load_rules() -> list[Rule]:
    """Read rules from $SCAN_DENYLIST (path or literal) or the default path."""
    raw = os.environ.get("SCAN_DENYLIST", "")
    if raw and not Path(raw).exists() and ("\n" in raw or "\t" in raw):
        text = raw                      # CI passes the list itself via a secret
    else:
        path = Path(raw) if raw else DEFAULT_DENYLIST
        if not path.exists():
            sys.stderr.write(
                f"scan_forbidden: no denylist at {path}.\n"
                "  local: clone arch-ive-private to ./private/\n"
                "  CI:    set the SCAN_DENYLIST secret\n"
                "Refusing to pass without one.\n"
            )
            raise SystemExit(2)
        text = path.read_text(encoding="utf-8")

    rules: list[Rule] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if line.lstrip().startswith("#") else line.rstrip()
        if not line.strip():
            continue
        parts = [p for p in line.split("\t") if p.strip()]
        term = parts[0].strip()
        mode = parts[1].strip() if len(parts) > 1 else "ci"
        if term:
            rules.append(Rule.build(term, mode))
    if not rules:
        sys.stderr.write("scan_forbidden: denylist is empty; refusing to pass.\n")
        raise SystemExit(2)
    return rules


def scan_text(text: str, rules: list[Rule], origin: str) -> list[str]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule in rules:
            if rule.pattern.search(line):
                excerpt = line.strip()[:110]
                hits.append(f"  {origin}:{lineno}  [{rule.mode}] {rule.term!r}  ->  {excerpt}")
    return hits


def scan_path_name(path: str, rules: list[Rule]) -> list[str]:
    return [
        f"  {path}  [filename] {rule.term!r}"
        for rule in rules
        if rule.pattern.search(path)
    ]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def staged_paths() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def staged_blob_bytes(path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "show", f":{path}"], capture_output=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        return b""


def check_staged_policy(path: str) -> list[str]:
    """Source material and large blobs must never enter a public repo."""
    problems = []
    suffix = Path(path).suffix.lower()
    if suffix in NEVER_COMMIT_SUFFIXES:
        problems.append(f"  {path}  [policy] '{suffix}' is source material and is never committed")
    size = len(staged_blob_bytes(path))
    if size > MAX_BLOB_BYTES:
        problems.append(f"  {path}  [policy] {size / 1e6:.1f} MB exceeds the {MAX_BLOB_BYTES / 1e6:.0f} MB limit")
    return problems


def staged_blob(path: str) -> str:
    try:
        return subprocess.run(
            ["git", "show", f":{path}"], capture_output=True, check=True
        ).stdout.decode("utf-8", "replace")
    except subprocess.CalledProcessError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--message", metavar="FILE")
    g.add_argument("--range", metavar="A..B")
    g.add_argument("--paths", nargs="+", metavar="PATH")
    g.add_argument("--stdin", action="store_true")
    args = ap.parse_args()

    rules = load_rules()
    hits: list[str] = []

    if args.staged:
        for path in staged_paths():
            hits += check_staged_policy(path)
            hits += scan_path_name(path, rules)
            if Path(path).suffix.lower() in BINARY_SUFFIXES:
                continue
            hits += scan_text(staged_blob(path), rules, path)

    elif args.message:
        hits += scan_text(Path(args.message).read_text(encoding="utf-8", errors="replace"),
                          rules, "commit-message")

    elif args.range:
        hits += scan_text(git("log", "--format=%B%n%an%n%ae", args.range), rules, "commit-log")
        hits += scan_text(git("diff", args.range), rules, "diff")

    elif args.paths:
        for path in args.paths:
            hits += scan_path_name(path, rules)
            p = Path(path)
            if p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES:
                hits += scan_text(p.read_text(encoding="utf-8", errors="replace"), rules, path)

    else:
        hits += scan_text(sys.stdin.read(), rules, "stdin")

    if hits:
        sys.stderr.write(
            "\n\033[31mBLOCKED: this must not enter a public repo.\033[0m\n"
            "This repo is public. These must not be committed:\n\n"
            + "\n".join(hits[:40])
            + (f"\n  ... and {len(hits) - 40} more" if len(hits) > 40 else "")
            + "\n\nUse a document slug or an organisation id instead. Real names\n"
              "belong in private/organisations.yaml and the database only.\n\n"
        )
        return 1

    print(f"scan_forbidden: clean ({len(rules)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
