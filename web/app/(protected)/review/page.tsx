import Link from "next/link";
import { requireSession } from "@/lib/session";
import { listReviewQueue, recordReview, REVIEW_STATUSES, type ReviewFilter } from "@/lib/queries";
import { DraftWrapper } from "@/components/draft-wrapper";
import { revalidatePath } from "next/cache";

export const dynamic = "force-dynamic";

export default async function ReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ document?: string; offset?: string; status?: string }>;
}) {
  const session = await requireSession();
  const sp = await searchParams;
  const offset = Number(sp.offset ?? 0) || 0;
  const status: ReviewFilter = REVIEW_STATUSES.includes(sp.status as ReviewFilter)
    ? (sp.status as ReviewFilter)
    : "pending";
  const canReview = session.role === "owner" || session.role === "editor";
  const { items, total } = await listReviewQueue(session.accountId, {
    document: sp.document,
    status,
    offset,
  });

  // Every link keeps the filter and the document, or paging out of a filtered
  // view silently drops you back into 'pending'.
  const href = (over: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    if (sp.document) q.set("document", sp.document);
    if (status !== "pending") q.set("status", status);
    for (const [k, v] of Object.entries(over)) {
      if (v === undefined) q.delete(k);
      else q.set(k, String(v));
    }
    const qs = q.toString();
    return `/review${qs ? `?${qs}` : ""}`;
  };

  async function decide(formData: FormData) {
    "use server";
    const s = await requireSession();
    // Role is checked on the server, not just hidden in the UI: a reader who
    // posts this form directly must still be refused.
    if (s.role !== "owner" && s.role !== "editor") return;
    const id = String(formData.get("id"));
    const decision = String(formData.get("decision"));
    if (decision !== "approved" && decision !== "rejected" && decision !== "pending") return;
    await recordReview(s.accountId, id, decision);
    revalidatePath("/review");
  }

  return (
    <div style={{ padding: "var(--s-6)", maxWidth: 1400, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: "var(--s-6)" }}>
        <h1 className="font-display" style={{ fontSize: "var(--fs-h1)", margin: 0 }}>Review</h1>
        <span className="font-mono text-muted">{total} {status === "all" ? "total" : status}</span>
      </header>

      {/* The page scan renders only on this view, so a decided item has to
          stay reachable — otherwise approving something is also the act of
          hiding the evidence it was approved against. */}
      <nav style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-5)" }}>
        {REVIEW_STATUSES.map((s) => (
          <Link
            key={s}
            href={s === "pending" ? href({ status: undefined, offset: undefined }) : `/review?${new URLSearchParams({ ...(sp.document ? { document: sp.document } : {}), status: s })}`}
            className="font-display transition-fast"
            style={{
              padding: "var(--s-2) var(--s-3)",
              fontSize: "var(--fs-label)",
              textDecoration: "none",
              border: "var(--border-width) solid var(--border-strong)",
              background: s === status ? "var(--accent)" : "transparent",
              color: s === status ? "var(--accent-text)" : "var(--text)",
            }}
          >
            {s}
          </Link>
        ))}
      </nav>

      {!canReview && (
        <p className="font-mono" style={{ background: "var(--surface-sunken)", color: "var(--text)", border: "var(--border-width) solid var(--border-strong)", padding: "var(--s-3)", marginBottom: "var(--s-4)" }}>
          Read-only: approving an extraction needs the editor or owner role.
        </p>
      )}

      {items.length === 0 ? (
        <p className="font-body text-muted">
          {status === "pending"
            ? "Nothing pending. Every extracted record has been decided — the approved tab still shows each record beside its page scan."
            : `No ${status} records.`}
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-6)" }}>
          {items.map((it) => (
            <li key={it.id} className="card" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 420px", gap: "var(--s-4)", padding: "var(--s-4)" }}>
              <div style={{ minWidth: 0 }}>
                <div className="font-display" style={{ fontSize: "var(--fs-micro)", marginBottom: "var(--s-2)" }}>
                  {it.item_type}
                  {it.extraction_confidence != null && (
                    <span className="font-mono text-muted"> · confidence {Number(it.extraction_confidence).toFixed(2)}</span>
                  )}
                  {it.review_status !== "pending" && (
                    <span className="font-mono text-muted"> · {it.review_status}</span>
                  )}
                </div>
                <DraftWrapper status={it.content_status}>
                  {it.title && <p className="font-mono" style={{ margin: "0 0 var(--s-2)" }}>{it.title}</p>}
                  <p className="font-body" style={{ margin: 0 }}>{it.statement}</p>
                </DraftWrapper>
                <p className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)", marginTop: "var(--s-3)" }}>
                  {it.document_slug}
                  {it.page_index != null && ` · pdf p${it.page_index}`}
                  {it.printed_page_label && ` · printed ${it.printed_page_label}`}
                </p>
                <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
                  {/* Only offer the decisions that would change something.
                      An Approve button on an already-approved record invites a
                      click that does nothing. */}
                  {it.review_status !== "approved" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="approved" />
                      <button type="submit" className="font-display transition-fast" disabled={!canReview}
                        style={{ padding: "var(--s-2) var(--s-4)", background: "var(--accent)", color: "var(--accent-text)", border: "var(--border-width) solid var(--border-strong)" }}>
                        Approve
                      </button>
                    </form>
                  )}
                  {it.review_status !== "rejected" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="rejected" />
                      <button type="submit" className="font-display transition-fast" disabled={!canReview}
                        style={{ padding: "var(--s-2) var(--s-4)", background: "transparent", color: "var(--text)", border: "var(--border-width) solid var(--border-strong)" }}>
                        Reject
                      </button>
                    </form>
                  )}
                  {it.review_status !== "pending" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="pending" />
                      <button type="submit" className="font-display transition-fast" disabled={!canReview}
                        style={{ padding: "var(--s-2) var(--s-4)", background: "transparent", color: "var(--text-muted)", border: "var(--border-width) dashed var(--border-strong)" }}>
                        Reopen
                      </button>
                    </form>
                  )}
                  <Link href={`/item/${it.id}`} className="font-mono text-muted" style={{ alignSelf: "center", fontSize: "var(--fs-sm)" }}>
                    open
                  </Link>
                </div>
              </div>

              {/* The scan, beside the record. No grain over it: this is the
                  surface where a person compares extracted text against the
                  original, and texture there is noise in the literal sense. */}
              <div style={{ border: "var(--border-width) solid var(--border)", background: "var(--surface-sunken)", minHeight: 200 }}>
                {it.page_image_key ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={`/api/page-image/${it.page_image_key.replace(/^pages\//, "")}`}
                    alt={`${it.document_slug} page ${it.page_index}`}
                    style={{ width: "100%", height: "auto", display: "block" }}
                  />
                ) : (
                  <p className="font-mono text-muted" style={{ padding: "var(--s-4)", fontSize: "var(--fs-sm)" }}>
                    no page render for this citation
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <nav style={{ display: "flex", gap: "var(--s-4)", marginTop: "var(--s-6)" }}>
        {offset > 0 && (
          <Link className="font-mono" href={href({ offset: Math.max(0, offset - 25) })}>&larr; previous</Link>
        )}
        {offset + items.length < total && (
          <Link className="font-mono" href={href({ offset: offset + 25 })}>next &rarr;</Link>
        )}
      </nav>
    </div>
  );
}
