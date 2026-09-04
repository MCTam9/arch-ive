import Link from "next/link";
import { signOut } from "@/auth";

export function Nav({ email }: { email?: string | null }) {
  return (
    <header
      style={{
        position: "relative",
        borderBottom: "var(--border-width-strong) solid var(--chrome-border)",
        background: "var(--chrome-bg)",
        color: "var(--chrome-text)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--s-3) var(--s-4)",
          gap: "var(--s-4)",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-6)" }}>
          <Link href="/" className="font-display" style={{ fontSize: "var(--fs-h3)", color: "var(--chrome-text)", textDecoration: "none" }}>
            arch-ive
          </Link>
          <nav style={{ display: "flex", gap: "var(--s-4)" }}>
            <NavLink href="/">Browse</NavLink>
            <NavLink href="/matrix">Matrix</NavLink>
            <NavLink href="/review">Review</NavLink>
            <NavLink href="/ingest">Ingest</NavLink>
            <NavLink href="/styleguide">Styleguide</NavLink>
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
          {email && (
            <span className="font-mono" style={{ fontSize: "var(--fs-sm)", color: "var(--chrome-muted)" }}>
              {email}
            </span>
          )}
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/login" });
            }}
          >
            <button
              type="submit"
              className="font-display transition-fast"
              style={{
                fontSize: "var(--fs-label)",
                background: "transparent",
                border: "var(--border-width) solid var(--chrome-border)",
                color: "var(--chrome-text)",
                padding: "var(--s-1) var(--s-2)",
                cursor: "pointer",
              }}
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </header>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="font-display transition-fast"
      style={{ fontSize: "var(--fs-label)", color: "var(--chrome-text)", textDecoration: "none" }}
    >
      {children}
    </Link>
  );
}
