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
          // The active treatment used to be this ternary, painted inline. That
          // is why the nav had no hover: an inline background beats any rule a
          // stylesheet can write for it. `aria-current` was already here, so
          // the CSS has an honest hook — `.chrome .tab[aria-current='page']` —
          // and the state is now expressed once, in one place, for both
          // assistive technology and the eye. The border stays transparent
          // rather than absent so nothing shifts a pixel between sections.
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className="tab tab-sm font-display"
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
