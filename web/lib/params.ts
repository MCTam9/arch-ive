// The URL contract, in one place.
//
// The browse vocabulary used to be declared six times across three naming
// conventions -- BROWSE_PARAMS in links.ts, a hand-retyped structural copy in
// browse/page.tsx, BrowseFilters in queries.ts, the hand-written translation
// between the last two, and two more local shapes in matrix/ and item/. The
// copies had already drifted: browse clamped its offset twice and review not
// at all, browse read its page size from BROWSE_PAGE_SIZE and review typed 25
// into two links, and the review status allowlist -- the thing that makes
// queries.ts's direct interpolation of the status into SQL safe -- existed
// twice, byte for byte.
//
// This module owns the read side: raw search params in, the shapes queries.ts
// accepts out. links.ts owns the write side. Neither imports the other's job.

import {
  BROWSE_PAGE_SIZE,
  REVIEW_STATUSES,
  type BrowseFilters,
  type ReviewFilter,
} from "@/lib/queries";
import { BROWSE_PARAMS } from "@/lib/links";

export { BROWSE_PAGE_SIZE };

/** The review queue's page size.
 *
 *  It lives here rather than in queries.ts because queries.ts is the SQL, and
 *  a page size is a property of the URL: `offset` steps by exactly this and
 *  the two pagination links have to agree with the LIMIT that produced the
 *  rows. review/page.tsx typed `25` into both links against a separate
 *  `opts.limit ?? 25` default; the page now passes this value in, so there is
 *  one number and the default is never reached.
 */
export const REVIEW_PAGE_SIZE = 25;

/** What Next actually hands a page.
 *
 *  Not `Record<string, string | undefined>`, which is what every call site
 *  used to declare. A repeated key -- `?document=a&document=b`, which any
 *  hand-edited or double-submitted URL can produce -- arrives as an array.
 *  Passed on unexamined it reaches `d.slug = $1` as the Postgres array
 *  literal `'{a,b}'`, matches nothing, and renders an empty page with no
 *  error: indistinguishable from RLS returning zero rows, which is the one
 *  failure this app must never make look like an empty corpus.
 */
export type RawSearchParams = Record<string, string | string[] | undefined>;

/** The first value for a key, whether or not it was repeated.
 *
 *  Every read of a search param goes through this. "First wins" is the same
 *  rule `URLSearchParams.get` uses, so a link built by href() and a URL typed
 *  by hand resolve identically.
 */
export function first(sp: RawSearchParams, key: string): string | undefined {
  const value = sp[key];
  return Array.isArray(value) ? value[0] : value;
}

/** Collapse every key to its first value.
 *
 *  For the places that hand the whole object on -- `browseParams`, a
 *  `defaultValue` on a `<select>` -- rather than reading one key at a time.
 */
export function singleValued(sp: RawSearchParams): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const key of Object.keys(sp)) out[key] = first(sp, key);
  return out;
}

/** The largest offset worth sending to Postgres.
 *
 *  OFFSET takes a bigint, whose range is far wider than this, so the bound is
 *  not the database's -- it is JavaScript's. Above MAX_SAFE_INTEGER a Number
 *  no longer denotes one integer, so `offset + PAGE_SIZE` in a "next" link
 *  stops moving and the reader is stuck on a page that cannot advance. Clamp
 *  where the arithmetic stops being arithmetic.
 */
export const MAX_OFFSET = Number.MAX_SAFE_INTEGER;

/** A pagination offset: a non-negative integer, always.
 *
 *  Three separate hazards, none of which the four copies of this line all
 *  handled:
 *
 *  - Negative. `listKnowledgeItems` clamps and browse clamped again;
 *    `listReviewQueue` clamps nowhere and neither did review/page.tsx, so
 *    `/review?offset=-5` reached Postgres as a parameterised `OFFSET -5` and
 *    came back as 22023 "OFFSET must not be negative" -- a 500 into the error
 *    boundary from a URL anyone can type.
 *  - Infinite. `Number("1e999")` is `Infinity`, and node-postgres stringifies
 *    it into the wire protocol as the literal `"Infinity"`, which bigint
 *    input rejects.
 *  - Fractional. `Number("1.9")` is 1.9 and OFFSET takes a bigint, so it is
 *    truncated here rather than being refused there.
 *
 *  `Number("")` is 0 and `Number("abc")` is NaN; both mean "not given", which
 *  is page one.
 */
export function parseOffset(raw: string | string[] | undefined): number {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined) return 0;
  const n = Number(value);
  // NaN fails both comparisons and lands on 0; Infinity clamps to the top.
  if (!Number.isFinite(n)) return n > 0 ? MAX_OFFSET : 0;
  return Math.min(Math.max(Math.trunc(n), 0), MAX_OFFSET);
}

/** URL name -> BrowseFilters field. The one translation between the two
 *  naming conventions, and the reason there is no second list of URL names.
 *
 *  `satisfies` rather than a type annotation: the keys stay literal for
 *  Object.entries, while both halves are still checked -- a URL name that
 *  links.ts does not publish, or a filter field queries.ts does not have, is
 *  a compile error here rather than a filter that silently never applies.
 */
const BROWSE_FILTER_KEYS = {
  document: "documentSlug",
  item_type: "itemType",
  topic: "topicId",
  scale: "scaleId",
  level: "levelId",
  q: "q",
} satisfies Partial<Record<(typeof BROWSE_PARAMS)[number], keyof BrowseFilters>>;

/** The browse filters, as `listKnowledgeItems` wants them.
 *
 *  Blank and whitespace-only values read as absent. That matters most for
 *  `q`: `listKnowledgeItems` branches on `if (filters.q)`, so `?q=%20` used
 *  to switch the whole page onto the ranked path and run three ranking legs
 *  plus a headline over the corpus to find nothing -- and then report
 *  "by relevance" over an empty list. The other five would have become
 *  `slug = ' '`, which is merely wrong rather than expensive.
 *
 *  Keys that are not filters -- `offset`, and the `from`/`ret` pair that
 *  describes the journey rather than the query -- are ignored by
 *  construction, because only the table above is consulted.
 */
export function parseBrowseFilters(sp: RawSearchParams): BrowseFilters {
  const out: BrowseFilters = {};
  const entries = Object.entries(BROWSE_FILTER_KEYS) as [
    keyof typeof BROWSE_FILTER_KEYS,
    keyof BrowseFilters,
  ][];
  for (const [param, field] of entries) {
    const value = first(sp, param)?.trim();
    if (value) out[field] = value;
  }
  return out;
}

/** The review queue's status filter.
 *
 *  This allowlist IS the escaping. `listReviewQueue` builds its predicate as
 *  `k.review_status = '${status}'` -- a string interpolated straight into
 *  SQL, with no placeholder -- and that is safe only because the value can
 *  only ever be one of the four literals in REVIEW_STATUSES. The check
 *  existed twice, byte-identical, in queries.ts and in review/page.tsx; an
 *  invariant that load-bearing is not something to keep two copies of. The
 *  copy inside queries.ts stays as the last line of defence for any other
 *  caller. This is the one the URL goes through.
 *
 *  Anything else -- absent, empty, the wrong case, an unknown value, a quote
 *  and a semicolon -- is "pending", the queue's default view.
 */
export function parseReviewStatus(raw: string | string[] | undefined): ReviewFilter {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return REVIEW_STATUSES.includes(value as ReviewFilter) ? (value as ReviewFilter) : "pending";
}
