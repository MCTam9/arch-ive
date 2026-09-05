"""Split page text into windows the embedding model can actually read.

    python3 -m tools.chunk_pages                 # dry run
    python3 -m tools.chunk_pages --yes
    python3 -m tools.chunk_pages --status
    python3 -m tools.embed_chunks                # always the second step

A page with no extracted item becomes a chunk so its text is still findable.
That chunk was the *whole page*: median 2,186 characters, longest 13,642. The
embedding model is bge-small with a 512-token window, and **129 of the 398 page
chunks (32%) exceed it** -- so their vectors describe only the top of each page
and everything below it is invisible to vector search while still looking
indexed. Full-text was never affected: `tsv` covers the whole string.

So pages are windowed. `chunk.ordinal` has existed since the table was created,
defaulted to 0 on every row, waiting for exactly this.

Windows overlap, which is not redundancy: a sentence that straddles a boundary
is otherwise in neither window's vector. Search groups page chunks back into
one result per page and scores by the best window, so the overlap cannot
double-count and a match deep in a long page now ranks on its own merits
instead of on whatever happened to be at the top.
"""
from __future__ import annotations

import argparse
import math
import re

from tools import db
from tools.env import load_env

# The budget is tokens, not characters, and the difference is not academic: a
# page of prose runs to 6.5 characters per token while a page of dimensions and
# codes runs to 1.3. A fixed character window sized for prose still overflowed
# 512 tokens on four of the densest pages -- which are precisely the pages
# whose numbers people search for.
#
# So each page gets its own character budget, derived from its own density.
# 420 leaves room for the model's special tokens and for the estimator being
# slightly optimistic (measured worst case: 3% under, across all 1,097
# windows).
TARGET_TOKENS = 420
# A cap for the pathological case -- a page whose density estimate is wrong
# enough to ask for an enormous window.
MAX_CHARS = 2000
OVERLAP = 200

_UNIT = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Roughly what WordPiece will make of this, without loading the model.

    A tool that chunks text should not have to import a 130MB model to decide
    where to cut, and `tools/embed_chunks.py` already guards the case where the
    model is not installed at all. Words split into pieces of about three
    characters, punctuation is one token each; measured against the real
    tokenizer over every window this produces, the worst underestimate is 3%.
    """
    return sum(max(1, math.ceil(len(u) / 3)) for u in _UNIT.findall(text))


def _char_budget(text: str) -> int:
    """This page's token budget, expressed in characters."""
    tokens = estimate_tokens(text)
    if tokens <= 0:
        return MAX_CHARS
    return max(MIN_WINDOW * 2, min(MAX_CHARS, int(TARGET_TOKENS * len(text) / tokens)))
# Below this a window is page furniture -- a folio, a running head -- and its
# own row would only ever be noise.
MIN_WINDOW = 40

_PARA = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _hard_split(text: str, target: int) -> list[str]:
    """Last resort for a paragraph with no sentence breaks -- a table dumped as
    one run of text, which this corpus has plenty of."""
    out = []
    while len(text) > target:
        cut = text.rfind(" ", 0, target)
        if cut <= 0:
            cut = target
        out.append(text[:cut].strip())
        text = text[cut:].lstrip()
    if text.strip():
        out.append(text.strip())
    return out


def _pieces(text: str, target: int) -> list[str]:
    """Paragraphs, then sentences, then words: split at the largest boundary
    that gets a piece under the target."""
    out: list[str] = []
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= target:
            out.append(para)
            continue
        run = ""
        for sentence in _SENTENCE.split(para):
            if len(run) + len(sentence) + 1 <= target:
                run = f"{run} {sentence}".strip()
            else:
                if run:
                    out.append(run)
                run = ""
                if len(sentence) > target:
                    out.extend(_hard_split(sentence, target))
                else:
                    run = sentence
        if run:
            out.append(run)
    return out


def split(text: str) -> list[str]:
    """A page's text as overlapping windows, in reading order.

    Pure, and tested as such: the windowing rule is the part worth pinning, and
    it needs no database to be wrong.
    """
    text = (text or "").strip()
    if not text:
        return []
    target = _char_budget(text)
    if len(text) <= target:
        return [text]

    windows: list[str] = []
    run = ""
    for piece in _pieces(text, target):
        if len(run) + len(piece) + 2 <= target:
            run = f"{run}\n\n{piece}".strip()
            continue
        if run:
            windows.append(run)
        # Carry the tail of the previous window forward, snapped to a word, so
        # a sentence spanning the join survives in one piece somewhere.
        tail = run[-OVERLAP:] if run else ""
        cut = tail.find(" ")
        tail = tail[cut + 1:] if cut > 0 else tail
        run = f"{tail}\n\n{piece}".strip() if tail else piece
        while len(run) > target:
            windows.append(run[:target].rsplit(" ", 1)[0])
            run = run[target:].lstrip()
    if run:
        windows.append(run)

    # The character budget comes from the *page's* average density, and a
    # single window can be denser than its page -- a table sitting in the
    # middle of prose. Measured on the corpus nothing exceeded the model's real
    # 512-token limit, but "measured, and it was fine" is what the whole-page
    # chunk had going for it too. So the budget is enforced per window rather
    # than assumed, and the property holds by construction.
    bounded: list[str] = []
    for window in windows:
        bounded.extend(_fit(window))
    return [w for w in bounded if len(w) >= MIN_WINDOW] or [text[:target]]


