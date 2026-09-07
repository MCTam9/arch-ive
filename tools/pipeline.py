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
    """Mirrors the ingest_state enum in db/schema.sql.

    The values, not the order: what runs when is STAGES, below. DONE, FAILED
    and NEEDS_REVIEW are outcomes the orchestrator writes, never stages.
    """
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
    # requirement_scope_applicability rows: the same requirement applies
    # differently under each contractor scope of work, and collapsing that to
    # one row loses which role a target binds.
    scope_applicability: list[dict] = field(default_factory=list)
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
    requirement_scopes: list[dict] = field(default_factory=list)
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


# ── the stage sequence ───────────────────────────────────────────────────
#
# The order used to be written out by hand in ingest_inbox._ingest and
# restated in prose in three other places. It is data now, declared once,
# here. `tools/ingest_inbox.py` binds a function to each entry with
# @runs_stage and then iterates STAGES; nothing else decides what runs next.


@dataclass(frozen=True)
class Halt:
    """A stage's finding that the job is finished before the later stages run.

    Returned instead of None. `finalize` is handed the orchestrator's ingest
    context and performs the terminal move; the runner then stops. Only a
    Stage declared `ends_run` may return one, which is what keeps early
    termination a property of the sequence rather than a name the loop knows.
    """
    reason: str
    finalize: Callable[[Any], None]


@dataclass
class Stage:
    """One step of the ingest sequence.

    `run(ctx, *, force)` returns None to continue or a Halt to stop. It is
    bound at import time by the orchestrator that owns the side effects --
    this module owns the order, not the work -- so `run` is None until
    tools.ingest_inbox has been imported. `stage_sequence()` is the guard.
    """
    state: State
    run: Callable[..., Halt | None] | None = None
    ends_run: bool = False


# DISCOVERED and STABLE are not here: they happen on the filesystem before a
# job has anything to record -- a file is seen, then held until its size and
# mtime stop moving. The sequence starts at the first stage that writes.
STAGES: tuple[Stage, ...] = (
    Stage(State.HASHED),
    # the only stage that can finish a job on its own: identical bytes already
    # in the corpus mean there is nothing left to classify, register or extract
    Stage(State.DEDUPED, ends_run=True),
    Stage(State.CLASSIFIED),
    Stage(State.REGISTERED),
    # archived comes before extraction on purpose: the original is filed and
    # pushed first, so an extraction failure can never lose the file
    Stage(State.ARCHIVED),
    Stage(State.PAGES),
    Stage(State.STRUCTURED),
    Stage(State.EXTRACTED),
    Stage(State.ENRICHED),
    Stage(State.EMBEDDED),
)


def runs_stage(state: State) -> Callable[[Any], Any]:
    """Bind a function to its STAGES entry. Fails loudly on a state that the
    sequence does not contain, so a typo cannot silently drop a stage."""
    def decorate(fn):
        for stage in STAGES:
            if stage.state is state:
                stage.run = fn
                return fn
        raise KeyError(f"{state.value} is not in STAGES")
    return decorate


def stage_sequence() -> tuple[Stage, ...]:
    """STAGES, once every entry has a function. Raises if one does not: an
    unbound stage means the orchestrator was never imported, and skipping it
    silently would drop work from every ingest."""
    unbound = [s.state.value for s in STAGES if s.run is None]
    if unbound:
        raise RuntimeError(f"no function bound for stage(s): {', '.join(unbound)}")
    return STAGES


# ── enrichment: the second half of the sequence ──────────────────────────
#
# These would each be a STAGES entry of their own, but `ingest_job.state` and
# `ingest_stage_run.stage` are both the `ingest_state` enum in
# db/schema.sql, ingest_stage_run is UNIQUE (job_id, stage), and that enum has
# exactly one name -- 'enriched' -- for all of this. So they are steps within
# one stage and one transaction, in this order, until the enum grows names for
# them. Add the state, then split the step out; do not add a step that needs
# its own resume point.
#
# Every one is pure DB and idempotent. `crop_figures`, `describe_figures` and
# `upload_page_images` deliberately stay manual: they need R2, a restored
# original and externally-produced model output, and dropping a file into
# inbox/ must not start doing network I/O.
#
# Three calling conventions coexist upstream -- (conn, document_id),
# (conn, document_slug), and two-phase plan/apply behind a --yes CLI gate.
# Normalising them is this adapter's whole job; the tools' own signatures are
# what their CLIs and other callers depend on and are not ours to change.


@dataclass(frozen=True)
class Enrichment:
    """One pure-DB enrichment tool, adapted to a single call shape.

    `run(conn, *, document_id, slug)` returns a stats dict recorded under
    `name` in the enriched stage's ingest_stage_run.stats.
    """
    name: str
    run: Callable[..., dict[str, Any]]


def _enrich_facets(conn, *, document_id: str, slug: str) -> dict[str, Any]:
    from tools.classify_facets import classify_items

    return classify_items(conn, document_id)


def _enrich_stage_links(conn, *, document_id: str, slug: str) -> dict[str, Any]:
    from tools.link_stages import link_stages

    return link_stages(conn, document_id)


# plan/apply is the CLI's dry-run gate, not the function's: apply(plan(...)) is
# the whole of what `--yes` does. Both scope on slug, which a new revision
# shares with the document it supersedes -- so both revisions are re-planned.
# That is the tools' own --document behaviour and it is idempotent: the
# superseded document's windows recompute to what is already stored.


def _enrich_page_chunks(conn, *, document_id: str, slug: str) -> dict[str, Any]:
    from tools import chunk_pages

    return chunk_pages.apply(conn, chunk_pages.plan(conn, slug))


def _enrich_figure_chunks(conn, *, document_id: str, slug: str) -> dict[str, Any]:
    from tools import chunk_figures

    return chunk_figures.apply(conn, chunk_figures.plan(conn, slug))


def _enrich_chunk_text(conn, *, document_id: str, slug: str) -> dict[str, Any]:
    from tools.refresh_chunk_text import refresh

    # the id, not the slug: a new revision shares its predecessor's slug, and
    # this stage is only ever recomposing the document this job just wrote
    return {"rewritten": refresh(conn, document_id=document_id)}


ENRICHMENTS: tuple[Enrichment, ...] = (
    Enrichment("facets", _enrich_facets),
    Enrichment("stage_links", _enrich_stage_links),
    Enrichment("page_chunks", _enrich_page_chunks),
    Enrichment("figure_chunks", _enrich_figure_chunks),
    # last of the five, and before State.EMBEDDED: rewriting a chunk clears its
    # embedding, so anything that rewrites text has to run before the embedder
    Enrichment("chunk_text", _enrich_chunk_text),
)


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
