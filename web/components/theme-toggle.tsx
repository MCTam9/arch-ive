"use client";

// tokens.css carries a complete [data-theme] palette for both themes and
// nothing in the app ever set the attribute, so the only way to change theme
// was to change your operating system. This sets it.
//
// Three states, not two: light, dark, and "system" — which stamps nothing and
// lets prefers-color-scheme decide. That third state matters, because it is
// the default and the one most viewers are in.

import { useSyncExternalStore } from "react";

type Theme = "system" | "light" | "dark";
const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const KEY = "arch-ive-theme";

// The stored theme is state that lives outside React, so it is read through
// useSyncExternalStore rather than copied into useState by an effect. The
// effect version called setState in its own body, which is a cascading render
// and which react-hooks/set-state-in-effect rejects — and it also could not
// see a change made anywhere but this component.
const listeners = new Set<() => void>();

function read(): Theme {
  try {
    const stored = localStorage.getItem(KEY) as Theme | null;
    if (stored && ORDER.includes(stored)) return stored;
  } catch {
    // Private windows and blocked site data throw on read as well as write.
  }
  return "system";
}

// There is no localStorage on the server, and the inline script in
// app/layout.tsx has already applied the stored value before React hydrates.
// So the server renders the default label and hydration corrects it, which is
// what the mount effect used to do.
function serverSnapshot(): Theme {
  return "system";
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // `storage` fires in the *other* tabs, never the one that wrote — so both
  // this and the explicit notify in apply() are needed to cover both cases.
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private windows and blocked site data throw on write. A theme that
    // does not persist is a small loss; a page that crashes is not.
  }
  for (const onChange of listeners) onChange();
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, read, serverSnapshot);
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

  return (
    <button
      type="button"
      className="btn btn-secondary btn-sm font-display"
      onClick={() => apply(next)}
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
