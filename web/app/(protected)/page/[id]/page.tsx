import Link from "next/link";
import { notFound } from "next/navigation";
import { requireSession } from "@/lib/session";
import { getSourcePage, type PageAsset } from "@/lib/queries";
import { backLink } from "@/lib/links";
import { Mono, CiteRef } from "@/components/mono";
import { PageScan } from "@/components/page-scan";
import { DraftWrapper } from "@/components/draft-wrapper";
import { GeneratedBlock } from "@/components/generated-mark";

export const dynamic = "force-dynamic";

// Where a page-text or figure result lands.
//
// Search reaches three kinds of thing now, and only one of them is a
// knowledge_item with a page of its own. A page chunk and a figure description
// both came from a page, so the page is where they go: the scan answers "where
// did this come from", and for a figure it is the only way to check a
// generated sentence against the thing it describes. Nothing else in the app
// could show you a source page on its own terms -- a scan was reachable only
// as an attachment to a citation.

export default async function SourcePageView({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ asset?: string; from?: string; ret?: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const sp = await searchParams;

  const page = await getSourcePage(session.accountId, id);
  if (!page) notFound();

  const back = backLink(sp.from, sp.ret);
  const focused = page.assets.find((a) => a.id === sp.asset) ?? null;
  const described = page.assets.filter((a) => a.vlm_description);

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "var(--s-6)" }}>
      <p style={{ margin: "0 0 var(--s-4)" }}>
        <Link href={back.href} className="link font-mono" style={{ fontSize: "var(--fs-sm)" }}>
          &larr; {back.label}
        </Link>
      </p>

      <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: "0 0 var(--s-2)" }}>
        {focused ? "Figure" : "Page"}
      </h1>
      <div style={{ marginBottom: "var(--s-5)" }}>
        <CiteRef
          documentSlug={page.document_title ?? page.document_slug}
          pageIndex={page.page_index}
          printedPageLabel={page.printed_page_label}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gap: "var(--s-6)", alignItems: "start" }}>
        {/* overflow:hidden clips the dimming ring the highlight casts —
            without it the 100vmax shadow darkens the whole page. */}
        <div style={{ position: "relative", overflow: "hidden" }}>
          <PageScan
            imageKey={page.page_image_key}
            widthPt={page.width_pt}
            heightPt={page.height_pt}
            pageIndex={page.page_index}
            documentLabel={page.document_title ?? page.document_slug}
            priority
          />
          {/* bbox is in PDF points with a top-left origin, the same space as
              width_pt/height_pt, so the box is four percentages and needs no
              conversion — the same fact tools/crop_figures.py relies on to cut
              the crop in the first place. */}
          {focused && <Highlight asset={focused} widthPt={page.width_pt} heightPt={page.height_pt} />}
        </div>

        <div>
          {focused ? (
            <GeneratedBlock model={focused.vlm_model}>
              <p className="font-body" style={{ margin: 0 }}>{focused.vlm_description}</p>
            </GeneratedBlock>
          ) : (
            <>
              <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-3)" }}>
                Page text
              </h2>
              {/* The page's own text is source material, so it takes the
                  source-fidelity treatment: a wip or template page must not
                  read as guidance just because it is quoted verbatim. */}
              <DraftWrapper status={page.content_status}>
                <p className="font-body" style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: "var(--fs-sm)" }}>
                  {page.text?.trim() || "This page carries no extractable text."}
                </p>
              </DraftWrapper>
            </>
          )}

          {described.length > 0 && (
            <div style={{ marginTop: "var(--s-6)" }}>
              <h2 className="font-display" style={{ fontSize: "var(--fs-label)", margin: "0 0 var(--s-3)" }}>
                Figures on this page
              </h2>
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
                {described.map((a) => (
                  <li key={a.id}>
                    <Link
                      href={`/page/${page.id}?asset=${a.id}`}
                      className="card card-link"
                      style={{ padding: "var(--s-2)", display: "block" }}
                      aria-current={a.id === focused?.id ? "true" : undefined}
                    >
                      <Mono className="text-muted" style={{ fontSize: "var(--fs-micro)" }}>
                        {a.vlm_description?.slice(0, 90)}…
                      </Mono>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Highlight({
  asset,
  widthPt,
  heightPt,
}: {
  asset: PageAsset;
  widthPt: number | null;
  heightPt: number | null;
}) {
  const box = asset.bbox?.map(Number);
  if (!box || box.length !== 4 || !widthPt || !heightPt) return null;
  const [x0, y0, x1, y1] = box;
  return (
    <span
      aria-hidden="true"
      style={{
        position: "absolute",
        left: `${(x0 / widthPt) * 100}%`,
        top: `${(y0 / heightPt) * 100}%`,
        width: `${((x1 - x0) / widthPt) * 100}%`,
        height: `${((y1 - y0) / heightPt) * 100}%`,
        border: "var(--border-width-strong) solid var(--accent-strong)",
        boxShadow: "0 0 0 100vmax rgba(0, 0, 0, 0.35)",
        pointerEvents: "none",
      }}
    />
  );
}
