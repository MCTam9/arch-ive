// Level bands echo the source sheet's own colour coding (see tokens.css
// --level-1..4) so a Level 3 cell reads the same on screen as on paper.
export function LevelBadge({
  ordinal,
  code,
}: {
  ordinal: number | null | undefined;
  code?: string | null;
}) {
  if (!ordinal) return null;
  const cls = ordinal >= 1 && ordinal <= 4 ? `level-${ordinal}` : "";
  return (
    <span className={`level-chip font-mono ${cls}`} title={`Level ${ordinal}`}>
      {code ?? `L${ordinal}`}
    </span>
  );
}
