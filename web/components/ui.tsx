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

/** The class, not a style object — and that distinction is the whole reason
 *  hover states work now.
 *
 *  This used to return an inline CSSProperties object. An inline style beats a
 *  class selector, so `.btn:hover` in globals.css would have been valid CSS
 *  that silently lost to `style={{background: …}}` on every button in the app.
 *  Adding the rules without moving the styling into classes produces a diff
 *  that reads as a fix and changes nothing on screen. */
function buttonClass(variant: ButtonVariant): string {
  return `btn btn-${variant} font-display`;
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
  /** Layout only — alignment, width, margin. Anything that paints belongs in
   *  the variant, or the states stop applying for the reason above. */
  style?: CSSProperties;
}) {
  return (
    <button type={type} disabled={disabled} className={buttonClass(variant)} style={style}>
      {children}
    </button>
  );
}

/** A link that looks like a button. Same geometry, so a row of actions does
 *  not step when one of them happens to be navigation.
 *
 *  `disabled` is spelled aria-disabled here: an <a> can never match
 *  `button:disabled`, so a ButtonLink was the one control that could look
 *  disabled, announce itself disabled, and still navigate on click. The CSS
 *  pairs the attribute with `pointer-events: none`. */
export function ButtonLink({
  href,
  children,
  variant = "secondary",
  disabled,
  style,
}: {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
  disabled?: boolean;
  style?: CSSProperties;
}) {
  return (
    <Link
      href={href}
      className={buttonClass(variant)}
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : undefined}
      style={style}
    >
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
