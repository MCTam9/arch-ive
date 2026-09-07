import Link from "next/link";
import { requireSession } from "@/lib/session";
import { listReviewQueue, recordReview, REVIEW_STATUSES } from "@/lib/queries";
import { backParams, href } from "@/lib/links";
import {
  REVIEW_PAGE_SIZE,
  first,
  parseOffset,
  parseReviewStatus,
  type RawSearchParams,
} from "@/lib/params";
import { DraftWrapper } from "@/components/draft-wrapper";
import { PageScan } from "@/components/page-scan";
import { Button, DataLabel, PageHeader, EmptyState } from "@/components/ui";
import { CiteRef } from "@/components/mono";
import { revalidatePath } from "next/cache";

export const dynamic = "force-dynamic";

export default async function ReviewPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const sp = await searchParams;
  // Parsed by lib/params.ts, not here. The offset clamp is the reason: this
  // page had none and listReviewQueue has none either, so /review?offset=-5
  // reached Postgres as OFFSET -5 and 500'd. The status allowlist is the
  // other: it is what makes listReviewQueue's interpolation of the status
  // into SQL safe, and an invariant that load-bearing gets one copy.
  const offset = parseOffset(sp.offset);
  const status = parseReviewStatus(sp.status);
  const documentSlug = first(sp, "document");
  const canReview = session.role === "owner" || session.role === "editor";
  const { items, total } = await listReviewQueue(session.accountId, {
    document: documentSlug,
    status,
    // Passed rather than defaulted: the two pagination links below step by
    // exactly this, and they used to type 25 against a separate default.
    limit: REVIEW_PAGE_SIZE,
    offset,
  });

  // Every link keeps the filter and the document, or paging out of a filtered
  // view silently drops you back into 'pending'. `undefined` in `over` clears
  // a key -- the spread carries it over the current value and href() drops it.
  const reviewHref = (over: Record<string, string | number | undefined> = {}) =>
    href("/review", {
      document: documentSlug,
      status: status === "pending" ? undefined : status,
      ...over,
    });

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
      <PageHeader title="Review" meta={`${total} ${status === "all" ? "total" : status}`} />

      {/* The page scan renders only on this view, so a decided item has to
          stay reachable — otherwise approving something is also the act of
          hiding the evidence it was approved against. */}
      {/* aria-current does two jobs at once here: it is how a screen reader is
          told which tab is selected — which nothing did before — and it is the
          hook `.tab[aria-current='page']` hangs the selected styling off, so
          the state is expressed once instead of as an inline ternary a hover
          rule could never override. */}
      {/* Switching tab returns to page 1: the offsets do not correspond
          between two differently-sized queues. The 'pending' tab clears the
          key rather than spelling out the default, so the queue's home URL is
          `/review` and not `/review?status=pending`. This used to be a second,
          inline URLSearchParams builder that dropped the offset only by
          never carrying it. */}
      <nav aria-label="Review status" style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-5)" }}>
        {REVIEW_STATUSES.map((s) => (
          <Link
            key={s}
            href={reviewHref({ status: s === "pending" ? undefined : s, offset: undefined })}
            aria-current={s === status ? "page" : undefined}
            className="tab font-display"
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
        <EmptyState title={status === "pending" ? "Nothing pending" : `No ${status} records`}>
          {status === "pending"
            ? "Every extracted record has been decided. The approved tab still shows each one beside its page scan."
            : "Nothing has been given this status yet."}
        </EmptyState>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-6)" }}>
          {items.map((it, i) => (
            <li key={it.id} className="card" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 420px", gap: "var(--s-4)", padding: "var(--s-4)" }}>
              <div style={{ minWidth: 0 }}>
                {/* Was the bitmap face at 10px, which both broke the 12px
                    floor and shouted PROCESS_STEP at an enum value. */}
                <div style={{ marginBottom: "var(--s-2)" }}>
                  <DataLabel>
                    {it.item_type.replace(/_/g, " ")}
                    {it.extraction_confidence != null &&
                      ` · confidence ${Number(it.extraction_confidence).toFixed(2)}`}
                    {it.review_status !== "pending" && ` · ${it.review_status}`}
                  </DataLabel>
                </div>
                <DraftWrapper status={it.content_status}>
                  {it.title && <p className="font-mono" style={{ margin: "0 0 var(--s-2)" }}>{it.title}</p>}
                  <p className="font-body" style={{ margin: 0 }}>{it.statement}</p>
                </DraftWrapper>
                <div style={{ marginTop: "var(--s-3)" }}>
                  <CiteRef
                    documentSlug={it.document_title ?? it.document_slug}
                    pageIndex={it.page_index}
                    printedPageLabel={it.printed_page_label}
                  />
                </div>
                <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
                  {/* Only offer the decisions that would change something.
                      An Approve button on an already-approved record invites a
                      click that does nothing. */}
                  {it.review_status !== "approved" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="approved" />
                      <Button variant="primary" disabled={!canReview}>
                        Approve
                      </Button>
                    </form>
                  )}
                  {it.review_status !== "rejected" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="rejected" />
                      <Button variant="secondary" disabled={!canReview}>
                        Reject
                      </Button>
                    </form>
                  )}
                  {it.review_status !== "pending" && (
                    <form action={decide}>
                      <input type="hidden" name="id" value={it.id} />
                      <input type="hidden" name="decision" value="pending" />
                      <Button variant="quiet" disabled={!canReview}>
                        Reopen
                      </Button>
                    </form>
                  )}
                  {/* Carries where it came from, so "back" returns to this
                      status tab at this offset rather than to page 1 of
                      pending. */}
                  <Link
                    href={href(`/item/${it.id}`, backParams("review", reviewHref({ offset: offset || undefined })))}
                    className="font-mono link"
                    style={{ alignSelf: "center", fontSize: "var(--fs-sm)" }}
                  >
                    open
                  </Link>
                </div>
              </div>

              {/* The scan, beside the record. Only the first is eager — the
                  other 24 used to be fetched at once, each opening its own
                  authenticated request and database transaction. */}
              <PageScan
                imageKey={it.page_image_key}
                documentLabel={it.document_title ?? it.document_slug}
                pageIndex={it.page_index}
                widthPt={it.width_pt}
                heightPt={it.height_pt}
                priority={i === 0}
              />
            </li>
          ))}
        </ul>
      )}

      {/* `link` was missing on both of these, so review's pagination rendered
          in body colour with the browser's default underline while browse's
          carried the accent — the same control, two appearances. */}
      <nav aria-label="Pages" style={{ display: "flex", gap: "var(--s-4)", marginTop: "var(--s-6)" }}>
        {offset > 0 && (
          <Link className="font-mono link" href={reviewHref({ offset: Math.max(0, offset - REVIEW_PAGE_SIZE) })}>&larr; previous</Link>
        )}
        {offset + items.length < total && (
          <Link className="font-mono link" href={reviewHref({ offset: offset + REVIEW_PAGE_SIZE })}>next &rarr;</Link>
        )}
      </nav>
    </div>
  );
}
