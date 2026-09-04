#!/usr/bin/env python3
"""Structural check on the WAT layering described in CLAUDE.md.

Workflows are the instructions, tools are the execution, and the agent sits
between them. That only holds if it is checked: the layer that rots first is
the workflow layer, because a tool that works is its own reward and the SOP
that explains when to run it is not. This repo drifted exactly that way --
whole areas of work (deployment, database provisioning) existed as tools and
ad-hoc shell with no SOP at all, so the next run had to be reconstructed from
memory each time.

Five checks, all mechanical:

  1. every runnable tool is named by at least one workflow
  2. every extractor appears in the registry in workflows/add_extractor.md
  3. no orphan workflows -- an SOP that names no file is prose, not a procedure
  4. execution stays in the tool layer -- no runnable script in a stray place
  5. secrets stay in the sanctioned env files

Run it directly, or let the pre-commit hook and CI run it:

    python3 scripts/check_wat.py [--quiet]

Exit 0 clean, 1 on any violation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories allowed to hold a runnable script, and why.
#   tools/       the execution layer proper
#   extractors/  registered modules the stage runner dispatches to
#   scripts/     repo gates -- these guard the repo, they do not process corpus
#   web/scripts/ build-time helpers for the Next.js app
#   tests/       pytest
RUNNABLE_DIRS = ("tools", "extractors", "scripts", "web/scripts", "tests")

# Env files that may hold secrets. CLAUDE.md says .env and nowhere else; the
# web app's are listed explicitly because Next.js reads its own, which is a
# real deviation and better recorded here than discovered later.
ALLOWED_ENV_FILES = {".env", ".env.example", "web/.env", "web/.env.example", "web/.env.local"}

MAIN_BLOCK = re.compile(r"^if __name__ == [\"']__main__[\"']:", re.M)


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _workflow_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted((ROOT / "workflows").glob("*.md"))}


def _is_runnable(path: Path) -> bool:
    try:
        return bool(MAIN_BLOCK.search(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def _entry_points() -> list[Path]:
    """Everything a person or the agent can actually invoke.

    Python modules with a __main__ block, plus shell and node scripts in the
    script directories -- `scripts/load_neon.sh` and
    `web/scripts/upload_page_images.mjs` are as much a procedure as any tool,
    and both were run repeatedly with no SOP behind them.
    """
    found: list[Path] = []
    for group in ("tools", "extractors", "scripts", "web/scripts"):
        d = ROOT / group
        if not d.is_dir():
            continue
        for path in sorted(d.iterdir()):
            if not path.is_file() or path.name == "__init__.py":
                continue
            if path.suffix == ".py" and _is_runnable(path):
                found.append(path)
            elif path.suffix in (".sh", ".mjs", ".js"):
                found.append(path)
    return found


def check_tools_have_workflows(workflows: dict[str, str], fail) -> None:
    for path in _entry_points():
        group = path.parent.relative_to(ROOT).as_posix()
        needles = (path.name, f"{group.replace('/', '.')}.{path.stem}", f"{group}/{path.stem}")
        if not any(n in text for text in workflows.values() for n in needles):
            fail(
                f"{_rel(path)} can be run but no workflow says when or why. "
                f"Add it to an existing SOP in workflows/, or write one."
            )


def check_extractors_are_registered(workflows: dict[str, str], fail) -> None:
    registry = workflows.get("add_extractor.md")
    if registry is None:
        fail("workflows/add_extractor.md is missing -- it is the extractor registry")
        return
    for path in sorted((ROOT / "extractors").glob("*.py")):
        if path.name == "__init__.py":
            continue
        if path.name not in registry:
            fail(
                f"extractors/{path.name} is not listed in workflows/add_extractor.md. "
                f"The registry is how the agent knows which shapes are already handled."
            )


def check_no_orphan_workflows(workflows: dict[str, str], fail) -> None:
    referenced = re.compile(r"\b(?:tools|extractors|scripts|db|web)/[\w./-]+")
    for name, text in workflows.items():
        if not referenced.search(text):
            fail(
                f"workflows/{name} names no file under tools/, extractors/, scripts/, db/ or web/. "
                f"A workflow that points at no tool is prose, not a procedure."
            )


def check_execution_stays_in_the_tool_layer(fail) -> None:
    skip_parts = {".git", ".venv", "node_modules", "__pycache__", ".next", ".tmp", "private", "inbox"}
    for path in ROOT.rglob("*.py"):
        rel = _rel(path)
        if set(Path(rel).parts) & skip_parts:
            continue
        if any(rel.startswith(d + "/") for d in RUNNABLE_DIRS):
            continue
        if _is_runnable(path):
            fail(
                f"{rel} is a runnable script outside the tool layer. "
                f"Execution belongs in {', '.join(d + '/' for d in RUNNABLE_DIRS)}."
            )


def check_secrets_stay_in_env(fail) -> None:
    skip_parts = {".git", ".venv", "node_modules", "__pycache__", ".next", ".tmp"}
    for path in ROOT.rglob(".env*"):
        rel = _rel(path)
        if set(Path(rel).parts) & skip_parts or not path.is_file():
            continue
        if rel not in ALLOWED_ENV_FILES:
            fail(
                f"{rel} is an env file outside the sanctioned set. "
                f"Secrets live in .env (see CLAUDE.md); add it to ALLOWED_ENV_FILES here "
                f"only with a reason, and make sure .gitignore covers it."
            )


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    problems: list[str] = []

    def fail(msg: str) -> None:
        problems.append(msg)

    workflows = _workflow_texts()
    if not workflows:
        print("check_wat: no workflows/ found -- the instruction layer is missing", file=sys.stderr)
        return 1

    check_tools_have_workflows(workflows, fail)
    check_extractors_are_registered(workflows, fail)
    check_no_orphan_workflows(workflows, fail)
    check_execution_stays_in_the_tool_layer(fail)
    check_secrets_stay_in_env(fail)

    if problems:
        print(f"check_wat: {len(problems)} WAT violation(s)", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nSee CLAUDE.md. Workflows are the instruction layer; a tool without one "
            "means the next run gets reconstructed from memory instead of read.",
            file=sys.stderr,
        )
        return 1

    if not quiet:
        n_tools = sum(1 for p in (ROOT / "tools").glob("*.py") if p.name != "__init__.py")
        n_ext = sum(1 for p in (ROOT / "extractors").glob("*.py") if p.name != "__init__.py")
        print(f"check_wat: clean ({len(workflows)} workflows, {n_tools} tools, {n_ext} extractors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
