import Link from "next/link";
import { requireSession } from "@/lib/session";
import { getFacetOptions, listKnowledgeItems, type BrowseFilters } from "@/lib/queries";
import { Mono, CiteRef } from "@/components/mono";
import { StatusFlag } from "@/components/draft-wrapper";

export const dynamic = "force-dynamic";

type SearchParams = {
  document?: string;
  item_type?: string;
  topic?: string;
  scale?: string;
  level?: string;
  q?: string;
};

export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const session = await requireSession();
  const sp = await searchParams;

  const filters: BrowseFilters = {
    documentSlug: sp.document || undefined,
    itemType: sp.item_type || undefined,
    topicId: sp.topic || undefined,
    scaleId: sp.scale || undefined,
    levelId: sp.level || undefined,
    q: sp.q || undefined,
  };

  const [facets, items] = await Promise.all([
    getFacetOptions(session.accountId),
    listKnowledgeItems(session.accountId, filters),
  ]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", minHeight: "calc(100dvh - 60px)" }}>
      <aside
        className="chrome"
        style={{
          borderRight: "var(--border-width) solid var(--chrome-border)",
          padding: "var(--s-4)",
        }}
      >
        <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-4) 0" }}>
          Facets
        </h2>
        <form method="GET" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          <FacetSelect name="document" label="Document" value={sp.document} options={facets.documents.map((d) => ({ value: d.slug, label: d.slug }))} />
          <FacetSelect
            name="item_type"
            label="Item type"
            value={sp.item_type}
            options={["requirement", "benchmark", "guidance", "pattern", "template", "definition", "process_step", "role"].map((t) => ({ value: t, label: t }))}
          />
          <FacetSelect name="topic" label="Topic" value={sp.topic} options={facets.topics.map((t) => ({ value: t.id, label: t.label }))} />
          <FacetSelect name="scale" label="Scale" value={sp.scale} options={facets.scales.map((t) => ({ value: t.id, label: t.label }))} />
          <FacetSelect name="level" label="Level" value={sp.level} options={facets.levels.map((t) => ({ value: t.id, label: t.label }))} />
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <span className="font-display" style={{ fontSize: "var(--fs-micro)" }}>
              Search
            </span>
            <input
              name="q"
              defaultValue={sp.q}
              className="font-body"
              style={{ padding: "var(--s-2)", border: "var(--border-width) solid var(--chrome-border)" }}
            />
          </label>
          <button
            type="submit"
            className="font-display transition-fast"
            style={{
              padding: "var(--s-2)",
              background: "var(--accent)",
              color: "var(--accent-text)",
              border: "var(--border-width-strong) solid var(--border-strong)",
              fontSize: "var(--fs-label)",
              cursor: "pointer",
            }}
          >
            Apply
          </button>
          <Link href="/" className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            Clear filters
          </Link>
        </form>
      </aside>

      <section style={{ padding: "var(--s-4) var(--s-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "var(--s-4)" }}>
          <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: 0 }}>
            Browse
          </h1>
          <Mono className="text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            {items.length} item{items.length === 1 ? "" : "s"}
          </Mono>
        </div>

        {items.length === 0 ? (
          <EmptyState />
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            {items.map((item) => (
              <li key={item.id} className="card transition-fast" style={{ padding: "var(--s-4)" }}>
                <Link href={`/item/${item.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-3)", alignItems: "flex-start" }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-1)" }}>
                        <span className="font-display" style={{ fontSize: "var(--fs-micro)", color: "var(--text-muted)" }}>
                          {item.item_type}
                        </span>
                        <StatusFlag status={item.content_status} />
                      </div>
                      <p className="font-body" style={{ margin: 0, fontWeight: 600 }}>
                        {item.title || item.statement?.slice(0, 120) || "(untitled)"}
                      </p>
                      {item.statement && item.title && (
                        <p className="font-body text-muted" style={{ margin: "var(--s-1) 0 0 0", fontSize: "var(--fs-sm)" }}>
                          {item.statement.slice(0, 160)}
                        </p>
                      )}
                    </div>
                    <CiteRef documentSlug={item.document_slug} pageIndex={item.page_index} printedPageLabel={item.printed_page_label} />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function FacetSelect({
  name,
  label,
  value,
  options,
}: {
  name: string;
  label: string;
  value?: string;
  options: { value: string; label: string }[];
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
      <span className="font-display" style={{ fontSize: "var(--fs-micro)" }}>
        {label}
      </span>
      <select
        name={name}
        defaultValue={value ?? ""}
        className="font-mono"
        style={{ padding: "var(--s-2)", border: "var(--border-width) solid var(--chrome-border)" }}
      >
        <option value="">all</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyState() {
  return (
    <div className="card" style={{ padding: "var(--s-10)", textAlign: "center" }}>
      <p className="font-display" style={{ fontSize: "var(--fs-h3)", margin: "0 0 var(--s-2) 0" }}>
        No matching items
      </p>
      <p className="text-muted font-body">
        Topic / scale / level facets rely on item_term tagging, which the enrichment
        stage of the pipeline populates — it may not have run yet for this corpus.
        Try clearing a filter.
      </p>
    </div>
  );
}
