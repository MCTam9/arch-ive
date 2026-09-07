// Pure. No database, no session, no Next — every case here is a string the
// URL bar can produce, and each one corresponds to a copy of this logic that
// existed somewhere else and had drifted from its twin.
import { describe, expect, it } from "vitest";
import {
  BROWSE_PAGE_SIZE,
  MAX_OFFSET,
  REVIEW_PAGE_SIZE,
  first,
  parseBrowseFilters,
  parseOffset,
  parseReviewStatus,
  singleValued,
} from "@/lib/params";

describe("first", () => {
  it("returns a single value unchanged", () => {
    expect(first({ document: "crib-water" }, "document")).toBe("crib-water");
  });

  it("collapses a repeated key to the first value", () => {
    // This is the whole reason the raw type is `string | string[]`. Next hands
    // ?document=a&document=b to the page as an array; passed on, it reaches
    // `d.slug = $1` as the Postgres array literal '{a,b}', matches nothing,
    // and renders an empty page with no error.
    expect(first({ document: ["a", "b"] }, "document")).toBe("a");
  });

  it("reads an absent or empty key as undefined", () => {
    expect(first({}, "document")).toBeUndefined();
    expect(first({ document: undefined }, "document")).toBeUndefined();
    expect(first({ document: [] }, "document")).toBeUndefined();
  });
});

describe("singleValued", () => {
  it("collapses every key at once and keeps the rest", () => {
    expect(
      singleValued({ document: ["a", "b"], q: "wind", offset: undefined, from: "review" }),
    ).toEqual({ document: "a", q: "wind", offset: undefined, from: "review" });
  });
});

describe("parseOffset", () => {
  it("reads an absent or empty offset as page one", () => {
    expect(parseOffset(undefined)).toBe(0);
    expect(parseOffset("")).toBe(0);
    expect(parseOffset("0")).toBe(0);
  });

  it("passes a plain offset through", () => {
    expect(parseOffset("25")).toBe(25);
  });

  it("reads a non-number as page one", () => {
    expect(parseOffset("abc")).toBe(0);
    expect(parseOffset("25 items")).toBe(0);
  });

  it("clamps a negative offset to zero", () => {
    // The live bug. listKnowledgeItems clamps and browse/page.tsx clamped
    // again; listReviewQueue clamps nowhere and neither did review/page.tsx,
    // so /review?offset=-5 reached Postgres as a parameterised OFFSET -5 and
    // came back 22023 "OFFSET must not be negative" — a 500 into the error
    // boundary from a URL anyone can type.
    expect(parseOffset("-25")).toBe(0);
    expect(parseOffset("-5")).toBe(0);
    expect(parseOffset("-1e999")).toBe(0);
  });

  it("truncates a fractional offset, because OFFSET takes a bigint", () => {
    expect(parseOffset("1.9")).toBe(1);
    expect(parseOffset("-0.5")).toBe(0);
  });

  it("clamps Infinity, which pg would otherwise send as the string 'Infinity'", () => {
    // Number("1e999") is Infinity. node-postgres stringifies it into the wire
    // protocol verbatim and bigint input rejects the literal.
    expect(parseOffset("1e999")).toBe(MAX_OFFSET);
    expect(Number.isSafeInteger(parseOffset("1e999"))).toBe(true);
  });

  it("clamps a huge but finite offset to a safe integer", () => {
    // Above MAX_SAFE_INTEGER, `offset + PAGE_SIZE` in the "next" link stops
    // moving, so the reader is stranded on a page that cannot advance.
    expect(parseOffset("99999999999999999999")).toBe(MAX_OFFSET);
    expect(parseOffset(String(Number.MAX_SAFE_INTEGER))).toBe(MAX_OFFSET);
  });

  it("collapses a repeated offset rather than reading NaN from an array", () => {
    expect(parseOffset(["50", "75"])).toBe(50);
  });
});

