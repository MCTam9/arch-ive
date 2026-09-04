import { requireSession } from "@/lib/session";
import { listIngestJobs } from "@/lib/queries";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

const TERMINAL_STATES = new Set(["done", "failed"]);

export default async function IngestPage() {
  const session = await requireSession();
  const { jobs, stages } = await listIngestJobs(session.accountId);

  const stagesByJob = new Map<string, typeof stages>();
  for (const s of stages) {
    const list = stagesByJob.get(s.job_id) ?? [];
    list.push(s);
    stagesByJob.set(s.job_id, list);
  }

  return (
    <div style={{ padding: "var(--s-6)" }}>
      <h1 className="font-display" style={{ fontSize: "var(--fs-h1)", margin: "0 0 var(--s-2) 0" }}>
        Ingest
      </h1>
      <p className="text-muted font-body" style={{ marginBottom: "var(--s-6)" }}>
        {jobs.length} job{jobs.length === 1 ? "" : "s"} · {jobs.filter((j) => !TERMINAL_STATES.has(j.state)).length} in flight
      </p>

      {jobs.length === 0 ? (
        <p className="font-body text-muted">No ingest jobs recorded.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          {jobs.map((job) => {
            const jobStages = stagesByJob.get(job.id) ?? [];
            return (
              <div key={job.id} className="card" style={{ padding: "var(--s-4)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-3)", flexWrap: "wrap" }}>
                  <div>
                    <Mono style={{ fontWeight: 700 }}>{job.original_filename}</Mono>
                    <div className="text-muted font-mono" style={{ fontSize: "var(--fs-micro)" }}>
                      {job.source_path}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center" }}>
                    <StateChip state={job.state} />
                    <span className="font-mono text-muted" style={{ fontSize: "var(--fs-micro)" }}>
                      {job.lane}
                    </span>
                  </div>
                </div>

                <div className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--s-2) 0" }}>
                  {job.doc_kind_guess ?? "unclassified"}
                  {job.classification_confidence != null ? ` (${job.classification_confidence})` : ""} · attempts:{" "}
                  {job.attempts} · updated {job.updated_at.slice(0, 19).replace("T", " ")}
                </div>

                {job.last_error && (
                  <p
                    className="font-mono"
                    style={{
                      background: "var(--n200)",
                      border: "var(--border-width) solid var(--n900)",
                      padding: "var(--s-2)",
                      fontSize: "var(--fs-sm)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {job.last_error}
                  </p>
                )}

                {jobStages.length > 0 && (
                  <ol
                    style={{
                      display: "flex",
                      gap: "var(--s-1)",
                      flexWrap: "wrap",
                      listStyle: "none",
                      padding: 0,
                      margin: "var(--s-2) 0 0 0",
                    }}
                  >
                    {jobStages.map((s, i) => (
                      <li
                        key={i}
                        className="font-mono"
                        title={s.error ?? undefined}
                        style={{
                          fontSize: "var(--fs-micro)",
                          padding: "2px var(--s-1)",
                          border: "var(--border-width) solid var(--border)",
                          background: s.status === "ok" ? "var(--surface)" : s.status === "failed" ? "var(--n200)" : "var(--surface-sunken)",
                          color: s.status === "failed" ? "var(--level-2)" : "inherit",
                        }}
                      >
                        {s.stage}
                        {s.duration_ms != null ? ` ${s.duration_ms}ms` : ""}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StateChip({ state }: { state: string }) {
  const bg = state === "done" ? "var(--level-3)" : state === "failed" ? "var(--level-2)" : state === "needs_review" ? "var(--n500)" : "var(--n400)";
  return (
    <span
      className="font-display"
      style={{
        fontSize: "var(--fs-micro)",
        padding: "var(--s-1) var(--s-2)",
        background: bg,
        color: "var(--n900)",
      }}
    >
      {state}
    </span>
  );
}
