// Imported through the "@/" alias on purpose: it is the one piece of
// vitest.config.ts that no assertion can otherwise reach, and a broken alias
// would show up as "cannot find module" in every future suite instead of here.
import { describe, expect, it } from "vitest";
import {
  BROWSE_PATH,
  backLink,
  browseHref,
  browseParams,
  href,
  safePath,
} from "@/lib/links";

describe("href", () => {
  it("drops empty, null and undefined values", () => {
    expect(
      href("/browse", { document: "", topic: undefined, scale: null, q: "wind" }),
    ).toBe("/browse?q=wind");
  });

  it("keeps a literal 0", () => {
    // The reason the guard tests three values rather than falsiness: offset 0
    // is page one, not "unset", and `if (!value)` would drop it and send the
    // reader back to wherever the default offset points.
    expect(href("/browse", { offset: 0 })).toBe("/browse?offset=0");
  });

  it("returns a bare path when nothing survives", () => {
    expect(href("/browse", { document: "", topic: undefined })).toBe("/browse");
    expect(href("/browse")).toBe("/browse");
  });

  it("encodes separators so a value cannot inject a parameter", () => {
    // URLSearchParams spells a space "+", not "%20"; both decode the same and
    // the point of the assertion is that neither the space nor the "&" reaches
    // the URL raw.
    expect(href("/browse", { q: "wind & rain" })).toBe("/browse?q=wind+%26+rain");
  });
});

describe("safePath", () => {
  it("accepts a path, query string and all", () => {
    expect(safePath("/browse?topic=wind&offset=20")).toBe(
      "/browse?topic=wind&offset=20",
    );
  });

  it("rejects nothing at all", () => {
    expect(safePath(undefined)).toBeNull();
    expect(safePath(null)).toBeNull();
    expect(safePath("")).toBeNull();
  });

  it("rejects absolute and protocol-relative URLs", () => {
    expect(safePath("https://evil.example/browse")).toBeNull();
    expect(safePath("//evil.example")).toBeNull();
    expect(safePath("evil.example")).toBeNull();
  });

  it("rejects a backslash in the second position", () => {
    // Chrome and Safari normalise "\" to "/" before resolving, so both of
    // these are "//evil.example" by the time a redirect follows them. The
    // pre-fix guard tested only startsWith("//") and let them through.
    expect(safePath("/\\evil.example")).toBeNull();
    expect(safePath("/\\/evil.example")).toBeNull();
  });
});

describe("backLink", () => {
  it("labels and points at the section the reader came from", () => {
    expect(backLink("matrix", undefined)).toEqual({
      label: "matrix",
      href: "/matrix",
    });
    expect(backLink("review", undefined)).toEqual({
      label: "review queue",
      href: "/review",
    });
    expect(backLink(undefined, undefined)).toEqual({
      label: "browse",
      href: BROWSE_PATH,
    });
  });

  it("prefers a safe ret, so the return trip keeps the filters", () => {
    expect(backLink("review", "/review?status=pending&offset=25")).toEqual({
      label: "review queue",
      href: "/review?status=pending&offset=25",
    });
  });

  it("drops a hostile ret but keeps the label", () => {
    // Degrading to the section page rather than to browse: the reader still
    // came from the review queue, and only the destination was untrustworthy.
    expect(backLink("review", "//evil.example")).toEqual({
      label: "review queue",
      href: "/review",
    });
    expect(backLink("matrix", "/\\evil.example")).toEqual({
      label: "matrix",
      href: "/matrix",
    });
  });

  it("falls back to browse for an unknown from", () => {
    expect(backLink("styleguide", undefined)).toEqual({
      label: "browse",
      href: BROWSE_PATH,
    });
  });
});

describe("browseParams", () => {
  it("keeps the browse filters and drops everything else", () => {
    expect(
      browseParams({
        document: "crib-water",
        item_type: "benchmark",
        topic: "wind",
        scale: "district",
        level: "good",
        q: "shading",
        offset: "20",
        // from/ret describe the journey, not the query; carrying them into a
        // browse URL makes browse claim it was reached from somewhere it wasn't.
        from: "review",
        ret: "/review?status=pending",
        status: "pending",
      }),
    ).toEqual({
      document: "crib-water",
      item_type: "benchmark",
      topic: "wind",
      scale: "district",
      level: "good",
      q: "shading",
      offset: "20",
    });
  });

  it("drops empty values", () => {
    expect(browseParams({ topic: "wind", document: "", scale: undefined })).toEqual({
      topic: "wind",
    });
  });

  it("round-trips through browseHref", () => {
    const sp = { topic: "wind", q: "shading", from: "matrix" };
    const url = new URL(browseHref(browseParams(sp)), "https://arch-ive.example");
    expect(url.pathname).toBe(BROWSE_PATH);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      topic: "wind",
      q: "shading",
    });
  });
});