def _fit(window: str) -> list[str]:
    """Cut a window down until every piece is inside the token budget."""
    if estimate_tokens(window) <= TARGET_TOKENS:
        return [window]
    out: list[str] = []
    rest = window
    while estimate_tokens(rest) > TARGET_TOKENS:
        # Cut proportionally to how far over budget it is, then back off to a
        # word boundary; iterating rather than solving because the density of
        # the head is not the density of the whole.
        cut = max(MIN_WINDOW, int(len(rest) * TARGET_TOKENS / estimate_tokens(rest)))
        head = rest[:cut]
        space = head.rfind(" ")
        if space > MIN_WINDOW:
            head = head[:space]
        if estimate_tokens(head) > TARGET_TOKENS:
            head = head[: len(head) // 2]
        out.append(head.strip())
        rest = rest[len(head):].lstrip()
    if rest.strip():
        out.append(rest.strip())
    return [piece for piece in out if piece]


def plan(conn, document: str | None = None) -> dict[str, list[dict]]:
    """What a run would do, keyed on (document, page, ordinal)."""
    params: list = []
    where = ""
    if document:
        where = " AND d.slug = %s"
        params.append(document)

    pages = db.all_rows(
        conn,
        f"""SELECT p.document_id::text AS document_id, p.page_index, p.text
              FROM source_page p
              JOIN source_document d ON d.id = p.document_id
             WHERE p.content_status = 'real' AND p.text IS NOT NULL{where}""",
        tuple(params),
    )
    existing = {
        (r["document_id"], r["page_from"], r["ordinal"]): r
        for r in db.all_rows(
            conn,
            f"""SELECT c.id::text AS chunk_id, c.document_id::text AS document_id,
                       c.page_from, c.ordinal, c.text
                  FROM chunk c
                  JOIN source_document d ON d.id = c.document_id
                 WHERE c.knowledge_item_id IS NULL AND c.asset_id IS NULL{where}""",
            tuple(params),
        )
    }

    # A page an item was extracted from has no page chunk: the item's own chunk
    # covers it. Which pages those are is decided by write_extraction, so the
    # rule here is simply "keep windowing the pages that already have one".
    covered = {(k[0], k[1]) for k in existing}
    wanted: dict[tuple, str] = {}
    for p in pages:
        key = (p["document_id"], p["page_index"])
        if key not in covered:
            continue
        for i, window in enumerate(split(p["text"])):
            wanted[(*key, i)] = window

    insert = [
        {"document_id": k[0], "page_index": k[1], "ordinal": k[2], "text": v}
        for k, v in wanted.items() if k not in existing
    ]
    rewrite = [
        {"chunk_id": existing[k]["chunk_id"], "text": v}
        for k, v in wanted.items() if k in existing and existing[k]["text"] != v
    ]
    stale = [r for k, r in existing.items() if k not in wanted]
    return {"insert": insert, "rewrite": rewrite, "stale": stale}


def apply(conn, work: dict[str, list[dict]]) -> dict[str, int]:
    for r in work["insert"]:
        conn.execute(
            "INSERT INTO chunk (document_id, page_from, page_to, ordinal, text, content_status) "
            "VALUES (%s, %s, %s, %s, %s, 'real')",
            (r["document_id"], r["page_index"], r["page_index"], r["ordinal"], r["text"]),
        )
    for r in work["rewrite"]:
        conn.execute("UPDATE chunk SET text = %s, embedding = NULL WHERE id = %s",
                     (r["text"], r["chunk_id"]))
    for r in work["stale"]:
        conn.execute("DELETE FROM chunk WHERE id = %s", (r["chunk_id"],))
    return {k: len(v) for k, v in work.items()}


def status(conn) -> int:
    rows = db.all_rows(
        conn,
        """SELECT d.slug,
                  count(*)::int AS chunks,
                  count(DISTINCT c.page_from)::int AS pages,
                  max(length(c.text))::int AS longest,
                  count(*) FILTER (WHERE length(c.text) > %s)::int AS oversize
             FROM chunk c
             JOIN source_document d ON d.id = c.document_id
            WHERE c.knowledge_item_id IS NULL AND c.asset_id IS NULL
            GROUP BY d.slug ORDER BY 2 DESC""",
        (MAX_CHARS,),
    )
    print(f"  {'document':34} {'chunks':>7} {'pages':>6} {'longest':>8} {'oversize':>9}")
    for r in rows:
        print(f"  {r['slug']:34} {r['chunks']:>7} {r['pages']:>6} {r['longest']:>8} {r['oversize']:>9}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", help="restrict to one document slug")
    ap.add_argument("--status", action="store_true", help="window sizes per document")
    ap.add_argument("--yes", action="store_true", help="write; without it this is a dry run")
    args = ap.parse_args()

    load_env()
    with db.connect() as conn:
        if args.status:
            return status(conn)
        work = plan(conn, args.document)
        counts = {k: len(v) for k, v in work.items()}
        print(f"chunk_pages: {counts['insert']} to insert, {counts['rewrite']} to rewrite, "
              f"{counts['stale']} to remove")
        if not args.yes:
            print("pass --yes to write")
            return 0
        written = apply(conn, work)
        conn.commit()
        print(f"chunk_pages: inserted {written['insert']}, rewrote {written['rewrite']}, "
              f"removed {written['stale']}")
        if written["insert"] or written["rewrite"]:
            print("next: python3 -m tools.embed_chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
