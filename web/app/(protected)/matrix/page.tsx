import { requireSession } from "@/lib/session";
import { listFrameworks, getMatrixDocuments, getMatrix, type MatrixCell } from "@/lib/queries";
import { href, backParams, BROWSE_PATH } from "@/lib/links";
import { first, type RawSearchParams } from "@/lib/params";
import Link from "next/link";
import { Mono } from "@/components/mono";
import { Button, PageHeader, EmptyState } from "@/components/ui";
import { StatusFlag } from "@/components/draft-wrapper";

export const dynamic = "force-dynamic";

export default async function MatrixPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const sp = await searchParams;

  // Repeated keys collapse to the first, as everywhere else: ?document=a&document=b
  // used to arrive as an array, never match a sheet, and fall back silently.
  const requestedFramework = first(sp, "framework");
  const requestedDocument = first(sp, "document");

  const frameworks = await listFrameworks(session.accountId);
  const frameworkSlug =
    requestedFramework ||
    frameworks.find((f) => f.slug === "practice-crib-sheets")?.slug ||
    frameworks[0]?.slug;

  if (!frameworkSlug) {
    return (
      <div style={{ padding: "var(--s-6)" }}>
        <EmptyState title="No frameworks" action={{ href: BROWSE_PATH, label: "Go to browse" }}>
          The matrix reads from the framework tables, which the compliance and crib-sheet
          extractors populate. Nothing has been loaded into them yet.
        </EmptyState>
      </div>
    );
  }

  const documents = await getMatrixDocuments(session.accountId, frameworkSlug);
  // Only honour the requested sheet if it belongs to THIS framework. Switching
  // framework used to submit the previous framework's sheet, which is truthy,
  // so the fallback was skipped and the page reported "No criteria found" for
  // a combination that cannot exist.
  const documentSlug = documents.some((d) => d.slug === requestedDocument)
    ? requestedDocument
    : documents[0]?.slug;

  // Assembled rows, not a Map the page has to know the key convention of.
  // `cells[i]` is `levels[i]`, always present, so there is no lookup here to
  // get wrong and no `?? []` turning a mismatch into a table of em-dashes.
  const { levels, rows, hasUnassigned } = await getMatrix(
    session.accountId,
    frameworkSlug,
    documentSlug,
  );
  const matrixHref = href("/matrix", { framework: frameworkSlug, document: documentSlug });

  return (
    <div style={{ padding: "var(--s-6)" }}>
      <PageHeader title="Matrix">
        The screen version of the source crib sheet: level bands as columns, criteria as rows —
        laid out to make verification against the PDF trivial.
      </PageHeader>

      <form method="GET" style={{ display: "flex", gap: "var(--s-4)", marginBottom: "var(--s-4)", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
          <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
            Framework
          </span>
          <select
            name="framework"
            defaultValue={frameworkSlug}
            className="field font-mono"
          >
            {frameworks.map((f) => (
              <option key={f.slug} value={f.slug}>
                {f.name ?? f.slug}
              </option>
            ))}
          </select>
        </label>
        {documents.length > 0 && (
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
              Sheet
            </span>
            <select
              name="document"
              defaultValue={documentSlug}
              className="field font-mono"
            >
              {documents.map((d) => (
                <option key={d.slug} value={d.slug}>
                  {d.title ?? d.slug}
                </option>
              ))}
            </select>
          </label>
        )}
        <Button variant="primary" style={{ alignSelf: "flex-end" }}>
          Go
        </Button>
      </form>

      {/* A framework with no rating scale still has requirements, and they
          now have a column to sit in — so "no levels" is only empty when
          there is nothing unassigned either. Before, every such framework
          reported itself empty while holding all of its requirements. */}
      {rows.length === 0 || (levels.length === 0 && !hasUnassigned) ? (
        <EmptyState title="Nothing in this sheet">
          This framework has no criteria for the selected sheet. Pick another sheet above, or
          switch framework.
        </EmptyState>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              borderCollapse: "collapse",
              width: "100%",
              tableLayout: "fixed",
              minWidth: 900,
            }}
          >
            <thead>
              <tr>
                <th
                  className="font-display"
                  style={{
                    textAlign: "left",
                    fontSize: "var(--fs-label)",
                    padding: "var(--s-2)",
                    border: "var(--border-width) solid var(--border-strong)",
                    width: 220,
                    // Semantic tokens: this header was --n900/--n100 outright,
                    // so it was the one cell in the table that ignored the theme.
                    background: "var(--surface-sunken)",
                    color: "var(--text)",
                  }}
                >
                  Criterion
                </th>
                {levels.map((lvl) => (
                  <th
                    key={lvl.id}
                    className={`level-${lvl.ordinal}`}
                    style={{
                      textAlign: "left",
                      padding: "var(--s-2)",
                      border: "var(--border-width) solid var(--border-strong)",
                    }}
                  >
                    {/* The level CODE is data — it has to read the same here as
                        on the source sheet, and the bitmap face uppercased it.
                        The NAME is a label, so it keeps the display face. */}
                    <span className="font-mono" style={{ fontSize: "var(--fs-sm)", fontWeight: 700 }}>
                      {lvl.code}
                    </span>{" "}
                    <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
                      {lvl.name}
                    </span>
                  </th>
                ))}
                {/* Requirements the sheet states without a level band. They
                    are real requirements — the old lookup built the key
                    `${criterion}::null`, which nothing ever asked for, so
                    they were dropped in silence rather than shown. */}
                {hasUnassigned && (
                  <th
                    title="Stated without a rating level"
                    style={{
                      textAlign: "left",
                      padding: "var(--s-2)",
                      border: "var(--border-width) solid var(--border-strong)",
                      background: "var(--surface-sunken)",
                      color: "var(--text)",
                    }}
                  >
                    <span className="font-mono" style={{ fontSize: "var(--fs-sm)", fontWeight: 700 }}>
                      —
                    </span>{" "}
                    <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
                      No level
                    </span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ criterion: c, cells, unassigned }) => (
                <tr key={c.id}>
                  <td
                    className="font-mono"
                    style={{
                      fontSize: "var(--fs-sm)",
                      padding: "var(--s-2)",
                      border: "var(--border-width) solid var(--border)",
                      verticalAlign: "top",
                      background: "var(--surface-sunken)",
                    }}
                  >
                    <div style={{ fontWeight: 700 }}>{c.code}</div>
                    <div className="font-body text-muted" style={{ fontSize: "var(--fs-sm)" }}>
                      {c.title}
                    </div>
                  </td>
                  {/* Iterating the row's own cells, not the levels: the two
                      arrays are the same length by construction, so there is
                      nothing here to keep in step by hand. */}
                  {cells.map((cellItems, i) => (
                    <MatrixCellList key={levels[i].id} items={cellItems} matrixHref={matrixHref} />
                  ))}
                  {hasUnassigned && (
                    <MatrixCellList items={unassigned} matrixHref={matrixHref} />
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// One cell of the table, level column or "no level" column alike. Extracted
// because the unassigned column renders exactly the same thing, and a second
// copy of it is how the two would drift.
function MatrixCellList({ items, matrixHref }: { items: MatrixCell[]; matrixHref: string }) {
  return (
    <td
      style={{
        padding: "var(--s-2)",
        border: "var(--border-width) solid var(--border)",
        verticalAlign: "top",
      }}
    >
      {items.length === 0 ? (
        <span className="text-muted font-mono" style={{ fontSize: "var(--fs-micro)" }}>
          —
        </span>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
          {items.map((cell) => (
            <li key={cell.knowledge_item_id}>
              {/* `link`, not colour: "inherit" — a cell sits on a level band,
                  and an inherited colour made the one interactive thing in
                  the table indistinguishable from the label beside it apart
                  from an underline. */}
              <Link
                href={href(`/item/${cell.knowledge_item_id}`, backParams("matrix", matrixHref))}
                className="link font-body"
                style={{ fontSize: "var(--fs-sm)" }}
              >
                {cell.statement || cell.target_text}
              </Link>
              <div style={{ display: "flex", gap: "var(--s-1)", alignItems: "center", marginTop: 2 }}>
                {cell.target_text && cell.target_text !== cell.statement && (
                  <Mono style={{ fontSize: "var(--fs-micro)" }} className="text-muted">
                    {cell.target_text}
                    {cell.unit ? ` ${cell.unit}` : ""}
                  </Mono>
                )}
                <StatusFlag status={cell.content_status} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </td>
  );
}
