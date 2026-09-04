import { requireSession } from "@/lib/session";
import { listFrameworks, getMatrixDocuments, getMatrix } from "@/lib/queries";
import Link from "next/link";
import { Mono } from "@/components/mono";
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
    return <p className="font-body" style={{ padding: "var(--s-6)" }}>No frameworks found.</p>;
  }

  const documents = await getMatrixDocuments(session.accountId, frameworkSlug);
  const documentSlug = sp.document || documents[0]?.slug;

  const { levels, criteria, cells } = await getMatrix(session.accountId, frameworkSlug, documentSlug);

  return (
    <div style={{ padding: "var(--s-6)" }}>
      <h1 className="font-display" style={{ fontSize: "var(--fs-h1)", margin: "0 0 var(--s-2) 0" }}>
        Matrix
      </h1>
      <p className="text-muted font-body" style={{ marginBottom: "var(--s-4)" }}>
        The screen version of the source crib sheet: level bands as columns, criteria as rows —
        laid out to make verification against the PDF trivial.
      </p>

      <form method="GET" style={{ display: "flex", gap: "var(--s-4)", marginBottom: "var(--s-4)", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
          <span className="font-display" style={{ fontSize: "var(--fs-micro)" }}>
            Framework
          </span>
          <select
            name="framework"
            defaultValue={frameworkSlug}
            className="font-mono"
            style={{ padding: "var(--s-2)", border: "var(--border-width) solid var(--border-strong)", background: "var(--surface)" }}
          >
            {frameworks.map((f) => (
              <option key={f.slug} value={f.slug}>
                {f.slug}
              </option>
            ))}
          </select>
        </label>
        {documents.length > 0 && (
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
            <span className="font-display" style={{ fontSize: "var(--fs-micro)" }}>
              Sheet
            </span>
            <select
              name="document"
              defaultValue={documentSlug}
              className="font-mono"
              style={{ padding: "var(--s-2)", border: "var(--border-width) solid var(--border-strong)", background: "var(--surface)" }}
            >
              {documents.map((d) => (
                <option key={d.slug} value={d.slug}>
                  {d.slug}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="submit"
          className="font-display transition-fast"
          style={{
            alignSelf: "flex-end",
            padding: "var(--s-2) var(--s-3)",
            background: "var(--accent)",
            color: "var(--accent-text)",
            border: "var(--border-width-strong) solid var(--border-strong)",
            fontSize: "var(--fs-label)",
            cursor: "pointer",
          }}
        >
          Go
        </button>
      </form>

      {criteria.length === 0 || levels.length === 0 ? (
        <p className="font-body text-muted">No criteria found for this framework/sheet combination.</p>
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
                    background: "var(--n900)",
                    color: "var(--n100)",
                  }}
                >
                  Criterion
                </th>
                {levels.map((lvl) => (
                  <th
                    key={lvl.id}
                    className={`font-display level-${lvl.ordinal}`}
                    style={{
                      textAlign: "left",
                      fontSize: "var(--fs-label)",
                      padding: "var(--s-2)",
                      border: "var(--border-width) solid var(--border-strong)",
                    }}
                  >
                    {lvl.code} {lvl.name}
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
                                <Link
                                  href={`/item/${cell.knowledge_item_id}`}
                                  className="font-body transition-fast"
                                  style={{ fontSize: "var(--fs-sm)", color: "inherit", textDecoration: "none" }}
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
