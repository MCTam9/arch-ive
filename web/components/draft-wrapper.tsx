// Rule 4 (non-negotiable): provenance is visible. Any content_status of
// wip / lorem / draft / template renders with a diagonal-hatch background
// and a bitmap stamp. The failure mode this exists to prevent — placeholder
// text served as guidance — is the single biggest risk this system carries.
import type { ReactNode } from "react";

const STAMP_TEXT: Record<string, string> = {
  wip: "DRAFT",
  draft: "DRAFT",
  lorem: "PLACEHOLDER",
  template: "TEMPLATE",
};

export function needsStamp(status: string | null | undefined): boolean {
  return !!status && status in STAMP_TEXT;
}

export function DraftWrapper({
  status,
  children,
}: {
  status: string | null | undefined;
  children: ReactNode;
}) {
  if (!needsStamp(status)) return <>{children}</>;
  const label = STAMP_TEXT[status as string];
  return (
    <div className="status-hatch" style={{ padding: "var(--s-3)" }}>
      <span className="status-stamp font-display" style={{ fontSize: "var(--fs-label)" }}>
        {label}
      </span>
      <div style={{ marginTop: "var(--s-2)" }}>{children}</div>
    </div>
  );
}

/** Inline variant for table cells / list rows where the block layout above is too heavy. */
export function StatusFlag({ status }: { status: string | null | undefined }) {
  if (!needsStamp(status)) return null;
  const label = STAMP_TEXT[status as string];
  return (
    <span
      className="status-stamp font-display"
      style={{ fontSize: "var(--fs-micro)", padding: "1px var(--s-1)" }}
    >
      {label}
    </span>
  );
}
