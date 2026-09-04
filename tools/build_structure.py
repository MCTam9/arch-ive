"""Fills the recursive `doc_node` tree for one document.

Three strategies, cheapest/most-reliable first:

1. **Bookmarks.** `doc.get_toc()` when it exists -- one document in the
   corpus carries 261 entries of it. A PowerPoint export's outline is one
   entry per slide, so decks use this path too, just with `node_kind='slide'`.
2. **Heading detection.** Three documents ship with no bookmarks at all.
   Headings there are recovered from `page.get_text("dict")`: a span
   noticeably larger than the page's body-text size, plus the numeric
   section codes (`1.1.`, `3.2.4.1.`) and criterion codes (`RE2.1`, `PC4.2`)
   this corpus uses, found at line starts.
3. **Fixed shapes.** Crib sheets are two panels (reference + level matrix);
   xlsx workbooks are one node per sheet.

ltree labels only accept `[A-Za-z0-9_]`, so every code is sanitised (dots ->
underscores) and suffixed with a running counter to keep paths unique per
document even when two headings sanitise to the same text.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl
import pymupdf
import psycopg

from tools import db

_NUMERIC_CODE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){1,4})\.?\s+\S")
_ALPHA_CODE_RE = re.compile(r"^([A-Z]{2,4}\d+(?:\.\d+)+)\b\s*\S?")
_LEADING_CODE_RE = re.compile(r"^([A-Za-z]{0,4}\d+(?:[.\-]\d+)*)\.?\s+")
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]+")


def build_structure(conn: psycopg.Connection, document_id: str, path: Path) -> int:
    """Fill doc_node from bookmarks or heading detection. Returns node count."""
    path = Path(path)
    conn.execute("DELETE FROM doc_node WHERE document_id = %s", (document_id,))

    if path.suffix.lower() == ".xlsx":
        return _build_from_sheets(conn, document_id, path)

    row = db.one(conn, "SELECT doc_kind FROM source_document WHERE id = %s", (document_id,))
    doc_kind = row["doc_kind"] if row else "unknown"

    with pymupdf.open(path) as doc:
        if doc_kind == "crib_sheet":
            return _build_crib_panels(conn, document_id, doc)

        toc = _safe_toc(doc)
        if doc_kind == "deck" and toc:
            return _build_slides(conn, document_id, toc)
        if toc:
            return _build_from_toc(conn, document_id, doc, toc)
        return _build_from_headings(conn, document_id, doc)


# ── shared node writer ───────────────────────────────────────────────────


def _insert_node(conn: psycopg.Connection, *, document_id: str, parent_id: str | None,
                  node_kind: str, code: str | None, title: str | None, title_alt: str | None,
                  ordinal: int, page_from: int | None, page_to: int | None,
                  ltree_path: str, text: str | None = None) -> str:
    return db.one(
        conn,
        """INSERT INTO doc_node
                (document_id, parent_id, node_kind, code, title, title_alt,
                 ordinal, page_from, page_to, path, text)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::ltree, %s)
           RETURNING id""",
        (document_id, parent_id, node_kind, code, title, title_alt,
         ordinal, page_from, page_to, ltree_path, text),
    )["id"]


def _sanitize(label: str | None) -> str:
    if not label:
        return "n"
    cleaned = _SANITIZE_RE.sub("_", label.strip()).strip("_")
    return cleaned or "n"


# ── xlsx: one node per sheet ──────────────────────────────────────────────


def _build_from_sheets(conn: psycopg.Connection, document_id: str, path: Path) -> int:
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        count = 0
        for ordinal, name in enumerate(wb.sheetnames):
            _insert_node(
                conn, document_id=document_id, parent_id=None, node_kind="sheet",
                code=name, title=name, title_alt=None, ordinal=ordinal,
                page_from=None, page_to=None,
                ltree_path=f"{_sanitize(name)}_{ordinal}",
            )
            count += 1
        return count
    finally:
        wb.close()


# ── crib sheets: fixed 2-panel shape ─────────────────────────────────────


def _build_crib_panels(conn: psycopg.Connection, document_id: str, doc: "pymupdf.Document") -> int:
    titles = ["Reference", "Level matrix"]
    count = 0
    for i in range(doc.page_count):
        title = titles[i] if i < len(titles) else f"Panel {i + 1}"
        _insert_node(
            conn, document_id=document_id, parent_id=None, node_kind="panel",
            code=None, title=title, title_alt=None, ordinal=i,
            page_from=i + 1, page_to=i + 1, ltree_path=f"panel_{i}",
        )
        count += 1
    return count


# ── decks: one node per slide, from the PowerPoint-exported outline ──────


def _build_slides(conn: psycopg.Connection, document_id: str, toc: list) -> int:
    count = 0
    for ordinal, (_level, title, page) in enumerate(toc):
        clean_title = re.sub(r"^Slide\s*\d+:\s*", "", title or "").strip() or title
        _insert_node(
            conn, document_id=document_id, parent_id=None, node_kind="slide",
            code=f"slide-{ordinal + 1}", title=clean_title, title_alt=None,
            ordinal=ordinal, page_from=page, page_to=page,
            ltree_path=f"slide_{ordinal}",
        )
        count += 1
    return count


# ── bookmark-derived tree ─────────────────────────────────────────────────

_LEVEL_KIND = {1: "chapter", 2: "section"}


def _kind_for_level(level: int) -> str:
    return _LEVEL_KIND.get(level, "subsection")


def _build_from_toc(conn: psycopg.Connection, document_id: str, doc: "pymupdf.Document",
                     toc: list) -> int:
    parent_at: dict[int, str] = {}
    path_at: dict[int, str] = {}
    entries: list[tuple[str, int, int]] = []  # (node_id, level, page)

    for ordinal, (level, title, page) in enumerate(toc):
        title = (title or "").strip()
        code = _leading_code(title)
        seg = f"{_sanitize(code or title)}_{ordinal}"
        parent_level = level - 1
        parent_id = parent_at.get(parent_level)
        parent_path = path_at.get(parent_level, "")
        full_path = f"{parent_path}.{seg}" if parent_path else seg

        node_id = _insert_node(
            conn, document_id=document_id, parent_id=parent_id, node_kind=_kind_for_level(level),
            code=code, title=title or None, title_alt=None, ordinal=ordinal,
            page_from=max(page, 1), page_to=None, ltree_path=full_path,
        )
        parent_at[level] = node_id
        path_at[level] = full_path
        for stale in [lv for lv in parent_at if lv > level]:
            del parent_at[stale]
            del path_at[stale]
        entries.append((node_id, level, max(page, 1)))

    _fill_page_to(conn, entries, doc.page_count)
    return len(toc)


def _leading_code(title: str) -> str | None:
    m = _LEADING_CODE_RE.match(title)
    return m.group(1) if m else None


def _fill_page_to(conn: psycopg.Connection, entries: list[tuple[str, int, int]], last_page: int) -> None:
    """page_to = one before the next entry at the same-or-shallower level, else the doc's last page."""
    for i, (node_id, level, page_from) in enumerate(entries):
        page_to = last_page
        for _node_id2, level2, page_from2 in entries[i + 1:]:
            if level2 <= level:
                page_to = max(page_from, page_from2 - 1)
                break
        conn.execute("UPDATE doc_node SET page_to = %s WHERE id = %s", (page_to, node_id))


