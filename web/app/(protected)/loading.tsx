// Every route here is force-dynamic and queries Postgres, so navigation used
// to sit on the previous page with no feedback at all until the new one was
// ready. This is what the reader sees in the meantime.
//
// Not a skeleton of fake rows: a shimmering outline of content that does not
// exist yet reads as data, and on a page whose whole purpose is to be trusted
// about what the corpus says, that is the wrong instinct.

export default function Loading() {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{ padding: "var(--s-16) var(--s-6)", display: "grid", placeItems: "center" }}
    >
      <p className="font-display text-muted" style={{ fontSize: "var(--fs-label)", margin: 0 }}>
        Loading…
      </p>
    </div>
  );
}
