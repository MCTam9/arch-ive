import Link from "next/link";
import { requireSession } from "@/lib/session";
import {
  getFacetOptions,
  listKnowledgeItems,
  BROWSE_PAGE_SIZE,
  ITEM_TYPES,
  type BrowseFilters,
  type BrowseItem,
  type FacetOptions,
} from "@/lib/queries";
import { href, browseParams } from "@/lib/links";
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
  offset?: string;
};

export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const session = await requireSession();
  const sp = await searchParams;
  const offset = Math.max(Number(sp.offset ?? 0) || 0, 0);

  const filters: BrowseFilters = {
    documentSlug: sp.document || undefined,
    itemType: sp.item_type || undefined,
    topicId: sp.topic || undefined,
    scaleId: sp.scale || undefined,
    levelId: sp.level || undefined,
    q: sp.q || undefined,
  };

  const [facets, { items, total }] = await Promise.all([
    getFacetOptions(session.accountId),
    listKnowledgeItems(session.accountId, filters, { offset }),
  ]);

  const current = browseParams(sp as Record<string, string | undefined>);
  const ranked = Boolean(filters.q);

  // Every generated link keeps the rest of the filters. Changing a facet
  // returns to page 1; paging keeps everything and moves only the offset.
  const withFilters = (over: Record<string, string | number | undefined>) =>
    href("/", { ...current, ...over });

  const docLabel = (slug: string) =>
    facets.documents.find((d) => d.slug === slug)?.title ?? slug;
  const termLabel = (id: string) =>
    [...facets.topics, ...facets.scales, ...facets.levels].find((t) => t.id === id)?.label ?? id;

  const chips: { key: keyof typeof current; label: string }[] = [];
  if (current.document) chips.push({ key: "document", label: docLabel(current.document) });
  if (current.item_type) chips.push({ key: "item_type", label: current.item_type.replace(/_/g, " ") });
  for (const k of ["topic", "scale", "level"] as const) {
    if (current[k]) chips.push({ key: k, label: termLabel(current[k]!) });
  }
  if (current.q) chips.push({ key: "q", label: `“${current.q}”` });

  const from = total === 0 ? 0 : offset + 1;
  const to = offset + items.length;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 260px) 1fr" }}>
      <aside
        className="chrome"
        style={{
          borderRight: "var(--border-width) solid var(--chrome-border)",
          padding: "var(--s-4)",
        }}
      >
        <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-4) 0" }}>
          Filters
        </h2>
        <form method="GET" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          {/* Changing any filter starts again at page 1 — keeping the old
              offset lands you on an empty page and looks like no results. */}
          <input type="hidden" name="offset" value="0" />

          <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
              Search text
            </span>
            <input
              name="q"
              defaultValue={sp.q}
              placeholder="e.g. embodied carbon"
              className="font-body"
              style={{ padding: "var(--s-2)", border: "var(--border-width) solid var(--chrome-border)" }}
            />
          </label>

          <FacetSelect
            name="document"
            label="Document"
            value={sp.document}
            options={facets.documents.map((d) => ({
              value: d.slug,
              label: d.title ?? d.slug,
              n: d.n,
            }))}
          />
          <FacetSelect
            name="item_type"
            label="Item type"
            value={sp.item_type}
            options={ITEM_TYPES.map((t) => ({ value: t, label: t.replace(/_/g, " ") }))}
          />
          <FacetSelect name="topic" label="Topic" value={sp.topic} options={toOptions(facets.topics)} />
          <FacetSelect name="scale" label="Scale" value={sp.scale} options={toOptions(facets.scales)} />
          <FacetSelect name="level" label="Level" value={sp.level} options={toOptions(facets.levels)} />

          <button
            type="submit"
            className="font-display transition-fast"
            style={{
              padding: "var(--s-2) var(--s-4)",
              background: "var(--accent)",
              color: "var(--accent-text)",
              border: "var(--border-width-strong) solid var(--border-strong)",
              fontSize: "var(--fs-label)",
              cursor: "pointer",
            }}
          >
            Apply
          </button>
        </form>
      </aside>

      <section style={{ padding: "var(--s-4) var(--s-6)", minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            gap: "var(--s-4)",
            flexWrap: "wrap",
            marginBottom: "var(--s-3)",
          }}
        >
          <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: 0 }}>
            Browse
          </h1>
          <Mono className="text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            {total === 0
              ? "no results"
              : `${from}–${to} of ${total} · ${ranked ? "by relevance" : "document order"}`}
          </Mono>
        </div>

        {chips.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--s-2)",
              alignItems: "center",
              marginBottom: "var(--s-4)",
            }}
          >
            {chips.map((chip) => (
              <Link
                key={chip.key}
                href={withFilters({ [chip.key]: undefined, offset: undefined })}
                className="font-mono transition-fast"
                title={`Remove ${chip.key.replace(/_/g, " ")} filter`}
                style={{
                  fontSize: "var(--fs-sm)",
                  padding: "var(--s-1) var(--s-2)",
                  border: "var(--border-width) solid var(--border-strong)",
                  background: "var(--accent-surface)",
                  color: "var(--text)",
                  textDecoration: "none",
                }}
              >
                {chip.label} <span aria-hidden="true">×</span>
              </Link>
            ))}
            <Link href="/" className="font-mono link" style={{ fontSize: "var(--fs-sm)" }}>
              Clear all
            </Link>
          </div>
        )}

        {items.length === 0 ? (
          <EmptyState hasFilters={chips.length > 0} />
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            {items.map((item) => (
              <li key={item.id}>
                <Link href={`/item/${item.id}?from=browse&ret=${encodeURIComponent(withFilters({}))}`} className="card card-link transition-fast" style={{ padding: "var(--s-4)" }}>
                  <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-2)" }}>
                    <span className="font-mono text-muted" style={{ fontSize: "var(--fs-micro)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {item.item_type.replace(/_/g, " ")}
                    </span>
                    <StatusFlag status={item.content_status} />
                  </div>
                  <p className="font-body" style={{ margin: 0, fontWeight: 600 }}>
                    {item.title || item.statement?.slice(0, 120) || "(untitled)"}
                  </p>
                  {item.snippet ? (
                    <p className="font-body text-muted" style={{ margin: "var(--s-2) 0 0", fontSize: "var(--fs-sm)" }}>
                      <Highlighted text={item.snippet} />
                    </p>
                  ) : (
                    item.title &&
                    item.statement && (
                      <p className="font-body text-muted" style={{ margin: "var(--s-2) 0 0", fontSize: "var(--fs-sm)" }}>
                        {item.statement.slice(0, 160)}
                        {item.statement.length > 160 ? "…" : ""}
                      </p>
                    )
                  )}
                  <div style={{ marginTop: "var(--s-3)" }}>
                    <CiteRef
                      documentSlug={item.document_title ?? item.document_slug}
                      pageIndex={item.page_index}
                      printedPageLabel={item.printed_page_label}
                    />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {total > items.length && (
          <nav aria-label="Pages" style={{ display: "flex", gap: "var(--s-4)", marginTop: "var(--s-6)", alignItems: "baseline" }}>
            {offset > 0 && (
              <Link className="font-mono link" href={withFilters({ offset: Math.max(0, offset - BROWSE_PAGE_SIZE) })}>
                &larr; previous
              </Link>
            )}
            {to < total && (
              <Link className="font-mono link" href={withFilters({ offset: offset + BROWSE_PAGE_SIZE })}>
                next &rarr;
              </Link>
            )}
          </nav>
        )}
      </section>
    </div>
  );
}

function toOptions(terms: FacetOptions["topics"]) {
  // An option that can only ever return nothing is worse than no option: the
  // taxonomy defines 27 topics and 26 are used, but stage and project_type are
  // barely tagged at all, so a term with no items is dropped rather than shown
  // as a filter that silently empties the page.
  return terms.filter((t) => t.n > 0).map((t) => ({ value: t.id, label: t.label, n: t.n }));
}

/** ts_headline marks matches with [[…]]; render them as <mark> without ever
 *  putting corpus text through dangerouslySetInnerHTML. */
function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text.split(/\[\[|\]\]/).map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} style={{ background: "var(--accent-surface)", color: "var(--text)" }}>
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
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
  options: { value: string; label: string; n?: number }[];
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
      <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
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
            {o.n !== undefined ? ` (${o.n})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyState({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div className="card" style={{ padding: "var(--s-10)", textAlign: "center" }}>
      <h2 className="font-display" style={{ fontSize: "var(--fs-h3)", margin: "0 0 var(--s-3)" }}>
        No matching records
      </h2>
      <p className="font-body text-muted" style={{ margin: "0 auto", maxWidth: "52ch" }}>
        {hasFilters
          ? "Filters combine with AND, so a narrow topic and a specific document together can easily match nothing. Remove one and try again."
          : "Nothing in the corpus matches. If this is unexpected, the enrichment stage that tags items with topic, scale and level may not have run for the newest documents."}
      </p>
      {hasFilters && (
        <p style={{ marginTop: "var(--s-4)", marginBottom: 0 }}>
          <Link href="/" className="font-display link" style={{ fontSize: "var(--fs-label)" }}>
            Clear all filters
          </Link>
        </p>
      )}
    </div>
  );
}
