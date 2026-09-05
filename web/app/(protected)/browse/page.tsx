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
import { BROWSE_PATH, browseHref, browseParams } from "@/lib/links";
import { Mono, CiteRef } from "@/components/mono";
import { Button, DataLabel, EmptyState } from "@/components/ui";
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
    browseHref({ ...current, ...over });

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
              className="field font-body"
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

          <Button variant="primary">Apply</Button>
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
                className="chip chip-filled font-mono"
                title={`Remove ${chip.key.replace(/_/g, " ")} filter`}
              >
                {/* .chip-x holds the × back until the chip is hovered, so the
                    remove affordance appears at the moment it becomes true.
                    Before, the × was permanently at full strength on something
                    that gave no other sign of being clickable. */}
                {chip.label} <span className="chip-x" aria-hidden="true">×</span>
              </Link>
            ))}
            <Link href={BROWSE_PATH} className="font-mono link" style={{ fontSize: "var(--fs-sm)" }}>
              Clear all
            </Link>
          </div>
        )}

        {items.length === 0 ? (
          <EmptyState
            title="No matching records"
            action={chips.length > 0 ? { href: BROWSE_PATH, label: "Clear all filters" } : undefined}
          >
            {chips.length > 0
              ? "Filters combine with AND, so a narrow topic and a specific document together can easily match nothing. Remove one and try again."
              : "Nothing in the corpus matches. If this is unexpected, the enrichment stage that tags items with topic, scale and level may not have run for the newest documents."}
          </EmptyState>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            {items.map((item) => (
              <li key={item.id}>
                <Link href={`/item/${item.id}?from=browse&ret=${encodeURIComponent(withFilters({}))}`} className="card card-link" style={{ padding: "var(--s-4)" }}>
                  <div style={{ display: "flex", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-2)" }}>
                    <DataLabel>{item.item_type.replace(/_/g, " ")}</DataLabel>
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
      <select name={name} defaultValue={value ?? ""} className="field font-mono">
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
