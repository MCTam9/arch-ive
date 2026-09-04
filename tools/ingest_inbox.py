"""The inbox orchestrator: `python -m tools.ingest_inbox --once [--dry-run] [--force SHA]`.

The inbox is a visible state machine on disk (see CONTRACT.md):

    inbox/                 drop zone
      _defaults.yaml       optional batch metadata applied to everything dropped
      _processing/         moved here the instant a file is picked up
      _done/YYYY-MM-DD/    originals after success
      _failed/             original + <name>.error.json naming the stage that failed
      _duplicates/         content hash already in the database
      _review/             loaded, but classification confidence was low

Files are MOVED, never deleted. Identity is the SHA-256 of content, not the
filename -- some of what lands here is a re-export of something we already
have, and some is a new revision of a document we already catalogued under
the same slug.

Never print an original filename: this module logs job ids, slugs and sha
prefixes only (CONTRACT.md ground rules -- the repo is public, the corpus is
not, and stdout has a way of ending up pasted into an issue).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools import db, pipeline

CONFIDENCE_THRESHOLD = 0.6
STATE_DIRS = ("_processing", "_done", "_failed", "_duplicates", "_review")
STABILITY_STATE_FILENAME = ".ingest_stability.json"
_IGNORED_SUFFIXES = (".crdownload", ".part", ".download")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


STABILITY_SECONDS = _env_float("STABILITY_SECONDS", 5.0)


def _inbox_dir() -> Path:
    return Path(os.environ.get("INBOX_DIR", "./inbox")).expanduser().resolve()


def _private_dir() -> Path:
    return Path(os.environ.get("PRIVATE_DIR", "./private")).expanduser().resolve()


def _lock_path() -> Path:
    return Path(os.environ.get("INGEST_LOCK", ".ingest.lock")).expanduser().resolve()


def ensure_state_dirs(inbox_dir: Path) -> None:
    for sub in STATE_DIRS:
        (inbox_dir / sub).mkdir(parents=True, exist_ok=True)


# ── ignore rules ─────────────────────────────────────────────────────────


def should_ignore(name: str) -> bool:
    """.DS_Store, ._AppleDouble, in-progress downloads, and anything starting
    with _ or . (batch metadata, the state-machine directories, dotfiles)."""
    if name.startswith(".") or name.startswith("_"):
        return True
    if name.endswith(_IGNORED_SUFFIXES):
        return True
    return False


def scan_candidates(inbox_dir: Path) -> list[Path]:
    """Files sitting directly in the inbox root that are eligible for pickup."""
    out = []
    for entry in sorted(inbox_dir.iterdir()):
        if entry.is_dir() or should_ignore(entry.name):
            continue
        if entry.name.endswith(".meta.yaml"):
            continue
        out.append(entry)
    return out


# ── stability check ──────────────────────────────────────────────────────
#
# A file is picked up only once size AND mtime have been unchanged for
# STABILITY_SECONDS. Pure and testable without real sleeps: `now` is passed
# in rather than read from the clock inside these two functions.


@dataclass
class StabilitySample:
    size: int
    mtime: float
    first_seen: float


def stability_update(
    prev: StabilitySample | None, size: int, mtime: float, now: float
) -> StabilitySample:
    if prev is None or prev.size != size or prev.mtime != mtime:
        return StabilitySample(size=size, mtime=mtime, first_seen=now)
    return prev


def is_stable(sample: StabilitySample, now: float, stability_seconds: float) -> bool:
    return (now - sample.first_seen) >= stability_seconds


def _load_stability(state_path: Path) -> dict[str, StabilitySample]:
    if not state_path.exists():
        return {}
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    for name, v in raw.items():
        try:
            out[name] = StabilitySample(size=v["size"], mtime=v["mtime"], first_seen=v["first_seen"])
        except (KeyError, TypeError):
            continue
    return out


def _save_stability(state_path: Path, data: dict[str, StabilitySample]) -> None:
    raw = {k: {"size": v.size, "mtime": v.mtime, "first_seen": v.first_seen} for k, v in data.items()}
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(state_path)


def stable_candidates(inbox_dir: Path, now: float | None = None) -> list[Path]:
    """Candidates whose (size, mtime) has held steady for STABILITY_SECONDS.

    Persists what it has seen so far in a hidden state file (ignored by the
    scanner because it starts with '.'), so stability accumulates across
    repeated sweeps rather than resetting every run.
    """
    now = time.time() if now is None else now
    state_path = inbox_dir / STABILITY_STATE_FILENAME
    tracker = _load_stability(state_path)
    updated: dict[str, StabilitySample] = {}
    stable: list[Path] = []
    for f in scan_candidates(inbox_dir):
        st = f.stat()
        sample = stability_update(tracker.get(f.name), st.st_size, st.st_mtime, now)
        updated[f.name] = sample
        if is_stable(sample, now, STABILITY_SECONDS):
            stable.append(f)
    _save_stability(state_path, updated)
    return stable


# ── hashing ───────────────────────────────────────────────────────────────


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streams the file so a 194 MB original is never read into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# ── a tiny hand-rolled YAML reader ──────────────────────────────────────
#
# PyYAML is not in requirements.txt and this repo prefers stdlib. The files
# we need to read (private/documents.yaml, inbox/_defaults.yaml, sidecar
# *.meta.yaml) are plain block-style YAML: nested mappings, sequences of
# mappings, inline flow lists, quoted/bare scalars, and one folded (`>`)
# multi-line scalar. That subset is what this parses -- no anchors, no
# multi-document streams, no flow mappings.


def _strip_comment(line: str) -> str:
    out = []
    in_s = in_d = False
    for ch in line:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _tokenize(text: str) -> list[tuple[int, str]]:
    tokens = []
    for raw in text.split("\n"):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        tokens.append((indent, stripped.strip()))
    return tokens


def _split_flow_list(inner: str) -> list[str]:
    parts, cur, in_q = [], [], None
    for ch in inner:
        if in_q:
            cur.append(ch)
            if ch == in_q:
                in_q = None
            continue
        if ch in "\"'":
            in_q = ch
            cur.append(ch)
            continue
        if ch == ",":
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?\d+\.\d+")


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [] if not inner else [_parse_scalar(p) for p in _split_flow_list(inner)]
    if _INT_RE.fullmatch(s):
        return int(s)
    if _FLOAT_RE.fullmatch(s):
        return float(s)
    return s


def _parse_multiline(tokens: list[tuple[int, str]], idx: int, indent: int, fold: bool):
    parts = []
    while idx < len(tokens) and tokens[idx][0] > indent:
        parts.append(tokens[idx][1])
        idx += 1
    return (" " if fold else "\n").join(parts), idx


def _parse_mapping(tokens: list[tuple[int, str]], idx: int, indent: int):
    result: dict[str, Any] = {}
    while idx < len(tokens):
        cur_indent, content = tokens[idx]
        if cur_indent != indent or content.startswith("- ") or ":" not in content:
            break
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        idx += 1
        if rest in (">", "|"):
            value, idx = _parse_multiline(tokens, idx, indent, fold=(rest == ">"))
        elif rest == "":
            if idx < len(tokens) and tokens[idx][0] > indent:
                value, idx = _parse_block(tokens, idx, tokens[idx][0])
            else:
                value = None
        else:
            value = _parse_scalar(rest)
        result[key] = value
    return result, idx


def _parse_sequence(tokens: list[tuple[int, str]], idx: int, indent: int):
    result: list[Any] = []
    while idx < len(tokens):
        cur_indent, content = tokens[idx]
        if cur_indent != indent or not content.startswith("- "):
            break
        item = content[2:]
        if item == "":
            idx += 1
            value, idx = _parse_block(tokens, idx, indent + 2)
        elif ":" in item and not item.lstrip().startswith(("[", '"', "'")):
            # first key of a block-mapping sequence item; splice a synthetic
            # token so the mapping parser sees it at the item's own indent
            spliced = list(tokens)
            spliced[idx] = (indent + 2, item)
            value, idx = _parse_mapping(spliced, idx, indent + 2)
        else:
            value = _parse_scalar(item)
            idx += 1
        result.append(value)
    return result, idx


def _parse_block(tokens: list[tuple[int, str]], idx: int, indent: int):
    if idx >= len(tokens) or tokens[idx][0] < indent:
        return None, idx
    if tokens[idx][1].startswith("- "):
        return _parse_sequence(tokens, idx, tokens[idx][0])
    return _parse_mapping(tokens, idx, tokens[idx][0])


def parse_yaml(text: str) -> Any:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    value, _ = _parse_block(tokens, 0, tokens[0][0])
    return value


# ── metadata resolution ──────────────────────────────────────────────────


def load_documents_yaml(private_dir: Path) -> dict[str, dict]:
    """filename -> merged metadata, from private/documents.yaml.

    Real organisation/person names may appear in the returned dict's values
    (that file is the one sanctioned place for them); callers must not log
    those values, only pass them through to the database.
    """
    path = private_dir / "documents.yaml"
    if not path.exists():
        return {}
    data = parse_yaml(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    out: dict[str, dict] = {}
    for entry in data.get("documents") or []:
        file_rel = entry.get("file")
        if not file_rel:
            continue
        merged = {**defaults, **entry}
        merged.pop("file", None)
        out[Path(file_rel).name] = merged
    return out


def resolve_static_meta(inbox_dir: Path, known_docs: dict[str, dict], path: Path) -> dict:
    """Merge, lowest to highest precedence: _defaults.yaml, a documents.yaml
    match by filename, then a myfile.meta.yaml sidecar next to this file."""
    meta: dict[str, Any] = {}
    defaults_path = inbox_dir / "_defaults.yaml"
    if defaults_path.exists():
        meta.update(parse_yaml(defaults_path.read_text(encoding="utf-8")) or {})
    if path.name in known_docs:
        meta.update(known_docs[path.name])
    sidecar_path = inbox_dir / path.with_suffix(".meta.yaml").name
    if sidecar_path.exists():
        meta.update(parse_yaml(sidecar_path.read_text(encoding="utf-8")) or {})
    return meta


# ── lockfile ──────────────────────────────────────────────────────────────


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    except OSError:
        return False
    return True


def acquire_lock(lock_path: Path) -> None:
    """Raises RuntimeError if another live run holds the lock. A lock left
    behind by a dead process (stale PID) is reclaimed automatically."""
    if lock_path.exists():
        pid = None
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
        if pid is not None and _pid_alive(pid):
            raise RuntimeError(f"ingest already running (pid {pid}); lock at {lock_path}")
        lock_path.unlink(missing_ok=True)  # stale: dead pid, or unreadable
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise RuntimeError(f"lost the race to acquire {lock_path}; another run just started") from None
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))


def release_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


# ── per-file moves ───────────────────────────────────────────────────────


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix, i = dest.stem, dest.suffix, 1
    while True:
        candidate = dest.with_name(f"{stem}__{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir / src.name)
    src.rename(dest)
    return dest


def _describe(path: Path) -> str:
    """A log-safe stand-in for a filename: extension and size only."""
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    return f"{path.suffix.lower() or '(no ext)'} file, {size} bytes"


# ── stage wrappers ──────────────────────────────────────────────────────
#
# Each wraps one pipeline.run_stage call. run_stage returns only a bool, so
# after it we re-read that stage's recorded `stats` from ingest_stage_run --
# this makes the decision resumable too: a rerun that short-circuits inside
# run_stage (prior status already 'ok') still gets the same stats back.


class StageFailure(Exception):
    def __init__(self, stage: str, error: str):
        self.stage = stage
        self.error = error
        super().__init__(f"{stage}: {error}")


def _stage_stats(job_id: str, stage: pipeline.State) -> dict:
    with db.connect() as conn:
        row = db.one(
            conn,
            "SELECT stats FROM ingest_stage_run WHERE job_id = %s AND stage = %s",
            (job_id, stage.value),
        )
        return (row or {}).get("stats") or {}


def _run(job_id: str, stage: pipeline.State, fn, *, force: bool) -> dict:
    try:
        pipeline.run_stage(job_id, stage, fn, force=force)
    except Exception as exc:  # noqa: BLE001 - translated into a StageFailure for the caller
        raise StageFailure(stage.value, f"{type(exc).__name__}: {exc}") from exc
    return _stage_stats(job_id, stage)


def _run_hashed(job_id: str, path: Path, *, force: bool) -> str:
    def fn(conn):
        sha = sha256_of_file(path)
        size = path.stat().st_size
        conn.execute(
            "UPDATE ingest_job SET sha256 = %s, size_bytes = %s WHERE id = %s",
            (sha, size, job_id),
        )
        return {"sha256": sha, "size_bytes": size}

    return _run(job_id, pipeline.State.HASHED, fn, force=force)["sha256"]


def _run_deduped(job_id: str, sha: str, *, force: bool) -> str | None:
    def fn(conn):
        # sha256 is UNIQUE on source_document regardless of revision status --
        # any match at all means these exact bytes are already in the corpus.
        # Exclude a match on this job's OWN document: with --force, every
        # stage (this one included) reruns even though it already succeeded,
        # and a fully-processed job's document legitimately shares its sha --
        # that is not a new duplicate, it is this job being redone.
        job_row = db.one(conn, "SELECT document_id FROM ingest_job WHERE id = %s", (job_id,))
        own_document_id = job_row["document_id"] if job_row else None
        existing = db.one(conn, "SELECT id, slug FROM source_document WHERE sha256 = %s", (sha,))
        if existing and str(existing["id"]) != str(own_document_id):
            return {"duplicate_of_slug": existing["slug"]}
        return {"duplicate_of_slug": None}

    return _run(job_id, pipeline.State.DEDUPED, fn, force=force).get("duplicate_of_slug")


def _run_classified(job_id: str, path: Path, *, force: bool) -> tuple[str, float]:
    def fn(conn):
        from tools.classify_document import classify

        doc_kind, confidence = classify(path)
        conn.execute(
            "UPDATE ingest_job SET doc_kind_guess = %s, classification_confidence = %s WHERE id = %s",
            (doc_kind, confidence, job_id),
        )
        return {"doc_kind": doc_kind, "confidence": confidence}

    stats = _run(job_id, pipeline.State.CLASSIFIED, fn, force=force)
    return stats["doc_kind"], stats["confidence"]


def _run_registered(
    job_id: str, path: Path, sha: str, slug: str, doc_kind: str, meta: dict, *, force: bool
) -> str:
    def fn(conn):
        from tools.ingest_document import register_document

        document_id = register_document(
            conn, path=path, sha256=sha, slug=slug, doc_kind=doc_kind, meta=meta
        )
        conn.execute("UPDATE ingest_job SET document_id = %s WHERE id = %s", (document_id, job_id))
        return {"document_id": str(document_id)}

    return _run(job_id, pipeline.State.REGISTERED, fn, force=force)["document_id"]


def _run_archived(job_id: str, path: Path, sha: str, slug: str, *, force: bool) -> str | None:
    def fn(conn):
        from tools.archive_original import archive

        r2_key = archive(path, sha, slug)
        conn.execute(
            "UPDATE source_document SET r2_key = %s, archived_at = now() WHERE sha256 = %s",
            (r2_key, sha),
        )
        return {"r2_key": r2_key}

    return _run(job_id, pipeline.State.ARCHIVED, fn, force=force).get("r2_key")


def _run_pages(job_id: str, document_id: str, path: Path, *, force: bool) -> int:
    def fn(conn):
        from tools.ingest_document import extract_pages

        count = extract_pages(conn, document_id, path)
        return {"page_count": count}

    return _run(job_id, pipeline.State.PAGES, fn, force=force).get("page_count", 0)


def _run_structured(job_id: str, document_id: str, path: Path, *, force: bool) -> int:
    def fn(conn):
        from tools.build_structure import build_structure

        count = build_structure(conn, document_id, path)
        return {"node_count": count}

    return _run(job_id, pipeline.State.STRUCTURED, fn, force=force).get("node_count", 0)


def _run_extracted(
    job_id: str, document_id: str, slug: str, doc_kind: str, path: Path, page_count: int, meta: dict, *, force: bool
) -> None:
    def fn(conn):
        pipeline.load_extractors()
        extractor = pipeline.for_doc_kind(doc_kind)
        pages = db.all_rows(
            conn, "SELECT * FROM source_page WHERE document_id = %s ORDER BY page_index", (document_id,)
        )
        ctx = pipeline.DocumentContext(
            document_id=str(document_id),
            slug=slug,
            path=path,
            doc_kind=doc_kind,
            page_count=page_count,
            pages=pages,
            meta=meta,
        )
        extraction = extractor.extract(ctx)

        from tools.write_extraction import write_extraction

        counts = write_extraction(conn, document_id, extraction)
        return counts if isinstance(counts, dict) else {"result": counts}

    _run(job_id, pipeline.State.EXTRACTED, fn, force=force)


def _run_enriched(job_id: str, document_id: str, *, force: bool) -> None:
    def fn(conn):
        try:
            from tools.enrich_document import enrich
        except ImportError as exc:
            # No cross-module signature for this stage is defined in
            # CONTRACT.md yet. Treat "not built" as nothing-to-do rather than
            # failing a document that is otherwise fully extracted.
            raise pipeline.StageSkipped(f"enrich module unavailable: {exc}") from exc
        return enrich(conn, document_id) or {}

    _run(job_id, pipeline.State.ENRICHED, fn, force=force)


def _run_embedded(job_id: str, document_id: str, *, force: bool) -> None:
    def fn(conn):
        from tools.embed_chunks import embed_pending, model_available

        if not model_available():
            raise pipeline.StageSkipped(
                "sentence-transformers not installed; no embeddings written"
            )
        count = embed_pending(conn, document_id)
        return {"embedded": count}

    _run(job_id, pipeline.State.EMBEDDED, fn, force=force)


# ── job bookkeeping ──────────────────────────────────────────────────────


def _find_or_create_job(processing_path: Path, original_filename: str) -> str:
    """Keyed on the file's path inside _processing/, which stays fixed for
    the whole run -- that is what makes crash-resume work: a leftover file
    found there on the next sweep resolves to the SAME job id, so run_stage's
    per-stage idempotency picks up exactly where it left off."""
    with db.connect() as conn:
        row = db.one(
            conn,
            "SELECT id FROM ingest_job WHERE source_path = %s ORDER BY discovered_at DESC LIMIT 1",
            (str(processing_path),),
        )
        if row:
            return row["id"]
        job_id = db.insert_returning_id(
            conn,
            "ingest_job",
            {
                "source_path": str(processing_path),
                "original_filename": original_filename,
                "state": "discovered",
            },
        )
        conn.commit()
        return job_id


def _finalize_duplicate(inbox_dir: Path, job_id: str, processing_path: Path, dup_slug: str) -> None:
    dest = _move(processing_path, inbox_dir / "_duplicates")
    print(f"duplicate: job={job_id} matches existing slug={dup_slug!r} -> {dest.relative_to(inbox_dir)}")


def _finalize_review(inbox_dir: Path, job_id: str, processing_path: Path, slug: str) -> None:
    dest = _move(processing_path, inbox_dir / "_review")
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_job SET state = 'needs_review', updated_at = now() WHERE id = %s", (job_id,)
        )
        conn.commit()
    print(f"needs_review: job={job_id} slug={slug!r} -> {dest.relative_to(inbox_dir)}")


def _finalize_done(inbox_dir: Path, job_id: str, processing_path: Path, slug: str) -> None:
    day_dir = inbox_dir / "_done" / dt.date.today().isoformat()
    dest = _move(processing_path, day_dir)
    with db.connect() as conn:
        conn.execute("UPDATE ingest_job SET state = 'done', updated_at = now() WHERE id = %s", (job_id,))
        conn.commit()
    print(f"done: job={job_id} slug={slug!r} -> {dest.relative_to(inbox_dir)}")


def _handle_failure(inbox_dir: Path, job_id: str, processing_path: Path, stage: str, error: str) -> None:
    dest = _move(processing_path, inbox_dir / "_failed")
    error_path = dest.with_name(dest.name + ".error.json")
    error_path.write_text(
        json.dumps({"job_id": str(job_id), "stage": stage, "error": error}, indent=2),
        encoding="utf-8",
    )
    print(f"failed: job={job_id} stage={stage} -> {dest.relative_to(inbox_dir)}")


# ── the per-file pipeline ────────────────────────────────────────────────


def _ingest(
    inbox_dir: Path, processing_path: Path, known_docs: dict[str, dict], *, force: bool
) -> None:
    original_filename = processing_path.name
    job_id = _find_or_create_job(processing_path, original_filename)
    static_meta = resolve_static_meta(inbox_dir, known_docs, processing_path)

    try:
        sha = _run_hashed(job_id, processing_path, force=force)
        dup_slug = _run_deduped(job_id, sha, force=force)
        if dup_slug is not None:
            _finalize_duplicate(inbox_dir, job_id, processing_path, dup_slug)
            return

        doc_kind_guess, confidence = _run_classified(job_id, processing_path, force=force)
        slug = static_meta.get("slug") or f"unfiled-{sha[:12]}"
        doc_kind = static_meta.get("doc_kind") or doc_kind_guess
        meta = {k: v for k, v in static_meta.items() if k not in ("slug", "doc_kind")}

        document_id = _run_registered(job_id, processing_path, sha, slug, doc_kind, meta, force=force)
        _run_archived(job_id, processing_path, sha, slug, force=force)
        page_count = _run_pages(job_id, document_id, processing_path, force=force)
        _run_structured(job_id, document_id, processing_path, force=force)
        _run_extracted(
            job_id, document_id, slug, doc_kind, processing_path, page_count, meta, force=force
        )
        _run_enriched(job_id, document_id, force=force)
        _run_embedded(job_id, document_id, force=force)
    except StageFailure as fail:
        _handle_failure(inbox_dir, job_id, processing_path, fail.stage, fail.error)
        return

    if confidence < CONFIDENCE_THRESHOLD:
        _finalize_review(inbox_dir, job_id, processing_path, slug)
    else:
        _finalize_done(inbox_dir, job_id, processing_path, slug)


def process_file(
    inbox_dir: Path, source_path: Path, known_docs: dict[str, dict], *, force: bool = False
) -> None:
    """A brand-new stable file: move it into _processing/, then run it."""
    processing_path = _move(source_path, inbox_dir / "_processing")
    _ingest(inbox_dir, processing_path, known_docs, force=force)


def _resume_orphans(inbox_dir: Path, known_docs: dict[str, dict]) -> None:
    """Files already sitting in _processing/ -- left there by a crash. Each
    one resumes from whatever stage its ingest_job last completed."""
    processing_dir = inbox_dir / "_processing"
    if not processing_dir.exists():
        return
    for entry in sorted(processing_dir.iterdir()):
        if entry.is_dir() or should_ignore(entry.name):
            continue
        _ingest(inbox_dir, entry, known_docs, force=False)


def _locate_by_filename(inbox_dir: Path, filename: str) -> Path | None:
    search_dirs = [inbox_dir / d for d in ("_processing", "_failed", "_review", "_duplicates")]
    done_dir = inbox_dir / "_done"
    if done_dir.exists():
        search_dirs.extend(sorted((p for p in done_dir.iterdir() if p.is_dir()), reverse=True))
    for d in search_dirs:
        if not d.is_dir():
            continue
        candidate = d / filename
        if candidate.exists():
            return candidate
    return None


def _force_reprocess(sha: str) -> None:
    with db.connect() as conn:
        job = db.one(
            conn,
            "SELECT id, source_path, original_filename FROM ingest_job "
            "WHERE sha256 = %s ORDER BY discovered_at DESC LIMIT 1",
            (sha,),
        )
    if not job:
        print(f"force: no ingest_job found for sha256={sha}")
        return

    inbox_dir = _inbox_dir()
    path = Path(job["source_path"])
    if not path.exists():
        located = _locate_by_filename(inbox_dir, job["original_filename"])
        if located is None:
            print(
                f"force: job={job['id']} original is not at its recorded path and "
                "could not be found in any inbox state directory"
            )
            return
        path = located
        # keep ingest_job.source_path in sync with reality so the next
        # lookup (and the next --force) finds it without this fallback
        with db.connect() as conn:
            conn.execute(
                "UPDATE ingest_job SET source_path = %s WHERE id = %s", (str(path), job["id"])
            )
            conn.commit()

    known_docs = load_documents_yaml(_private_dir())
    _ingest(inbox_dir, path, known_docs, force=True)


# ── one sweep ─────────────────────────────────────────────────────────────


def run_once(*, dry_run: bool = False, force_sha: str | None = None) -> None:
    inbox_dir = _inbox_dir()
    ensure_state_dirs(inbox_dir)

    if force_sha:
        _force_reprocess(force_sha)
        return

    known_docs = load_documents_yaml(_private_dir())

    if dry_run:
        processing_dir = inbox_dir / "_processing"
        for entry in sorted(processing_dir.iterdir()):
            if entry.is_dir() or should_ignore(entry.name):
                continue
            print(f"[dry-run] would resume in-flight job: {_describe(entry)}")
        for f in stable_candidates(inbox_dir):
            print(f"[dry-run] would ingest: {_describe(f)}")
        return

    _resume_orphans(inbox_dir, known_docs)
    for f in stable_candidates(inbox_dir):
        process_file(inbox_dir, f, known_docs)


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ingest_inbox")
    ap.add_argument("--once", action="store_true", help="run a single sweep and exit")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; no writes, no moves")
    ap.add_argument("--force", metavar="SHA", help="re-run every stage for the job with this sha256")
    args = ap.parse_args(argv)

    if not args.once:
        print("ingest_inbox: only --once is supported; the daemon lives in tools/watch_inbox.py")
        return 2

    if args.dry_run:
        run_once(dry_run=True, force_sha=args.force)
        return 0

    lock_path = _lock_path()
    try:
        acquire_lock(lock_path)
    except RuntimeError as exc:
        print(f"ingest_inbox: {exc}")
        return 1
    try:
        run_once(dry_run=False, force_sha=args.force)
    finally:
        release_lock(lock_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
