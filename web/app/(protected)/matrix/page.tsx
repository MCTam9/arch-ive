import { requireSession } from "@/lib/session";
import { listFrameworks, getMatrixDocuments, getMatrix } from "@/lib/queries";
import { href, BROWSE_PATH } from "@/lib/links";
import Link from "next/link";
import { Mono } from "@/components/mono";
import { Button, PageHeader, EmptyState } from "@/components/ui";
import { StatusFlag } from "@/components/draft-wrapper";

export const dynamic = "force-dynamic";

export default async function MatrixPage({
  searchParams,
}: {
  searchParams: Promise<{ framework?: string; document?: string }>;
}) {
  const session = await requireSession();
  const sp = await searchParams;

  const frameworks = await listFrameworks(session.accountId);
  const frameworkSlug =
    sp.framework || frameworks.find((f) => f.slug === "practice-crib-sheets")?.slug || frameworks[0]?.slug;

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
  const documentSlug = documents.some((d) => d.slug === sp.document)
    ? sp.document
    : documents[0]?.slug;

  const { levels, criteria, cells } = await getMatrix(session.accountId, frameworkSlug, documentSlug);
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

      {criteria.length === 0 || levels.length === 0 ? (
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
              </tr>
            </thead>
            <tbody>
              {criteria.map((c) => (
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
                  {levels.map((lvl) => {
                    const cellItems = cells.get(`${c.id}::${lvl.id}`) ?? [];
                    return (
                      <td
                        key={lvl.id}
                        style={{
                          padding: "var(--s-2)",
                          border: "var(--border-width) solid var(--border)",
                          verticalAlign: "top",
                        }}
                      >
                        {cellItems.length === 0 ? (
                          <span className="text-muted font-mono" style={{ fontSize: "var(--fs-micro)" }}>
                            —
                          </span>
                        ) : (
                          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                            {cellItems.map((cell) => (
                              <li key={cell.knowledge_item_id}>
                                {/* `link`, not colour: "inherit" — a cell sits
                                    on a level band, and an inherited colour
                                    made the one interactive thing in the table
                                    indistinguishable from the label beside it
                                    apart from an underline. */}
                                <Link
                                  href={`/item/${cell.knowledge_item_id}?from=matrix&ret=${encodeURIComponent(matrixHref)}`}
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
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
