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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ingest_status")
    ap.add_argument("--limit", type=int, default=25, help="number of jobs to show (default 25)")
    args = ap.parse_args(argv)

    with db.connect() as conn:
        jobs = fetch_jobs(conn, args.limit)
        stages = fetch_stage_runs(conn, [str(j["id"]) for j in jobs])

    print(render(jobs, stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
