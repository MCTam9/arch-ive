import Link from "next/link";
import { signOut } from "@/auth";
import { NavLinks } from "./nav-links";
import { ThemeToggle } from "./theme-toggle";

export function Nav({ email }: { email?: string | null }) {
  return (
    // `chrome` as a class rather than the two inline declarations it replaces:
    // the descendant rules (.chrome .tab, .chrome .btn) are how the rail's
    // controls take the chrome palette for their hover and press states, and a
    // descendant selector needs an ancestor to hang off.
    <header
      className="chrome"
      style={{
        position: "relative",
        borderBottom: "var(--border-width-strong) solid var(--chrome-border)",
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
          {/* The wordmark goes home, which is no longer the same place as
              Browse. It still never takes aria-current: home is where the
              wordmark always goes, so marking it adds nothing, and two current
              markers in one rail is worse than none. */}
          <Link href="/" className="tab font-display" style={{ fontSize: "var(--fs-h3)" }}>
            arch-ive
          </Link>
          <NavLinks />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
          {email && (
            <span className="font-mono" style={{ fontSize: "var(--fs-sm)", color: "var(--chrome-muted)" }}>
              {email}
            </span>
          )}
          <ThemeToggle />
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/login" });
            }}
          >
            <button type="submit" className="btn btn-secondary btn-sm font-display">
              Sign out
            </button>
          </form>
        </div>
      </div>
    </header>
  );
}
