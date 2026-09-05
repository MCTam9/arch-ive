// Rule 4 (provenance is visible), for the axis DraftWrapper does not cover.
//
// DraftWrapper marks content whose *source* is unfinished -- wip, lorem,
// template. A figure description is a different claim and a more dangerous
// one: the source is finished and genuine, but the sentence was written by a
// model, not by the document. Searching now returns those descriptions in the
// same list as quoted requirements, and nothing else on the card would tell
// them apart.
//
// Deliberately NOT a new key in DraftWrapper's STAMP_TEXT. Two reasons, and
// the first is a trap: needsStamp() returns false for any status outside its
// four-key map, so a synthetic content_status would render *nothing at all*
// and fail silently -- the worst possible outcome for a provenance marker.
// The second is that content_status describes source fidelity, and the schema
// separates the two axes on purpose (db/migrate/2026-09-05_figure_assets.sql).
//
// So this keys on the one fact that is true iff a model wrote the text:
// source_asset.vlm_model is present.

/** Inline, for a result card. Renders nothing when the text is not generated. */
export function GeneratedFlag({ model }: { model?: string | null }) {
  if (!model) return null;
  return (
    <span className="generated-flag font-mono" title={`Generated description — ${model}`}>
      described by {shortModel(model)}
    </span>
  );
}

/** Block, for the page where the description is read rather than skimmed. */
export function GeneratedBlock({
  model,
  children,
}: {
  model?: string | null;
  children: React.ReactNode;
}) {
  if (!model) return <>{children}</>;
  return (
    <div className="generated-block">
      <p className="generated-flag font-mono" style={{ margin: "0 0 var(--s-2)" }}>
        described by {shortModel(model)}
      </p>
      {children}
      <p className="font-body text-muted" style={{ margin: "var(--s-2) 0 0", fontSize: "var(--fs-micro)" }}>
        Written by a model from the image above. Not text from the document.
      </p>
    </div>
  );
}

// Stored as e.g. "claude-sonnet-5 (Claude Code session)". The parenthetical is
// how it was produced, which belongs in the audit log and not on a card.
function shortModel(model: string): string {
  return model.split(" (")[0];
}
