// Film grain texture, ~3% opacity by default (var(--grain-opacity) in
// tokens.css). Rule: never render this over a page-scan image. That rule used
// to be theoretical -- this build had no page-image view -- and is now live:
// item/[id], page/[id] and review all render <PageScan>, where grain would sit
// on top of a photograph of a real document and read as damage to the source.
// Chrome surfaces only (header bars, draft stamps, the matrix legend); never a
// page-wide overlay, which is how it would reach a scan by accident.
export function Grain({ on = "light" }: { on?: "light" | "dark" }) {
  return (
    <div
      aria-hidden
      className={`grain-layer ${on === "dark" ? "on-dark" : "on-light"}`}
    />
  );
}
