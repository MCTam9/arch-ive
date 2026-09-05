import { ButtonLink } from "@/components/ui";

// Lives inside (protected) so a bad id renders with the nav still attached.
// Without this file, notFound() from /item/[id] fell through to Next's stock
// 404, which renders outside this layout: no nav, no wordmark, no link of any
// kind. A mistyped or stale item id was a hard dead end.

export default function NotFound() {
  return (
    <div style={{ maxWidth: "60ch", margin: "0 auto", padding: "var(--s-16) var(--s-6)" }}>
      <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: "0 0 var(--s-3)" }}>
        Not found
      </h1>
      <p className="font-body" style={{ margin: "0 0 var(--s-6)" }}>
        That record does not exist, or it is not visible to your account. Records
        are identified by id, so a link from an old note may point at something
        that has since been re-ingested under a new one.
      </p>
      {/* Was the barest link in the app — no class, no state, the browser's
          default underline in body colour. The one way out of a dead end
          should look like a way out. */}
      <ButtonLink href="/" variant="primary">
        Back to browse
      </ButtonLink>
    </div>
  );
}
