// Film grain texture, ~3% opacity by default (var(--grain-opacity) in
// tokens.css). Rule: never render this over a page-scan image — there is no
// page-image view in this build (see the TODO in app/(protected)/item/[id]/page.tsx),
// so it is safe to use here on chrome surfaces (header bars, draft stamps,
// the matrix legend) but it should not be dropped in as a page-wide overlay
// without checking that rule still holds once page images exist.
export function Grain({ on = "light" }: { on?: "light" | "dark" }) {
  return (
    <div
      aria-hidden
      className={`grain-layer ${on === "dark" ? "on-dark" : "on-light"}`}
    />
  );
}
