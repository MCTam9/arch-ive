// URL building, in one place.
//
// Every link in this app used to be a bare path, so navigating anywhere threw
// away the filters, the sheet, the review status and the page you were on. The
// review queue had grown its own local fix for this; that idea is here now, so
// every page uses the same one.

export type QueryValue = string | number | undefined | null;

/** Build a path with a query string, dropping empty values.
 *
 *  Empty values are dropped rather than serialised, so a URL never accumulates
 *  `?document=&item_type=&topic=` from an unset `<select>` — those read as
 *  broken when shared, and every consumer has to `|| undefined` them anyway.
 */
export function href(path: string, params: Record<string, QueryValue> = {}): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    q.set(key, String(value));
  }
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Where the results list lives.
 *
 *  It used to be `/`, which meant the wordmark and the Browse nav link pointed
 *  at the same place and neither could honestly claim to be current. `/` is
 *  the home page now. Every browse link goes through `browseHref` so the next
 *  move costs one line rather than fourteen.
 */
export const BROWSE_PATH = "/browse";

/** The filters the browse page understands, in the order they appear in the UI. */
export const BROWSE_PARAMS = [
  "document",
  "item_type",
  "topic",
  "scale",
  "level",
  "q",
  "offset",
] as const;

export type BrowseParams = Partial<Record<(typeof BROWSE_PARAMS)[number], string>>;

/** Keep only the browse filters out of an arbitrary search-params object. */
export function browseParams(sp: Record<string, string | undefined>): BrowseParams {
  const out: BrowseParams = {};
  for (const key of BROWSE_PARAMS) {
    if (sp[key]) out[key] = sp[key];
  }
  return out;
}

/** A browse URL with these filters. */
export function browseHref(params: Record<string, QueryValue> = {}): string {
  return href(BROWSE_PATH, params);
}

/** A path we are willing to redirect to, or null.
 *
 *  Same rule the `ret` parameter has always used: it must be a path on this
 *  site. `//evil.example` is a protocol-relative URL that browsers treat as
 *  absolute, so "starts with /" alone is not enough.
 */
export function safePath(candidate: string | undefined | null): string | null {
  if (!candidate) return null;
  return candidate.startsWith("/") && !candidate.startsWith("//") ? candidate : null;
}

/** Where "back" goes from an item, and what to call it.
 *
 *  An item is reachable from browse, the matrix and the review queue, and the
 *  back link used to say "browse" and go to unfiltered browse from all three —
 *  wrong for two of them, and it discarded whatever filter got you there. The
 *  inbound link carries `from` (and its own state) so the return trip lands
 *  where it started.
 */
export function backLink(from: string | undefined, ret: string | undefined) {
  const target = safePath(ret);
  switch (from) {
    case "matrix":
      return { label: "matrix", href: target ?? "/matrix" };
    case "review":
      return { label: "review queue", href: target ?? "/review" };
    default:
      return { label: "browse", href: target ?? BROWSE_PATH };
  }
}
