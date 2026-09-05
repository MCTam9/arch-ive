"use client";

// The only client component in the app, and it earns that for one reason:
// marking the current page needs the pathname, and a server component cannot
// read it. Everything else in the nav -- the wordmark, the email, the sign-out
// server action -- stays on the server.

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Browse" },
  { href: "/matrix", label: "Matrix" },
  { href: "/review", label: "Review" },
  { href: "/ingest", label: "Ingest" },
  { href: "/styleguide", label: "Styleguide" },
] as const;

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav aria-label="Sections" style={{ display: "flex", gap: "var(--s-1)" }}>
      {LINKS.map((link) => {
        // "/" would otherwise match every route. An item page counts as
        // Browse, because that is the section it belongs to.
        const active =
          link.href === "/"
            ? pathname === "/" || pathname.startsWith("/item/")
            : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className="font-display transition-fast"
            style={{
              fontSize: "var(--fs-label)",
              color: active ? "var(--accent-text)" : "var(--chrome-text)",
              background: active ? "var(--accent)" : "transparent",
              // The border is always there, transparent when inactive, so the
              // label does not shift by a pixel as you move between sections.
              border: `var(--border-width) solid ${active ? "var(--border-strong)" : "transparent"}`,
              padding: "var(--s-1) var(--s-2)",
              textDecoration: "none",
            }}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
