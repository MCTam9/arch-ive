import { Grain } from "@/components/grain";
import { DraftWrapper } from "@/components/draft-wrapper";
import { LevelBadge } from "@/components/level-badge";
import { Mono } from "@/components/mono";

const RAMP = [
  ["--n900", "var(--n900)"],
  ["--n800", "var(--n800)"],
  ["--n700", "var(--n700)"],
  ["--n600", "var(--n600)"],
  ["--n500", "var(--n500)"],
  ["--n400", "var(--n400)"],
  ["--n300", "var(--n300)"],
  ["--n200", "var(--n200)"],
  ["--n100", "var(--n100)"],
];

const SPACING = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20];

function Swatch({ name, value }: { name: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1)" }}>
      <div
        style={{
          height: 48,
          background: value,
          border: "var(--border-width) solid var(--border)",
        }}
      />
      <Mono className="text-muted" style={{ fontSize: "var(--fs-micro)" }}>
        {name}
      </Mono>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: "var(--s-10)" }}>
      <h2
        className="font-display"
        style={{
          fontSize: "var(--fs-h3)",
          borderBottom: "var(--border-width-strong) solid var(--border-strong)",
          paddingBottom: "var(--s-2)",
          marginBottom: "var(--s-4)",
        }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function ThemePanel({ theme }: { theme: "light" | "dark" }) {
  return (
    <div
      data-theme={theme}
      style={{
        background: "var(--bg)",
        color: "var(--text)",
        padding: "var(--s-6)",
        border: "var(--border-width-strong) solid var(--border-strong)",
      }}
    >
      <p className="font-display text-muted" style={{ fontSize: "var(--fs-label)", marginBottom: "var(--s-4)" }}>
        theme: {theme}
      </p>

      <Section title="Neutral ramp + accent">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "var(--s-3)" }}>
          {RAMP.map(([name, value]) => (
            <Swatch key={name} name={name} value={value} />
          ))}
          <Swatch name="--mint" value="var(--mint)" />
          <Swatch name="--mint-deep" value="var(--mint-deep)" />
        </div>
      </Section>

      <Section title="Level bands (source colour-coding)">
        <div style={{ display: "flex", gap: "var(--s-3)", alignItems: "center", flexWrap: "wrap" }}>
          <LevelBadge ordinal={1} />
          <LevelBadge ordinal={2} />
          <LevelBadge ordinal={3} />
          <LevelBadge ordinal={4} />
          <Mono className="text-muted" style={{ fontSize: "var(--fs-sm)" }}>
            level-1 · level-2 · level-3 · level-4
          </Mono>
        </div>
      </Section>

      <Section title="Semantic surfaces">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--s-3)" }}>
          {["bg", "surface", "surface-sunken", "border"].map((tok) => (
            <Swatch key={tok} name={`--${tok}`} value={`var(--${tok})`} />
          ))}
        </div>
      </Section>

      <Section title="Type — bitmap chrome vs mono data vs body prose">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
          <div>
            <span className="font-display" style={{ fontSize: "var(--fs-h1)" }}>
              Facet Label H1
            </span>
            <p className="text-muted font-mono" style={{ fontSize: "var(--fs-micro)" }}>
              .font-display — --fs-h1, chrome only, never data
            </p>
          </div>
          <div>
            <span className="font-display" style={{ fontSize: "var(--fs-h2)" }}>
              Section heading H2
            </span>
          </div>
          <div>
            <span className="font-display" style={{ fontSize: "var(--fs-label)" }}>
              FACET LABEL — 12px floor
            </span>
          </div>
          <div>
            <Mono style={{ fontSize: "var(--fs-mono-lg)" }}>
              {"<95 l/p/day"} · kgCO2e/m²GIA · 0.4* · 380 vs 330
            </Mono>
            <p className="text-muted font-mono" style={{ fontSize: "var(--fs-micro)" }}>
              .font-mono — every number, unit, code, cell value
            </p>
          </div>
          <div>
            <p className="font-body" style={{ fontSize: "var(--fs-body)" }}>
              Body prose renders in JetBrains Mono at 400: paragraph text for
              statements, guidance and definitions extracted from the corpus.
            </p>
          </div>
        </div>
      </Section>

      <Section title="Spacing — 4px grid">
        <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--s-2)" }}>
          {SPACING.map((n) => (
            <div key={n} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--s-1)" }}>
              <div style={{ width: 16, height: `var(--s-${n})`, background: "var(--accent)" }} />
              <Mono className="text-muted" style={{ fontSize: "var(--fs-micro)" }}>
                s-{n}
              </Mono>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Hard shadow + motion">
        <div style={{ display: "flex", gap: "var(--s-6)", alignItems: "center" }}>
          <div
            className="shadow-hard"
            style={{
              background: "var(--surface)",
              border: "var(--border-width) solid var(--border-strong)",
              padding: "var(--s-4)",
            }}
          >
            <Mono style={{ fontSize: "var(--fs-sm)" }}>shadow-hard</Mono>
          </div>
          <button
            className="transition-fast font-display"
            style={{
              padding: "var(--s-3) var(--s-4)",
              background: "var(--accent)",
              color: "var(--accent-text)",
              border: "var(--border-width-strong) solid var(--border-strong)",
              fontSize: "var(--fs-label)",
              cursor: "pointer",
            }}
          >
            hover me (≤120ms)
          </button>
        </div>
      </Section>

      <Section title="Provenance — draft / placeholder stamp">
        <div style={{ display: "flex", gap: "var(--s-4)", flexWrap: "wrap" }}>
          <DraftWrapper status="wip">
            <p className="font-body">This block came from a WIP source page.</p>
          </DraftWrapper>
          <DraftWrapper status="lorem">
            <p className="font-body">Lorem ipsum dolor sit amet — flagged, never served as fact.</p>
          </DraftWrapper>
        </div>
      </Section>

      <Section title="Grain (chrome surfaces only, never over a page scan)">
        <div style={{ display: "flex", gap: "var(--s-4)" }}>
          <div style={{ position: "relative", width: 160, height: 80, background: "var(--n900)", overflow: "hidden" }}>
            <Grain on="dark" />
          </div>
          <div style={{ position: "relative", width: 160, height: 80, background: "var(--n100)", border: "var(--border-width) solid var(--border)", overflow: "hidden" }}>
            <Grain on="light" />
          </div>
        </div>
      </Section>
    </div>
  );
}

export default function StyleguidePage() {
  return (
    // A <div>, not a <main>: this page now sits inside (protected), whose
    // layout already provides the one <main id="main"> the skip link targets.
    <div style={{ background: "var(--n800)", minHeight: "100dvh", padding: "var(--s-6)" }}>
      <h1
        className="font-display"
        style={{ color: "var(--n100)", fontSize: "var(--fs-h1)", margin: "0 0 var(--s-6) 0" }}
      >
        Styleguide
      </h1>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--s-6)",
        }}
      >
        <ThemePanel theme="light" />
        <ThemePanel theme="dark" />
      </div>
    </div>
  );
}
