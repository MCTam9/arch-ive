// The rendered page a record was extracted from.
//
// One component because two pages were doing this differently and each was
// missing something the other had: the review queue had no `loading="lazy"`
// and eagerly fetched up to 25 authenticated images at once, while item detail
// silently rendered nothing when a citation had no scan. Both built alt text
// that reads "page null" when page_index is absent.
//
// Space is reserved from the page's real dimensions (source_page.width_pt /
// height_pt, populated for all 806 pages in three geometries), so the image
// does not reflow its row when it decodes.
//
// Deliberately no grain over this surface: it is where a reader compares
// extracted text against the original, and texture there is noise in the
// literal sense.

export function PageScan({
  imageKey,
  documentLabel,
  pageIndex,
  widthPt,
  heightPt,
  priority = false,
}: {
  imageKey: string | null;
  documentLabel: string;
  pageIndex: number | null;
  widthPt: number | null;
  heightPt: number | null;
  /** The first scan on a page is above the fold; everything else waits. */
  priority?: boolean;
}) {
  const ratio = widthPt && heightPt ? `${widthPt} / ${heightPt}` : "1191 / 842";

  if (!imageKey) {
    return (
      <div
        className="surface-sunken"
        style={{
          aspectRatio: ratio,
          border: "var(--border-width) solid var(--border)",
          display: "grid",
          placeItems: "center",
          padding: "var(--s-4)",
        }}
      >
        <p className="font-body text-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
          No page render for this citation.
        </p>
      </div>
    );
  }

  const alt =
    pageIndex != null
      ? `Scan of ${documentLabel}, page ${pageIndex}`
      : `Scan from ${documentLabel}`;

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/api/page-image/${imageKey.replace(/^pages\//, "")}`}
      alt={alt}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      style={{
        width: "100%",
        height: "auto",
        aspectRatio: ratio,
        display: "block",
        border: "var(--border-width) solid var(--border)",
        background: "var(--surface-sunken)",
      }}
    />
  );
}
