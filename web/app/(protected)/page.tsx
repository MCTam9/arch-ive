import Link from "next/link";
import { redirect } from "next/navigation";
import { requireSession } from "@/lib/session";
import { getHomeSummary } from "@/lib/queries";
import { BROWSE_PATH, browseHref, browseParams } from "@/lib/links";
import { Mono } from "@/components/mono";
import { EmptyState } from "@/components/ui";

export const dynamic = "force-dynamic";

// The launcher. `/` used to be the results list itself, so arriving signed in
// dropped you on page 1 of all 771 items in document order -- a view that
// answers no question anyone had.
//
// It is shaped like a search home page, but deliberately not blank like one.
// Google's home page is empty because its index is unknowable and there is
// nothing honest to put there. This corpus is 771 items across 14 documents:
// small enough to show. So the grid is not only navigation -- it is the size
// and shape of what you are about to search, which is the thing a newcomer
// most needs and the thing a blank box cannot give them.

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const sp = await searchParams;

  // Belt and braces. proxy.ts already turns `/?topic=…` into a real 307 to
  // /browse before anything renders, which is what a shared link deserves.
  // This is the same rule enforced where it cannot be bypassed: if the proxy
  // is ever skipped, an old link still keeps its filters instead of silently
  // arriving on a page that ignores them. A redirect thrown here is a 200 with
  // a client-side hop, because the layout has already streamed -- correct, but
  // second best, which is why it is second.
  const inherited = browseParams(sp);
  if (Object.keys(inherited).length > 0) redirect(browseHref(inherited));

  const { topics, itemTypes, totals } = await getHomeSummary(session.accountId);

  if (totals.items === 0) {
    return (
      <div style={{ maxWidth: "60ch", margin: "0 auto", padding: "var(--s-16) var(--s-6)" }}>
        <EmptyState title="Nothing ingested yet" action={{ href: "/ingest", label: "Go to ingest" }}>
          The corpus is empty, so there is nothing to search. Run a document
          through the ingest pipeline and it will appear here.
        </EmptyState>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", padding: "var(--s-16) var(--s-6) var(--s-12)" }}>
      <div style={{ textAlign: "center", marginBottom: "var(--s-8)" }}>
        <h1 className="font-display" style={{ fontSize: "var(--fs-h1)", margin: "0 0 var(--s-2)" }}>
          arch-ive
        </h1>
        <p className="font-body text-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
          Architecture knowledge base
        </p>
      </div>

      {/* A plain GET form pointed at browse. No JS, no client component, no
          redirect handler -- the browser turns this into /browse?q=… by
          itself, which is exactly the behaviour wanted and none of the code.
          Same pattern as the filter rail, which has always worked this way. */}
      <form
        method="GET"
        action={BROWSE_PATH}
        role="search"
        style={{ display: "flex", gap: "var(--s-2)", marginBottom: "var(--s-4)" }}
      >
        <label htmlFor="home-q" className="sr-only">
          Search the corpus
        </label>
        <input
          id="home-q"
          name="q"
          type="search"
          autoFocus
          placeholder="e.g. embodied carbon 2030"
          className="field font-body"
          style={{ flex: 1, padding: "var(--s-3)", fontSize: "var(--fs-body)" }}
        />
        <button type="submit" className="btn btn-primary font-display" style={{ padding: "0 var(--s-5)" }}>
          Search
        </button>
      </form>

      {/* Counts, not adjectives. Figures are deliberately absent: 898 of them
          carry a description, but search cannot reach any of it (queries.ts
          inner-joins knowledge_item), and advertising them on the page whose
          job is to launch a search would promise something it does not do. */}
      <Mono
        className="text-muted"
        style={{ display: "block", textAlign: "center", fontSize: "var(--fs-sm)", marginBottom: "var(--s-10)" }}
      >
        {totals.items} items · {totals.documents} documents · {totals.pages} pages ·{" "}
        {totals.chunks} chunks
      </Mono>

      <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-3)" }}>
        Topics
      </h2>
      <ul
        style={{
          listStyle: "none",
          margin: "0 0 var(--s-8)",
          padding: 0,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: "var(--s-3)",
        }}
      >
        {topics.map((t) => (
          <li key={t.id} style={{ display: "flex" }}>
            <Link
              href={browseHref({ topic: t.id })}
              className="card card-link"
              style={{
                flex: 1,
                padding: "var(--s-3)",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                gap: "var(--s-4)",
                minHeight: "5.5rem",
              }}
            >
              <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
                {t.label}
              </span>
              {/* The count is data, so it is mono -- and it is the reason the
                  tile exists rather than a decoration on it. */}
              <Mono className="text-muted" style={{ fontSize: "var(--fs-sm)", textAlign: "right" }}>
                {t.n}
              </Mono>
            </Link>
          </li>
        ))}
      </ul>

      {/* Topic covers 487 of 771 items -- the largest document carries scale
          and building-use tags but no topic at all, so a topic grid on its own
          quietly hides a third of the corpus. Every item has exactly one
          item_type, so this row is the half that closes the gap. */}
      <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-3)" }}>
        Item type
      </h2>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-8)" }}>
        {itemTypes.map((t) => (
          <Link key={t.id} href={browseHref({ item_type: t.id })} className="chip font-mono">
            {t.id.replace(/_/g, " ")} <span className="text-muted">{t.n}</span>
          </Link>
        ))}
      </div>

      <p style={{ textAlign: "center", margin: 0 }}>
        <Link href={BROWSE_PATH} className="link font-mono">
          Browse all {totals.items} items &rarr;
        </Link>
      </p>
    </div>
  );
}
