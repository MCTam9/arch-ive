"""Readable status table of `ingest_job` rows for operators.

Shows slugs (or, before a document is registered, a sha256 prefix), never
the original filename -- see CONTRACT.md ground rules.
"""
from __future__ import annotations

import argparse

from tools import db


def _fmt_ts(ts) -> str:
    return "-" if ts is None else ts.strftime("%Y-%m-%d %H:%M")


def _identifier(job: dict) -> str:
    if job.get("slug"):
        return job["slug"]
    if job.get("sha256"):
        return job["sha256"][:12]
    return str(job["id"])[:8]


def fetch_jobs(conn, limit: int = 25) -> list[dict]:
    return db.all_rows(
        conn,
        """
        SELECT j.id, j.state, j.lane, j.attempts, j.last_error, j.sha256,
               j.discovered_at, j.updated_at, sd.slug
          FROM ingest_job j
          LEFT JOIN source_document sd ON sd.id = j.document_id
         ORDER BY j.updated_at DESC
         LIMIT %s
        """,
        (limit,),
    )


def fetch_stage_runs(conn, job_ids: list[str]) -> dict[str, list[dict]]:
    if not job_ids:
        return {}
    rows = db.all_rows(
        conn,
        """
        SELECT job_id, stage, status, duration_ms, error
          FROM ingest_stage_run
         WHERE job_id = ANY(%s)
         ORDER BY started_at
        """,
        (job_ids,),
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["job_id"]), []).append(r)
    return out


def render(jobs: list[dict], stages: dict[str, list[dict]]) -> str:
    if not jobs:
        return "ingest_status: no ingest_job rows"

    header = f"{'identifier':<28} {'state':<13} {'lane':<5} {'att':>3}  {'updated':<16}  last_error"
    lines = [header, "-" * max(len(header), 90)]
    for job in jobs:
        ident = _identifier(job)
        err = (job["last_error"] or "")[:60]
        lines.append(
            f"{ident:<28} {job['state']:<13} {job['lane']:<5} {job['attempts']:>3}  "
            f"{_fmt_ts(job['updated_at']):<16}  {err}"
        )
        for s in stages.get(str(job["id"]), []):
            dur = f"{s['duration_ms']}ms" if s["duration_ms"] is not None else "-"
            serr = f"  {s['error'][:80]}" if s["error"] else ""
            lines.append(f"    {s['stage']:<13} {s['status']:<8} {dur:>8}{serr}")
    return "\n".join(lines)


def prune(conn, apply: bool) -> int:
    """Remove jobs that never produced a document.

    The rule is deliberately narrow: `document_id IS NULL`. A job that
    registered a document is that document's provenance -- when it was picked
    up, what the classifier thought it was, which stages ran -- and deleting it
    would throw away the only record of how the corpus got here. A job with no
    document produced nothing, so there is nothing to lose.

    This exists because four smoke-test files (42-1231 bytes, all synthetic)
    sat in the ingest view as the only rows it had, while all 14 real documents
    were loaded through the tools directly and have no job at all.
    """
    doomed = db.all_rows(
        conn,
        """
        SELECT j.id, j.state::text AS state, j.sha256, j.size_bytes, j.discovered_at
          FROM ingest_job j
         WHERE j.document_id IS NULL
         ORDER BY j.discovered_at
        """,
    )
    if not doomed:
        print("no jobs without a document -- nothing to prune")
        return 0

    for j in doomed:
        print(f"  {j['state']:<13} {j['sha256'][:12]}  {j['size_bytes']:>10} bytes  {_fmt_ts(j['discovered_at'])}")
    if not apply:
        print(f"{len(doomed)} job(s) would be removed. Re-run with --yes to apply.")
        return 0

    ids = [j["id"] for j in doomed]
    with conn.transaction():
        # ingest_stage_run cascades on job_id (db/schema.sql), so the stage
        # history goes with it rather than being orphaned.
        db._exec(conn, "DELETE FROM ingest_job WHERE id = ANY(%s)", (ids,))
    print(f"pruned {len(ids)} job(s) and their stage runs")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ingest_status")
    ap.add_argument("--limit", type=int, default=25, help="number of jobs to show (default 25)")
    ap.add_argument("--prune", action="store_true",
                    help="list jobs that never produced a document; add --yes to delete them")
    ap.add_argument("--yes", action="store_true", help="required for --prune to write")
    args = ap.parse_args(argv)

    with db.connect() as conn:
        if args.prune:
            return prune(conn, args.yes)
        jobs = fetch_jobs(conn, args.limit)
        stages = fetch_stage_runs(conn, [str(j["id"]) for j in jobs])

    print(render(jobs, stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