describe("parseReviewStatus", () => {
  // The allowlist IS the escaping. listReviewQueue builds its predicate as
  // `k.review_status = '${status}'` — interpolated straight into SQL with no
  // placeholder — and that is safe only because the value can only ever be
  // one of these four literals. Every case below is an assertion about SQL
  // safety, not about a default.
  it("passes the four valid values through", () => {
    expect(parseReviewStatus("pending")).toBe("pending");
    expect(parseReviewStatus("approved")).toBe("approved");
    expect(parseReviewStatus("rejected")).toBe("rejected");
    expect(parseReviewStatus("all")).toBe("all");
  });

  it("falls back to pending for absent and empty", () => {
    expect(parseReviewStatus(undefined)).toBe("pending");
    expect(parseReviewStatus("")).toBe("pending");
  });

  it("falls back to pending for the wrong case and unknown values", () => {
    expect(parseReviewStatus("APPROVED")).toBe("pending");
    expect(parseReviewStatus("Pending")).toBe("pending");
    expect(parseReviewStatus("draft")).toBe("pending");
  });

  it("falls back to pending for an injection-shaped value", () => {
    expect(parseReviewStatus("pending' OR '1'='1")).toBe("pending");
    expect(parseReviewStatus("x'; DROP TABLE knowledge_item; --")).toBe("pending");
    // Not a prefix match, not a trim: only exact membership passes.
    expect(parseReviewStatus(" pending")).toBe("pending");
    expect(parseReviewStatus("pendingx")).toBe("pending");
  });

  it("collapses a repeated status", () => {
    expect(parseReviewStatus(["approved", "rejected"])).toBe("approved");
    expect(parseReviewStatus(["nonsense", "approved"])).toBe("pending");
  });
});

describe("parseBrowseFilters", () => {
  it("maps every URL name to its BrowseFilters field", () => {
    // The one snake_case -> camelCase translation. It used to be hand-written
    // in browse/page.tsx against a hand-retyped copy of the URL names.
    expect(
      parseBrowseFilters({
        document: "crib-water",
        item_type: "benchmark",
        topic: "wind",
        scale: "district",
        level: "good",
        q: "shading",
      }),
    ).toEqual({
      documentSlug: "crib-water",
      itemType: "benchmark",
      topicId: "wind",
      scaleId: "district",
      levelId: "good",
      q: "shading",
    });
  });

  it("reads an absent filter as absent rather than as an empty string", () => {
    expect(parseBrowseFilters({})).toEqual({});
    expect(parseBrowseFilters({ document: "", topic: undefined })).toEqual({});
  });

  it("reads a whitespace-only q as absent", () => {
    // listKnowledgeItems branches on `if (filters.q)`, so ?q=%20 used to
    // switch the page onto the ranked path and run three ranking legs plus a
    // headline over the corpus to find nothing — then report "by relevance"
    // over an empty list.
    expect(parseBrowseFilters({ q: "   " })).toEqual({});
    expect(parseBrowseFilters({ q: "\t\n" })).toEqual({});
  });

  it("trims the value it does keep", () => {
    expect(parseBrowseFilters({ q: "  shading  " })).toEqual({ q: "shading" });
  });

  it("collapses a repeated key instead of handing an array to the SQL", () => {
    expect(parseBrowseFilters({ document: ["a", "b"] })).toEqual({ documentSlug: "a" });
  });

  it("ignores keys that are not filters", () => {
    // offset is pagination, from/ret describe the journey, status belongs to
    // the review queue. None of them is a browse filter, and a stray one must
    // not become a predicate.
    expect(
      parseBrowseFilters({
        q: "shading",
        offset: "20",
        from: "review",
        ret: "/review?status=pending",
        status: "pending",
        documentSlug: "already-camel",
      }),
    ).toEqual({ q: "shading" });
  });
});

describe("page sizes", () => {
  it("exposes one number per surface", () => {
    // review/page.tsx typed 25 into two pagination links against a separate
    // `opts.limit ?? 25` default in queries.ts; the page now passes this in.
    expect(REVIEW_PAGE_SIZE).toBeGreaterThan(0);
    expect(BROWSE_PAGE_SIZE).toBeGreaterThan(0);
  });
});
