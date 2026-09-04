"""Embed pending chunks with a local sentence-transformers model.

Nothing leaves the machine: BAAI/bge-small-en-v1.5 runs locally and writes
into `chunk.embedding vector(384)`. Resumable by construction — it only ever
selects rows where `embedding IS NULL`, so a crash mid-run just means the
next invocation picks up where it left off.
"""
from __future__ import annotations

import sys

from tools import db

MODEL_NAME = "BAAI/bge-small-en-v1.5"
BATCH_SIZE = 64

_model = None  # loaded lazily, once per process


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    _model = SentenceTransformer(MODEL_NAME)
    return _model


def model_available() -> bool:
    """Whether the local model can actually be loaded.

    Separate from embed_pending because a caller needs to tell "embedded
    nothing because nothing was pending" apart from "embedded nothing because
    the model is not installed". The stage runner records the first as ok and
    the second as skipped; conflating them marks a corpus with zero embeddings
    as successfully embedded, which is how you come to believe a knowledge
    base has semantic search when it has none.
    """
    return _load_model() is not None


def embed_pending(conn, document_id: str | None = None) -> int:
    """Embed chunks whose embedding IS NULL. Returns count embedded.

    Prints a clear instruction and returns 0, without crashing the pipeline,
    if sentence-transformers isn't installed -- it's a large download and
    this must never trigger it on its own.
    """
    model = _load_model()
    if model is None:
        print(
            "embed_chunks: sentence-transformers is not installed.\n"
            "  Install it yourself (it pulls a multi-hundred-MB model):\n"
            "    ./.venv/bin/pip install sentence-transformers\n"
            "  Then re-run this stage; it is resumable and will pick up\n"
            "  exactly the chunks still missing an embedding.",
            file=sys.stderr,
        )
        return 0

    where = "embedding IS NULL"
    params: tuple = ()
    if document_id is not None:
        where += " AND document_id = %s"
        params = (document_id,)

    total = db.scalar(conn, f"SELECT count(*) FROM chunk WHERE {where}", params)
    if not total:
        print("embed_chunks: nothing pending")
        return 0

    print(f"embed_chunks: {total} chunk(s) pending, batches of {BATCH_SIZE}")
    embedded = 0
    while True:
        rows = db.all_rows(
            conn,
            f"SELECT id, text FROM chunk WHERE {where} ORDER BY id LIMIT %s",
            (*params, BATCH_SIZE),
        )
        if not rows:
            break

        vectors = model.encode(
            [r["text"] for r in rows],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        for row, vec in zip(rows, vectors):
            # cast explicitly rather than depending on the pgvector psycopg
            # adapter being registered: a bracketed literal always works.
            literal = "[" + ",".join(f"{x:.8f}" for x in vec.tolist()) + "]"
            conn.execute(
                "UPDATE chunk SET embedding = %s::vector WHERE id = %s",
                (literal, row["id"]),
            )
        # No commit here. The stage runner calls this inside db.transaction()
        # and an explicit commit raises there -- one stage, one transaction,
        # per CONTRACT.md. A caller that wants per-batch durability sets
        # autocommit on the connection instead (see __main__), which the loop
        # below gets for free. Resumability holds either way: the query
        # selects on `embedding IS NULL`, so an aborted run leaves the rest
        # pending rather than losing what it wrote.
        embedded += len(rows)
        print(f"embed_chunks: {embedded}/{total}")

    return embedded


if __name__ == "__main__":
    doc_id = sys.argv[1] if len(sys.argv) > 1 else None
    with db.connect() as _conn:
        # db.connect() runs set_config to apply the RLS account, which opens a
        # transaction, and autocommit cannot be switched on inside one. Commit
        # it first: that config is session-scoped (the `false` argument), so it
        # survives the commit and every later statement still sees the account.
        _conn.commit()
        # autocommit so a long run over thousands of chunks is durable batch by
        # batch rather than all-or-nothing
        _conn.autocommit = True
        n = embed_pending(_conn, doc_id)
    print(f"embed_chunks: embedded {n} chunk(s)")
