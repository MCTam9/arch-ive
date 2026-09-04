"""Pipeline contract: stages, the extractor registry, and the records extractors emit.

Two rules hold the design together:

1. Every stage is idempotent and keyed on (job_id, stage) in `ingest_stage_run`.
   Re-running the pipeline re-runs only what did not finish.
2. Extractors never touch the database. They read a document and return
   `Extraction`; the caller writes it in one transaction. That keeps them pure
   and testable, and means a failed write never leaves half a document behind.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

import psycopg

from tools import db


class State(str, Enum):
    """Mirrors the ingest_state enum in db/schema.sql, in pipeline order."""
    DISCOVERED = "discovered"
    STABLE = "stable"
    HASHED = "hashed"
    DEDUPED = "deduped"
    CLASSIFIED = "classified"
    REGISTERED = "registered"
    ARCHIVED = "archived"
    PAGES = "pages"
    STRUCTURED = "structured"
    EXTRACTED = "extracted"
    ENRICHED = "enriched"
    EMBEDDED = "embedded"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


FAST_LANE = [State.PAGES, State.STRUCTURED, State.EXTRACTED]
SLOW_LANE = [State.ENRICHED, State.EMBEDDED]


# ── what an extractor returns ────────────────────────────────────────────
#
# Records are plain dataclasses, not ORM rows. `ref` is a caller-local id an
# extractor uses to point one record at another before any database id exists.


@dataclass
class Citation:
    page_index: int
    printed_page_label: str | None = None
    bbox: list[float] | None = None


@dataclass
class Item:
    """One knowledge_item plus its subtype payload.

    `item_type` picks the subtable; `payload` holds that subtable's columns
    exactly as named in db/schema.sql, minus knowledge_item_id.
    """
    item_type: str                       # requirement|benchmark|guidance|pattern|
                                         # template|definition|process_step|role
    payload: dict[str, Any]
    title: str | None = None
    statement: str | None = None
    summary: str | None = None
    content_status: str = "real"
    confidence: float | None = None
    node_ref: str | None = None          # -> Node.ref
    citations: list[Citation] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)   # taxonomy_term ids
    ref: str | None = None


@dataclass
class Node:
    node_kind: str
    title: str | None = None
    title_alt: str | None = None
    code: str | None = None
    ordinal: int = 0
    page_from: int | None = None
    page_to: int | None = None
    parent_ref: str | None = None
    text: str | None = None
    ref: str | None = None


@dataclass
class Reference:
    """An unresolved citation to something outside this document."""
    raw_text: str
    ref_kind: str | None = None
    from_node_ref: str | None = None


@dataclass
class Extraction:
    nodes: list[Node] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    # rows for lookup tables the extractor discovered; upserted by the caller
    units: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    frameworks: list[dict] = field(default_factory=list)
    criteria: list[dict] = field(default_factory=list)
    rating_scales: list[dict] = field(default_factory=list)
    rating_levels: list[dict] = field(default_factory=list)
    design_variables: list[dict] = field(default_factory=list)
    design_variable_values: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentContext:
    """What an extractor is given. Read-only."""
    document_id: str
    slug: str
    path: Path                 # the original file, on local disk
    doc_kind: str
    page_count: int
    pages: list[dict]          # source_page rows: id, page_index, text, width_pt, ...
    meta: dict[str, Any] = field(default_factory=dict)


class Extractor(Protocol):
    """One module per document shape.

    Implementations live in extractors/ and register themselves with @register.
    They must be pure: no database, no network, no writes outside .tmp/.
    """
    doc_kinds: tuple[str, ...]

    def extract(self, ctx: DocumentContext) -> Extraction: ...


_REGISTRY: dict[str, Extractor] = {}


def register(extractor: Extractor) -> Extractor:
    for kind in extractor.doc_kinds:
        _REGISTRY[kind] = extractor
    return extractor


def for_doc_kind(doc_kind: str) -> Extractor:
    """Always returns something: an unrecognised shape still gets ingested."""
    if doc_kind in _REGISTRY:
        return _REGISTRY[doc_kind]
    return _REGISTRY["unknown"]


def load_extractors() -> None:
    """Import every module in extractors/ so its @register call runs.

    The fallback goes first, deliberately. It claims several doc_kinds so that
    an unrecognised document is still ingested, but a specific extractor must
    win wherever one exists -- and with plain alphabetical import order it
    would not (generic sorts after deck, and silently replaced it).
    """
    import importlib
    import pkgutil

    import extractors

    names = [m.name for m in pkgutil.iter_modules(extractors.__path__)]
    for name in sorted(names, key=lambda n: (n != "generic", n)):
        importlib.import_module(f"extractors.{name}")


# ── stage runner ─────────────────────────────────────────────────────────


class StageSkipped(Exception):
    """Raised by a stage that has nothing to do; recorded as 'skipped'."""


def run_stage(
    job_id: str,
    stage: State,
    fn: Callable[[psycopg.Connection], dict[str, Any] | None],
    *,
    force: bool = False,
) -> bool:
    """Run one stage once. Returns True if it succeeded or had already succeeded.

    Idempotency is enforced by the UNIQUE (job_id, stage) constraint on
    ingest_stage_run, not by checking-then-writing, so two runners racing on the
    same job cannot both execute the stage.
    """
    with db.connect() as conn:
        prior = db.one(
            conn,
            "SELECT status FROM ingest_stage_run WHERE job_id = %s AND stage = %s",
            (job_id, stage.value),
        )
        if prior and prior["status"] == "ok" and not force:
            return True
        if prior:
            conn.execute(
                "DELETE FROM ingest_stage_run WHERE job_id = %s AND stage = %s",
                (job_id, stage.value),
            )
        conn.execute(
            "INSERT INTO ingest_stage_run (job_id, stage, status) VALUES (%s, %s, 'running')",
            (job_id, stage.value),
        )
        conn.commit()

    started = time.monotonic()
    try:
        with db.transaction() as conn:
            stats = fn(conn) or {}
    except StageSkipped as exc:
        _finish(job_id, stage, "skipped", started, {}, str(exc))
        return True
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised to the caller
        _finish(job_id, stage, "failed", started, {}, f"{type(exc).__name__}: {exc}")
        _bump_failure(job_id, f"{stage.value}: {type(exc).__name__}: {exc}")
        raise
    _finish(job_id, stage, "ok", started, stats, None)
    _advance(job_id, stage)
    return True


def _finish(job_id, stage, status, started, stats, error) -> None:
    import json
    with db.connect() as conn:
        conn.execute(
            """UPDATE ingest_stage_run
                  SET status = %s, finished_at = now(), duration_ms = %s,
                      stats = %s, error = %s
                WHERE job_id = %s AND stage = %s""",
            (status, int((time.monotonic() - started) * 1000),
             json.dumps(stats), error, job_id, stage.value),
        )
        conn.commit()


def _advance(job_id, stage) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_job SET state = %s, updated_at = now() WHERE id = %s",
            (stage.value, job_id),
        )
        conn.commit()


def _bump_failure(job_id, message) -> None:
    with db.connect() as conn:
        conn.execute(
            """UPDATE ingest_job
                  SET attempts = attempts + 1, last_error = %s,
                      state = CASE WHEN attempts + 1 >= 3 THEN 'failed'::ingest_state
                                   ELSE state END,
                      updated_at = now()
                WHERE id = %s""",
            (message, job_id),
        )
        conn.commit()
