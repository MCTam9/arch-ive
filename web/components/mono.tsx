// Rule 1: every number, unit, code and cell value renders in the mono face —
// never the bitmap display face, and never plain body prose either, so a
// misread ('<95 l/p/day' vs '<95' with the unit lost in body text) can't
// happen. Route every data value through this.
export function Mono({
  children,
  className = "",
  style,
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span className={`font-mono ${className}`} style={style}>
      {children}
    </span>
  );
}

/** A citation reference: document slug + PDF page + printed page, all mono. */
export function CiteRef({
  documentSlug,
  pageIndex,
  printedPageLabel,
}: {
  documentSlug: string;
  pageIndex?: number | null;
  printedPageLabel?: string | null;
}) {
  return (
    <Mono className="text-muted">
      {documentSlug}
      {pageIndex != null ? ` · pdf p${pageIndex}` : ""}
      {printedPageLabel ? ` · p${printedPageLabel}` : ""}
    </Mono>
  );
}
