"use client";

// tokens.css carries a complete [data-theme] palette for both themes and
// nothing in the app ever set the attribute, so the only way to change theme
// was to change your operating system. This sets it.
//
// Three states, not two: light, dark, and "system" — which stamps nothing and
// lets prefers-color-scheme decide. That third state matters, because it is
// the default and the one most viewers are in.

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("arch-ive-theme", theme);
  } catch {
    // Private windows and blocked site data throw on write. A theme that
    // does not persist is a small loss; a page that crashes is not.
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  // Read on mount rather than during render: localStorage does not exist on
  // the server, and the inline script in app/layout.tsx has already applied
  // the stored value by now, so this only syncs the label.
  useEffect(() => {
    try {
      const stored = localStorage.getItem("arch-ive-theme") as Theme | null;
      if (stored && ORDER.includes(stored)) setTheme(stored);
    } catch {
      /* see above */
    }
  }, []);

  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

  return (
    <button
      type="button"
      className="btn btn-secondary btn-sm font-display"
      onClick={() => {
        setTheme(next);
        apply(next);
      }}
      aria-label={`Theme: ${LABEL[theme]}. Switch to ${LABEL[next]}.`}
      title={`Theme: ${LABEL[theme]}`}
      // Layout only. The label cycles between Auto / Light / Dark, which are
      // different widths, and the rail should not reflow as you click through.
      style={{ minWidth: "5ch" }}
    >
      {LABEL[theme]}
    </button>
  );
}
