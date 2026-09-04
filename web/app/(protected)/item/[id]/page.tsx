import { Fragment } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { requireSession } from "@/lib/session";
import { getKnowledgeItem } from "@/lib/queries";
import { Mono } from "@/components/mono";
import { DraftWrapper } from "@/components/draft-wrapper";
import { LevelBadge } from "@/components/level-badge";

export const dynamic = "force-dynamic";


export default async function ItemPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const item = await getKnowledgeItem(session.accountId, id);
  if (!item) notFound();

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "var(--s-6)" }}>
      <Link href="/" className="font-mono text-muted" style={{ fontSize: "var(--fs-sm)" }}>
        &larr; browse
      </Link>

      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)", margin: "var(--s-3) 0 var(--s-1) 0" }}>
        <span className="font-display" style={{ fontSize: "var(--fs-label)", color: "var(--text-muted)" }}>
          {item.item_type}
        </span>
        <Mono className="text-muted" style={{ fontSize: "var(--fs-sm)" }}>
          {item.document_slug}
        </Mono>
      </div>

      <h1 className="font-display" style={{ fontSize: "var(--fs-h1)", margin: "0 0 var(--s-4) 0" }}>
        {item.title || "(untitled)"}
      </h1>

      <DraftWrapper status={item.content_status}>
        {item.statement && (
          <p className="font-body" style={{ fontSize: "var(--fs-body)", marginBottom: "var(--s-3)" }}>
            {item.statement}
          </p>
        )}
        {item.summary && (
          <p className="font-body text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            {item.summary}
          </p>
        )}
      </DraftWrapper>

      <Section title="Payload">
        <PayloadTable itemType={item.item_type} payload={item.payload} />
      </Section>

      {item.scopes.length > 0 && (
        <Section title="Scope applicability">
          <div style={{ display: "grid", gridTemplateColumns: "auto auto 1fr", gap: "var(--s-1) var(--s-4)" }}>
            {item.scopes.map((s, i) => (
              <Fragment key={i}>
                <span className="font-body" style={{ fontSize: "var(--fs-sm)" }}>
                  {s.title}
                </span>
                <Mono style={{ fontSize: "var(--fs-sm)" }} className={s.applies ? "" : "text-muted"}>
                  {s.target_text ?? (s.applies ? "applies" : "n/a")}
                </Mono>
                <span className="text-muted font-body" style={{ fontSize: "var(--fs-sm)" }}>
                  {s.note ?? ""}
                </span>
              </Fragment>
            ))}
          </div>
        </Section>
      )}

      {item.terms.length > 0 && (
        <Section title="Facets">
          <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
            {item.terms.map((t, i) => (
              <span
                key={i}
                className="font-mono"
                style={{
                  padding: "var(--s-1) var(--s-2)",
                  border: "var(--border-width) solid var(--border-strong)",
                  fontSize: "var(--fs-sm)",
                }}
              >
                {t.taxonomy_id}: {t.label}
              </span>
            ))}
          </div>
        </Section>
      )}

      <Section title="Citation">
        {item.citations.length === 0 ? (
          <p className="text-muted font-body">No citation recorded for this item.</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
            {item.citations.map((c, i) => (
              <li key={i} className="font-mono" style={{ fontSize: "var(--fs-sm)" }}>
                {c.document_slug}
                {c.page_index != null && ` · pdf p${c.page_index}`}
                {c.printed_page_label && ` · printed p${c.printed_page_label}`}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <p className="text-muted font-body" style={{ fontSize: "var(--fs-micro)", marginTop: "var(--s-8)" }}>
        Node: {item.node_code ?? "—"} {item.node_title ?? ""} · review: {item.review_status}
      </p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
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

const LABELS: Record<string, string> = {
  requirement_kind: "Kind",
  target_text: "Target",
  target_value: "Target (parsed)",
  unit: "Unit",
  comparator: "Comparator",
  is_deliverable: "Deliverable?",
  deliverable_name: "Deliverable",
  parsed_ok: "Parsed OK?",
  level_ordinal: "Level ordinal",
  level_code: "Level",
  level_name: "Level name",
  criterion_code: "Criterion",
  criterion_title: "Criterion title",
  metric_name: "Metric",
  scope: "Scope",
  value_numeric: "Value (parsed)",
  value_min: "Value min",
  value_max: "Value max",
  value_text: "Value",
  is_placeholder: "Placeholder value?",
  caveat_text: "Caveat",
  building_use_id: "Building use",
  target_year: "Target year",
  region_id: "Region",
  standard_id: "Standard",
  baseline_relative_pct: "Baseline relative %",
  body_md: "Body",
  legend_tokens: "Legend tokens",
  disclaimer: "Disclaimer",
  pattern_kind: "Pattern kind",
  code: "Code",
  name: "Name",
  term: "Term",
  definition: "Definition",
  category: "Category",
  gate: "Gate",
  reports_to: "Reports to",
  qualifications: "Qualifications",
  template_kind: "Template kind",
  engine: "Engine",
  slug: "Slug",
};

function PayloadTable({ itemType, payload }: { itemType: string; payload: Record<string, unknown> | null }) {
  if (!payload) return <p className="text-muted font-body">No {itemType} record found.</p>;

  const entries = Object.entries(payload).filter(
    ([k, v]) => v !== null && v !== undefined && k !== "knowledge_item_id" && k !== "level_ordinal",
  );
  if (entries.length === 0) return <p className="text-muted font-body">Empty payload.</p>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--s-2) var(--s-4)" }}>
      {entries.map(([key, value]) => (
        <FieldRow key={key} label={LABELS[key] ?? key}>
          {key === "level_code" ? (
            <LevelBadge ordinal={(payload.level_ordinal as number) ?? null} code={value as string} />
          ) : (
            renderValue(value)
          )}
        </FieldRow>
      ))}
    </div>
  );
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span className="font-display" style={{ fontSize: "var(--fs-micro)", color: "var(--text-muted)", alignSelf: "center" }}>
        {label}
      </span>
      <div>{children}</div>
    </>
  );
}

function renderValue(value: unknown) {
  if (typeof value === "boolean") {
    return <Mono>{value ? "true" : "false"}</Mono>;
  }
  if (Array.isArray(value)) {
    return <Mono>{value.join(", ") || "—"}</Mono>;
  }
  if (typeof value === "object") {
    return <Mono>{JSON.stringify(value)}</Mono>;
  }
  return <Mono>{String(value)}</Mono>;
}
