"use client";

// An error boundary has to be a client component — that is React's rule, not
// a choice. Without this file a database error rendered as a bare 500 outside
// the layout, with no nav and no way back.
//
// The message is deliberately not shown. Errors here come from Postgres and
// carry table names, column names and sometimes row content; this corpus is
// third-party client material and the repo goes to some lengths to keep such
// text out of anywhere it might be pasted. The digest is enough to find the
// real error in the runtime logs.

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div style={{ maxWidth: "60ch", margin: "0 auto", padding: "var(--s-16) var(--s-6)" }}>
      <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: "0 0 var(--s-3)" }}>
        Something failed
      </h1>
      <p className="font-body" style={{ margin: "0 0 var(--s-4)" }}>
        This page could not be loaded. The database may be waking from idle — the
        free tier suspends after a period of inactivity — so trying again often
        works.
      </p>
      {error.digest && (
        <p className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)", margin: "0 0 var(--s-6)" }}>
          Reference {error.digest}
        </p>
      )}
      {/* Not <Button> from components/ui: this file is a client component and
          importing a server-side module into it would pull the whole ui module
          across the boundary. The classes are the same ones. */}
      <button type="button" onClick={reset} className="btn btn-primary font-display">
        Try again
      </button>
    </div>
  );
}
