"""Resolve external_reference.raw_text against the corpus.

Three outcomes:

- **resolved** -- raw_text names a document that IS one of the corpus's own
  (a token-overlap match against slug / title / original_filename, gated at
  a high threshold so a shared common word like "report" or "plan" cannot
  produce a false match on its own).
- **missing_source** -- raw_text is specific enough to identify what it is
  citing, and that referent is confirmed absent from the corpus. Two shapes
  of this were verified against the source PDFs (see
  workflows/resolve_references.md for the page-by-page check):
    * `ref_kind='module_chapter'|'page'` rows citing "Module N ..." -- all
      six crib sheets cite chapters/pages of a 6-module parent guide that
      was never collected (private/documents.yaml's `missing_parents`, when
      present, supplies the human-readable topic per module for reporting;
      classification itself does not depend on that file being present).
    * `ref_kind='external_document'` rows citing a specific sibling
      deliverable by its own document code (e.g. "Refer to
      CS-BHC-REP-ICT-Smart_Metaverse_Playbook") that names none of this
      corpus's 14 documents.
- **unresolved** -- left alone. Reserved for a reference too vague to say
  with confidence whether its referent is in the corpus or not.

Idempotent: only rows currently `status='unresolved'` are reclassified: a
row already settled as resolved/missing_source is left as is, so a second
run changes nothing.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from tools import db
from tools.ingest_inbox import parse_yaml

MODULE_RE = re.compile(r"module\s+(\d+)", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")

# a corpus-match this confident (fraction of the reference's own words found
# in one document's slug/title/filename) is treated as resolved; below it,
# a single shared word like "plan" or "report" must not manufacture a match.
MATCH_THRESHOLD = 0.6


def resolve_references(conn) -> dict:
    """Match external_reference.raw_text to documents in the corpus. Idempotent."""
    corpus = db.all_rows(
        conn, "SELECT id, slug, title, original_filename FROM source_document WHERE is_current"
    )
    module_topics = _load_module_topics()

    pending = db.all_rows(
        conn, "SELECT id, raw_text, ref_kind FROM external_reference WHERE status = 'unresolved'"
    )

    counts = {"resolved": 0, "unresolved": 0, "missing_source": 0}
    missing_modules: set[str] = set()
    missing_other: set[str] = set()

    for row in pending:
        status, resolved_document_id = _classify(row["raw_text"], row["ref_kind"], corpus)
        counts[status] += 1
        if status == "missing_source":
            module_match = MODULE_RE.search(row["raw_text"])
            if module_match:
                missing_modules.add(f"module-{module_match.group(1)}")
            else:
                missing_other.add(row["raw_text"])
        conn.execute(
            "UPDATE external_reference SET status = %s, resolved_document_id = %s WHERE id = %s",
            [status, resolved_document_id, row["id"]],
        )

    return {
        **counts,
        "missing_documents": sorted(module_topics.get(m, m) for m in missing_modules)
        + sorted(missing_other),
    }


def _classify(raw_text: str, ref_kind: str | None, corpus: list[dict]) -> tuple[str, str | None]:
    match = _find_corpus_match(raw_text, corpus)
    if match:
        return "resolved", match["id"]

    if ref_kind in ("module_chapter", "page") and MODULE_RE.search(raw_text):
        # specific enough to name a module of the (uncollected) parent guide
        return "missing_source", None

    if ref_kind in ("external_document", "sibling_doc"):
        # a formal document code that matches none of the corpus's own
        # documents is, by construction, confirmed absent -- not merely
        # ambiguous. See workflows/resolve_references.md for the per-row
        # source-page check behind this for the current corpus.
        return "missing_source", None

    return "unresolved", None


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").replace("_", " ").replace("-", " ").lower()))


def _find_corpus_match(raw_text: str, corpus: list[dict]) -> dict | None:
    raw_tokens = _tokens(raw_text)
    if len(raw_tokens) < 2:
        return None  # too little signal to trust a token-overlap match
    best, best_score = None, 0.0
    for doc in corpus:
        doc_tokens = (
            _tokens(doc["slug"]) | _tokens(doc.get("title")) | _tokens(doc.get("original_filename"))
        )
        if not doc_tokens:
            continue
        score = len(raw_tokens & doc_tokens) / len(raw_tokens)
        if score > best_score:
            best, best_score = doc, score
    return best if best_score >= MATCH_THRESHOLD else None


def _private_dir() -> Path:
    return Path(os.environ.get("PRIVATE_DIR", "./private")).expanduser().resolve()


def _load_module_topics() -> dict[str, str]:
    """module id ('module-1') -> short topic label, from private/documents.yaml.

    Enrichment only, for the human-readable report: classification above
    never depends on this file existing, since a fresh clone / test database
    will not have it.
    """
    path = _private_dir() / "documents.yaml"
    if not path.exists():
        return {}
    data = parse_yaml(path.read_text(encoding="utf-8")) or {}
    return {
        entry["id"]: entry["title"]
        for entry in data.get("missing_parents") or []
        if entry.get("id") and entry.get("title")
    }


if __name__ == "__main__":
    with db.transaction() as _conn:
        result = resolve_references(_conn)
    print(f"resolve_references: {result}")
