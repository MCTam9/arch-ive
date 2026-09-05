import { requireSession } from "@/lib/session";
import { listIngestJobs } from "@/lib/queries";
import { Mono } from "@/components/mono";
import { EmptyState, PageHeader } from "@/components/ui";

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
      <PageHeader
        title="Ingest"
        meta={`${jobs.length} job${jobs.length === 1 ? "" : "s"} · ${jobs.filter((j) => !TERMINAL_STATES.has(j.state)).length} in flight`}
      >
        What has come through the <code>inbox/</code> folder. Documents loaded through the
        tools directly have no job here.
      </PageHeader>

      {jobs.length === 0 ? (
        <EmptyState title="No ingest jobs">
          This view describes what came through the <code>inbox/</code> folder. The corpus was
          loaded through the tools directly, so those documents have no job rows and never
          appear here.
        </EmptyState>
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
                    // Semantic tokens, not raw ramp values. This block set a
                    // fixed near-white background and no colour, so in dark
                    // mode it inherited near-white text: an error message that
                    // was invisible exactly when you needed it. The same bug
                    // was fixed on /login and left standing here.
                    style={{
                      background: "var(--surface-sunken)",
                      color: "var(--text)",
                      border: "var(--border-width) solid var(--border-strong)",
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
                        // A failed chip was amber text on a fixed near-white
                        // fill — about 1.4:1, unreadable. The band colours are
                        // data encoding meant to be a FILL with near-black on
                        // top, which is how they are used everywhere else.
                        style={{
                          fontSize: "var(--fs-micro)",
                          padding: "var(--s-1)",
                          border: `var(--border-width) solid ${s.status === "failed" ? "var(--border-strong)" : "var(--border)"}`,
                          background:
                            s.status === "failed"
                              ? "var(--level-2)"
                              : s.status === "ok"
                                ? "var(--surface)"
                                : "var(--surface-sunken)",
                          color: s.status === "failed" ? "var(--level-2-text)" : "var(--text)",
                        }}
                      >
                        {/* The status is spelled out, not left to colour
                            alone: every chip's visible label was the stage
                            name, identical in all three states. */}
                        {s.stage}
                        {s.status === "failed" ? " failed" : ""}
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
  // The band colours are a fill that takes near-black text — that is what
  // --level-N-text is for, and the raw ramp values used here before did not
  // follow the theme. `done` and `failed` keep a band colour because they are
  // the two states worth spotting from across the list; everything else is a
  // neutral surface rather than another colour competing with them.
  const styles: Record<string, { background: string; color: string }> = {
    done: { background: "var(--level-3)", color: "var(--level-3-text)" },
    failed: { background: "var(--level-2)", color: "var(--level-2-text)" },
  };
  const tone = styles[state] ?? { background: "var(--surface-sunken)", color: "var(--text)" };
  return (
    // Mono, not the display face: `needs_review` rendered as NEEDS_REVIEW, and
    // these are enum values, not chrome.
    <span
      className="font-mono"
      style={{
        fontSize: "var(--fs-micro)",
        padding: "var(--s-1) var(--s-2)",
        border: "var(--border-width) solid var(--border)",
        ...tone,
      }}
    >
      {state.replace(/_/g, " ")}
    </span>
  );
}