# ── no bookmarks: font-size + section-code heading detection ─────────────


def _dominant_font_size(doc: "pymupdf.Document", sample_pages: int = 12) -> float:
    n = doc.page_count
    step = max(1, n // sample_pages)
    sizes: Counter[float] = Counter()
    for i in range(0, n, step):
        try:
            d = doc[i].get_text("dict")
        except Exception:
            continue
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if text:
                        sizes[round(span["size"], 1)] += len(text)
    if not sizes:
        return 10.0
    return sizes.most_common(1)[0][0]


def _line_text_and_stats(line: dict) -> tuple[str, float, bool]:
    spans = line.get("spans", [])
    text = "".join(s["text"] for s in spans).strip()
    max_size = max((s["size"] for s in spans), default=0.0)
    bold = any(any(k in s.get("font", "") for k in ("Bold", "Black", "SemiBold")) for s in spans)
    return text, max_size, bold


_JUNK_LINE_RE = re.compile(r"^[\d.,%*\s\-–—]+$")  # bare figures/percentages, not titles


def _drop_furniture(
    candidates: list[tuple[int, int, str | None, str]],
) -> list[tuple[int, int, str | None, str]]:
    """Discard styled text that is page furniture rather than structure.

    Big bold type is not the same thing as a heading. In practice it also picks
    up repeated table column headers, glossary acronyms and cover-page credit
    blocks, which between them outnumbered the real sections roughly ten to one.
    A candidate carrying a section code is always kept -- the code is evidence.
    An uncoded one has to earn its place.
    """
    seen: Counter[str] = Counter()
    for _, _, code, title in candidates:
        if code is None:
            seen[title.strip().casefold()] += 1

    kept: list[tuple[int, int, str | None, str]] = []
    used_codes: set[str] = set()
    for page, level, code, title in candidates:
        text = title.strip()
        if code is not None:
            # A section code identifies one place in a document. Repeats are
            # cross-references and checklist rows citing it -- notably the
            # strategy codes, which are requirements, not structure. Those
            # belong to the compliance extractor; keep only first sight here.
            if code in used_codes:
                continue
            used_codes.add(code)
            kept.append((page, level, code, title))
            continue
        # A real chapter title does not appear three times in one book.
        if seen[text.casefold()] >= 3:
            continue
        words = text.split()
        # Glossary entries: a lone short token, usually an acronym.
        if len(words) == 1 and (len(text) <= 6 or text.isupper()):
            continue
        # Body-text fragments that merely happened to be set in bold. A heading
        # starts cleanly and does not trail off mid-clause.
        if not (text[0].isupper() or text[0].isdigit()):
            continue
        if text.endswith((",", ";", ":")) or text.rstrip().endswith(" the"):
            continue
        kept.append((page, level, code, title))
    return kept


def _build_from_headings(conn: psycopg.Connection, document_id: str, doc: "pymupdf.Document") -> int:
    body_size = _dominant_font_size(doc)
    size_threshold = body_size + 1.5
    big_threshold = body_size + 5

    candidates: list[tuple[int, int, str | None, str]] = []  # (page, level, code, title)
    for i in range(doc.page_count):
        try:
            d = doc[i].get_text("dict")
        except Exception:
            continue
        for block in d.get("blocks", []):
            # Chapter-style headings often wrap across several same-style lines
            # inside one text block (design copy, not prose) -- merge those
            # runs into a single node instead of one fragment per line.
            buffer: list[str] = []

            def flush() -> None:
                if not buffer:
                    return
                joined = " ".join(buffer)
                buffer.clear()
                if 3 <= len(joined) <= 140 and not _JUNK_LINE_RE.match(joined):
                    candidates.append((i + 1, 1, None, joined))

            for line in block.get("lines", []):
                text, max_size, bold = _line_text_and_stats(line)
                if not text or _JUNK_LINE_RE.match(text):
                    flush()
                    continue
                numeric_m = _NUMERIC_CODE_RE.match(text)
                alpha_m = _ALPHA_CODE_RE.match(text)
                if numeric_m and max_size >= size_threshold and len(text) <= 140:
                    flush()
                    code = numeric_m.group(1)
                    level = min(code.count(".") + 1, 4)
                    candidates.append((i + 1, level, code, text))
                elif alpha_m and max_size >= size_threshold and len(text) <= 140:
                    flush()
                    candidates.append((i + 1, 2, alpha_m.group(1), text))
                elif bold and max_size >= big_threshold:
                    buffer.append(text)
                else:
                    flush()
            flush()

    candidates = _drop_furniture(candidates)

    if not candidates:
        # last resort: never leave a document with zero structure
        _insert_node(
            conn, document_id=document_id, parent_id=None, node_kind="volume",
            code=None, title=None, title_alt=None, ordinal=0,
            page_from=1, page_to=doc.page_count, ltree_path="doc",
        )
        return 1

    parent_at: dict[int, str] = {}
    path_at: dict[int, str] = {}
    entries: list[tuple[str, int, int]] = []

    for ordinal, (page, level, code, title) in enumerate(candidates):
        seg = f"{_sanitize(code or title)}_{ordinal}"
        parent_level = level - 1
        parent_id = parent_at.get(parent_level)
        parent_path = path_at.get(parent_level, "")
        full_path = f"{parent_path}.{seg}" if parent_path else seg

        node_id = _insert_node(
            conn, document_id=document_id, parent_id=parent_id, node_kind=_kind_for_level(level),
            code=code, title=title, title_alt=None, ordinal=ordinal,
            page_from=page, page_to=None, ltree_path=full_path,
        )
        parent_at[level] = node_id
        path_at[level] = full_path
        for stale in [lv for lv in parent_at if lv > level]:
            del parent_at[stale]
            del path_at[stale]
        entries.append((node_id, level, page))

    _fill_page_to(conn, entries, doc.page_count)
    return len(candidates)


def _safe_toc(doc: "pymupdf.Document") -> list:
    try:
        return doc.get_toc() or []
    except Exception:
        return []
