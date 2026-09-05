// The pieces every page was building for itself, slightly differently.
//
// Before this file there were two `Section` components with the same name and
// different styling, six paddings for the same button, and six empty states in
// four voices — only one of which had been designed. None of that was a
// decision; it was five pages written at five different times.
//
// Two rules from the project's own design notes are enforced here rather than
// remembered:
//
//   * the bitmap display face is for chrome, never for data. `.font-display`
//     uppercases, so it turned `process_step` into PROCESS_STEP and level
//     codes into shouted labels. Data goes through <DataLabel>, which is mono.
//   * bitmap type has a 12px floor. `.font-display` enforces it in CSS with
//     `font-size: max(var(--fs-label), 1em)`, and an inline `fontSize` beats
//     the class — which is how nine sites came to set 10px bitmap text.
//     Nothing here sets a display size below --fs-label.

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

/** The h1 and its right-hand summary. Every page had its own spacing. */
export function PageHeader({
  title,
  meta,
  children,
}: {
  title: string;
  /** Counts, ranges, status — mono, because it is nearly always data. */
  meta?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header style={{ marginBottom: "var(--s-5)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: "var(--s-4)",
          flexWrap: "wrap",
        }}
      >
        <h1 className="font-display" style={{ fontSize: "var(--fs-h2)", margin: 0 }}>
          {title}
        </h1>
        {meta && (
          <span className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            {meta}
          </span>
        )}
      </div>
      {children && (
        <p className="font-body text-muted" style={{ margin: "var(--s-2) 0 0", maxWidth: "68ch" }}>
          {children}
        </p>
      )}
    </header>
  );
}

/** A titled block within a page. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginTop: "var(--s-8)" }}>
      <h2
        className="font-display"
        style={{
          fontSize: "var(--fs-label)",
          borderBottom: "var(--border-width) solid var(--border)",
          paddingBottom: "var(--s-2)",
          marginBottom: "var(--s-3)",
        }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A small uppercase label for a data value — item type, state, kind.
 *
 *  Mono, not the bitmap face: case carries meaning in this corpus and the
 *  display face destroys it. The letter-spacing does the work the bitmap face
 *  was being used for. */
export function DataLabel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <span
      className="font-mono text-muted"
      style={{
        fontSize: "var(--fs-micro)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

type ButtonVariant = "primary" | "secondary" | "quiet";

function buttonStyle(variant: ButtonVariant): CSSProperties {
  const base: CSSProperties = {
    fontSize: "var(--fs-label)",
    padding: "var(--s-2) var(--s-4)",
    border: "var(--border-width) solid var(--border-strong)",
    cursor: "pointer",
    textDecoration: "none",
    display: "inline-block",
  };
  if (variant === "primary") {
    return {
      ...base,
      background: "var(--accent)",
      color: "var(--accent-text)",
      borderWidth: "var(--border-width-strong)",
    };
  }
  if (variant === "secondary") {
    return { ...base, background: "transparent", color: "var(--text)" };
  }
  return {
    ...base,
    background: "transparent",
    color: "var(--text-muted)",
    borderStyle: "dashed",
  };
}

export function Button({
  children,
  variant = "secondary",
  type = "submit",
  disabled,
  style,
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "submit" | "button";
  disabled?: boolean;
  style?: CSSProperties;
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      className="font-display transition-fast"
      style={{ ...buttonStyle(variant), ...style }}
    >
      {children}
    </button>
  );
}

/** A link that looks like a button. Same geometry, so a row of actions does
 *  not step when one of them happens to be navigation. */
export function ButtonLink({
  href,
  children,
  variant = "secondary",
}: {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
}) {
  return (
    <Link href={href} className="font-display transition-fast" style={buttonStyle(variant)}>
      {children}
    </Link>
  );
}

/** One empty state, one voice: say what is not here, then what to do. */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: { href: string; label: string };
}) {
  return (
    <div className="card" style={{ padding: "var(--s-10)", textAlign: "center" }}>
      <h2 className="font-display" style={{ fontSize: "var(--fs-h3)", margin: "0 0 var(--s-3)" }}>
        {title}
      </h2>
      <p className="font-body text-muted" style={{ margin: "0 auto", maxWidth: "54ch" }}>
        {children}
      </p>
      {action && (
        <p style={{ margin: "var(--s-5) 0 0" }}>
          <ButtonLink href={action.href}>{action.label}</ButtonLink>
        </p>
      )}
    </div>
  );
}
