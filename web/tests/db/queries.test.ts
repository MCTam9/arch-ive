// What the browse queries are allowed to show, pinned.
//
// tests/test_facet_subtree.py pins the subtree rule on the Python side and
// says in its own docstring that these tests pin it "for the web as much as
// for Python" -- but the web had no test to pin it with. The predicate here
// is no longer a copy of the Python one: both call item_has_term() in
// db/schema.sql. That is exactly why this file exists. A shared function is
// only one seam if both sides are held to it; otherwise it is one more place
// for the web to drift while the Python suite stays green.
//
// The five policy decisions under test, all of them decisions someone can
// undo by accident:
//   * a term matches its whole ltree subtree, downward only
//   * an item carrying two terms in that subtree is returned once, not twice
//   * rejected items are gone from browse, and still visible to the review
//     queue and the item page, which is where a reviewer undoes the decision
//   * a citation is the item's FIRST page, not whichever one the planner
//     reached for
//   * placeholder content is SHOWN, labelled -- the web does not filter it
//
// Fixture discipline: arch_test is shared with the Python suite and holds
// leftover rows from it. Everything here is namespaced with a random tag and
// EVERY assertion is scoped to this fixture's own document. An unscoped
// count would pass or fail depending on what else had run.
import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { withAccount } from "@/lib/db";
import {
  getFacetOptions,
  getHomeSummary,
  getKnowledgeItem,
  listKnowledgeItems,
  listReviewQueue,
  type BrowseItem,
} from "@/lib/queries";
import { TEST_ACCOUNT_ID } from "../support";

const tag = randomUUID().slice(0, 8);
const slug = `web-ts-${tag}`;
// One lexeme that exists nowhere else in the database, so the ranked path's
// result set is this fixture's and nothing else's. Scoped by documentSlug on
// top of that -- belt and braces, because a stemmer collision would be a
// maddening way to find out arch_test is shared.
const TOKEN = `zqxfixture${tag}`;
const parentTerm = `t${tag}.parent`;
const childTerm = `t${tag}.parent.child`;

// A body long enough to clear v_retrievable_chunk's 40-character page-text
// floor. Item and figure chunks are exempt from it; page chunks are not, and
// one of the tests below turns on that difference.
const body = (what: string) =>
  `${TOKEN} ${what} — a body of text comfortably past the forty character floor.`;

type Ids = {
  documentId: string;
  page0: string;
  page1Wip: string;
  page2: string;
  assetOnWipPage: string;
  onParent: string;
  onChild: string;
  rejected: string;
  placeholderItem: string;
};
let ids: Ids;

beforeAll(async () => {
  ids = await withAccount(TEST_ACCOUNT_ID, async (client) => {
    const one = async (sql: string, params: unknown[] = []) =>
      (await client.query(sql, params)).rows[0];

    // Terms go under the seeded 'topic' taxonomy rather than a private one,
    // so getFacetOptions and getHomeSummary -- which select taxonomy_id IN
    // ('topic','scale','level') -- can actually see them. Their ids and paths
    // are still tag-namespaced, so nothing collides.
    await client.query(
      // The path is passed a second time rather than reusing $1: Postgres
      // deduces one type per parameter, and text-and-ltree at once fails the
      // statement with 42P08 "inconsistent types deduced for parameter $1".
      `INSERT INTO taxonomy_term (id, taxonomy_id, code, label, path, parent_id, ordinal)
       VALUES ($1, 'topic', 'parent', 'fixture parent', $3::ltree, NULL, 900),
              ($2, 'topic', 'child',  'fixture child',  $4::ltree, $1,   901)`,
      [parentTerm, childTerm, parentTerm, childTerm],
    );

    const doc = await one(
      `INSERT INTO source_document (slug, title, doc_kind, sha256)
       VALUES ($1, 'fixture document', 'framework', $2) RETURNING id::text`,
      [slug, (randomUUID() + randomUUID()).replace(/-/g, "")],
    );

    // Page 1 is marked 'wip'. The figure below sits on it and carries
    // content_status 'real' of its own -- the 141-chunk case the retrieval
    // policy exists to catch.
    const pages: string[] = [];
    for (const [index, status] of [
      [0, "real"],
      [1, "wip"],
      [2, "real"],
    ] as const) {
      const p = await one(
        `INSERT INTO source_page (document_id, page_index, printed_page_label,
                                  content_status, text)
         VALUES ($1, $2, $3, $4::content_status, $5) RETURNING id::text`,
        [doc.id, index, `p${index}`, status, `page ${index}`],
      );
      pages.push(p.id);
    }

    const asset = await one(
      `INSERT INTO source_asset (page_id, code, vlm_description, vlm_model, image_key)
       VALUES ($1, 'fig-1', 'a described figure', 'test-vlm', 'fixture/fig-1.png')
       RETURNING id::text`,
      [pages[1]],
    );

    const item = async (
      title: string,
      itemType: string,
      contentStatus: string,
      reviewStatus: string,
    ) =>
      (
        await one(
          `INSERT INTO knowledge_item (document_id, item_type, title, statement,
                                       content_status, review_status)
           VALUES ($1, $2::item_type, $3, $4, $5::content_status, $6::review_status)
           RETURNING id::text`,
          [doc.id, itemType, title, body(title), contentStatus, reviewStatus],
        )
      ).id as string;

    const onParent = await item("on parent", "guidance", "real", "pending");
    const onChild = await item("on child", "guidance", "real", "pending");
    const rejected = await item("rejected", "guidance", "real", "rejected");
    // A different item_type, so the item-type facet has something to exclude
    // that is still an item -- an item excluded by a facet is not a
    // suppression, it is just a filter doing its job.
    const placeholderItem = await item("placeholder", "benchmark", "wip", "pending");

    // onChild carries BOTH terms in the subtree. A JOIN-based filter returns
    // it once per matching descendant; EXISTS returns it once.
    await client.query(
      `INSERT INTO item_term (knowledge_item_id, term_id) VALUES
         ($1, $4), ($2, $5), ($2, $4), ($3, $4)`,
      [onParent, onChild, rejected, parentTerm, childTerm],
    );

    // onParent's citations are inserted high page first. The first citation
    // is the one with the LOWEST page_index, not the first one written.
    await client.query(
      `INSERT INTO citation (knowledge_item_id, document_id, page_index, printed_page_label)
       VALUES ($1, $5, 5, 'p5'), ($1, $5, 2, 'p2'),
              ($2, $5, 3, 'p3'), ($3, $5, 4, 'p4'), ($4, $5, 0, 'p0')`,
      [onParent, onChild, rejected, placeholderItem, doc.id],
    );

    const chunk = async (
      text: string,
      opts: { item?: string; asset?: string; pageFrom: number; status?: string },
    ) =>
      client.query(
        `INSERT INTO chunk (document_id, knowledge_item_id, asset_id, page_from,
                            ordinal, text, content_status)
         VALUES ($1, $2, $3, $4, 0, $5, $6::content_status)`,
        [doc.id, opts.item ?? null, opts.asset ?? null, opts.pageFrom, text, opts.status ?? "real"],
      );

    await chunk(body("parent item chunk"), { item: onParent, pageFrom: 2 });
    await chunk(body("child item chunk"), { item: onChild, pageFrom: 3 });
    await chunk(body("placeholder item chunk"), { item: placeholderItem, pageFrom: 0 });
    // Rejected: retrievable in every other respect, and must not come back.
    await chunk(body("rejected item chunk"), { item: rejected, pageFrom: 4 });
    // Raw page text, over the floor.
    await chunk(body("page chunk"), { pageFrom: 0 });
    // Raw page text, under the floor. Its own page, so its absence is
    // observable rather than merged into the result above.
    await chunk(`${TOKEN} tiny`, { pageFrom: 2 });
    // The figure: 'real' itself, on a page that is not.
    await chunk(body("figure description"), { asset: asset.id, pageFrom: 1, status: "real" });

    return {
      documentId: doc.id,
      page0: pages[0],
      page1Wip: pages[1],
      page2: pages[2],
      assetOnWipPage: asset.id,
      onParent,
      onChild,
      rejected,
      placeholderItem,
    };
  });
});

// Safe after a half-built beforeAll: both statements are unconditional
// deletes over tag-namespaced keys and match zero rows if nothing was
// created. Nothing here is scoped by anything a partial fixture could have
// got wrong. The document cascades to its pages, assets, chunks, items,
// citations and item_terms; the parent term cascades to the child.
afterAll(async () => {
  await withAccount(TEST_ACCOUNT_ID, async (client) => {
    await client.query(`DELETE FROM source_document WHERE slug = $1`, [slug]);
    await client.query(`DELETE FROM taxonomy_term WHERE id IN ($1, $2)`, [
      parentTerm,
      childTerm,
    ]);
  });
});

const idsOf = (items: BrowseItem[]) => items.map((i) => i.id);
const byId = (items: BrowseItem[], id: string) => items.find((i) => i.id === id);

// ── subtree matching ──────────────────────────────────────────────────────

describe("term filters match the whole subtree", () => {
  test("a parent term returns items tagged on its children", async () => {
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      topicId: parentTerm,
    });
    expect(new Set(idsOf(items))).toEqual(new Set([ids.onParent, ids.onChild]));
  });

  test("a child term does not drag in its parent", async () => {
    // The subtree runs downward only. Both ways and every leaf filter would
    // pull in the parent's other branches.
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      topicId: childTerm,
    });
    expect(idsOf(items)).toEqual([ids.onChild]);
  });

  test("an item carrying two terms in the subtree is returned once", async () => {
    // EXISTS, not a JOIN. A join matches one row per descendant term, which
    // duplicates the row AND inflates count(*) OVER () -- so the header would
    // claim three items above a list of two.
    const { items, total } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      topicId: parentTerm,
    });
    expect(idsOf(items)).toHaveLength(new Set(idsOf(items)).size);
    expect(total).toBe(2);
  });

  test("the subtree rule survives into the ranked path", async () => {
    // Same predicate string, different surface: v_retrievable_chunk rather
    // than v_retrievable_item. If only one of them had been converted this is
    // the test that would say so.
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      topicId: parentTerm,
      q: TOKEN,
    });
    expect(new Set(idsOf(items))).toEqual(new Set([ids.onParent, ids.onChild]));
  });

  test("the facet count is the number the filter returns", async () => {
    // v_term_item_count exists so the advertised count and the result cannot
    // disagree. Scoped by looking up this fixture's own term, never by
    // asserting on the list's length.
    const facets = await getFacetOptions(TEST_ACCOUNT_ID);
    expect(facets.topics.find((t) => t.id === parentTerm)?.n).toBe(2);
    expect(facets.topics.find((t) => t.id === childTerm)?.n).toBe(1);

    const home = await getHomeSummary(TEST_ACCOUNT_ID);
    expect(home.topics.find((t) => t.id === parentTerm)?.n).toBe(2);
  });
});

// ── rejected ──────────────────────────────────────────────────────────────

describe("rejected items", () => {
  test("are not in browse, ranked or unranked", async () => {
    const listed = await listKnowledgeItems(TEST_ACCOUNT_ID, { documentSlug: slug });
    expect(idsOf(listed.items)).not.toContain(ids.rejected);
    expect(new Set(idsOf(listed.items))).toEqual(
      new Set([ids.onParent, ids.onChild, ids.placeholderItem]),
    );

    const searched = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      q: TOKEN,
    });
    expect(idsOf(searched.items)).not.toContain(ids.rejected);
  });

  test("are not counted in the facet the browse list is filtered by", async () => {
    // The count promising a row the filter will not return is the failure
    // mode this is here for -- three tagged items, two of them retrievable.
    const facets = await getFacetOptions(TEST_ACCOUNT_ID);
    expect(facets.topics.find((t) => t.id === parentTerm)?.n).toBe(2);
  });

  test("are still visible to the review queue", async () => {
    // The queue filters review_status itself and reads the base table on
    // purpose: a rejected row that cannot be listed cannot be un-rejected.
    const { items } = await listReviewQueue(TEST_ACCOUNT_ID, {
      document: slug,
      status: "rejected",
    });
    expect(items.map((i) => i.id)).toContain(ids.rejected);
  });

  test("still render on their own item page", async () => {
    // The review queue links straight to /item/[id]. Mirrors MCP's
    // get_citation, which reads the base table for the same reason.
    const item = await getKnowledgeItem(TEST_ACCOUNT_ID, ids.rejected);
    expect(item).not.toBeNull();
    expect(item?.review_status).toBe("rejected");
  });
});

// ── citations ─────────────────────────────────────────────────────────────

describe("citation shaping", () => {
  test("an item cites its first page, not an arbitrary one", async () => {
    // onParent's citations were written page 5 first. Ordering by page_index
    // is the contract; insertion order is not.
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, { documentSlug: slug });
    expect(byId(items, ids.onParent)?.page_index).toBe(2);
    expect(byId(items, ids.onParent)?.printed_page_label).toBe("p2");
  });

  test("an item with several citations is still one row", async () => {
    // LATERAL … LIMIT 1. A plain join multiplies the item by its pages, which
    // is how a browse total comes back larger than the corpus.
    const { items, total } = await listKnowledgeItems(TEST_ACCOUNT_ID, { documentSlug: slug });
    expect(items.filter((i) => i.id === ids.onParent)).toHaveLength(1);
    expect(total).toBe(3);
  });

  test("the ranked path cites the same page", async () => {
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      q: TOKEN,
    });
    expect(byId(items, ids.onParent)?.page_index).toBe(2);
  });
});

// ── placeholder content is shown, labelled ────────────────────────────────

describe("placeholder content", () => {
  test("appears in browse rather than being filtered out", async () => {
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, { documentSlug: slug });
    const row = byId(items, ids.placeholderItem);
    expect(row).toBeDefined();
    expect(row?.content_status).toBe("wip");
    expect(row?.is_placeholder).toBe(true);
  });

  test("a figure on a work-in-progress page is stamped, not called real", async () => {
    // The decision this commit implements. The figure chunk's own
    // content_status is 'real'; the page it describes is 'wip'. Reading the
    // item's column alone renders a description of unfinished work as fact --
    // 141 chunks of it on the dev corpus.
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      q: TOKEN,
    });
    const figure = items.find((i) => i.kind === "figure");
    expect(figure).toBeDefined();
    expect(figure?.asset_id).toBe(ids.assetOnWipPage);
    expect(figure?.content_status).toBe("wip");
    expect(figure?.is_placeholder).toBe(true);
    // Labelled, not withheld: it still carries the page to land on and the
    // model that wrote it.
    expect(figure?.page_id).toBe(ids.page1Wip);
    expect(figure?.vlm_model).toBe("test-vlm");
  });
});

// ── the three kinds, and what a facet hides ───────────────────────────────

describe("ranked search across the three kinds", () => {
  test("returns items, pages and figures, and applies the page-text floor", async () => {
    const { items, kindCounts, suppressed } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      q: TOKEN,
    });
    expect(kindCounts).toEqual({ item: 3, page: 1, figure: 1 });
    expect(suppressed).toBe(0);
    // The under-floor page chunk is on page 2 and is the only thing there. A
    // page result for it would mean the floor had been dropped -- and the
    // floor is page-only, which the three item chunks above it prove.
    expect(items.filter((i) => i.kind === "page").map((i) => i.page_index)).toEqual([0]);
  });

  test("an item-type facet suppresses pages and figures, and says so", async () => {
    // The bug this pins: `ki.item_type = ?` is NULL for a page or figure
    // chunk, `NOT ok` never fires on a NULL, so the two results below were
    // dropped from the list AND reported as `suppressed: 0`. The sentence the
    // browse page prints to explain the disappearance was the thing that
    // disappeared.
    const { items, total, suppressed, kindCounts } = await listKnowledgeItems(
      TEST_ACCOUNT_ID,
      { documentSlug: slug, itemType: "guidance", q: TOKEN },
    );
    expect(suppressed).toBe(2);
    expect(total).toBe(2);
    expect(kindCounts).toEqual({ item: 2 });
    expect(new Set(idsOf(items))).toEqual(new Set([ids.onParent, ids.onChild]));
  });

  test("a facet that matches nothing still reports what it hid", async () => {
    // The counts drive the FROM and the page hangs off them, so an empty page
    // still carries its explanation. Driving from the results would return
    // zero rows -- in precisely the case where the sentence is worth reading.
    const { items, total, suppressed } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      itemType: "role",
      q: TOKEN,
    });
    expect(items).toEqual([]);
    expect(total).toBe(0);
    expect(suppressed).toBe(2);
  });

  test("a topic facet suppresses them too", async () => {
    const { suppressed, kindCounts } = await listKnowledgeItems(TEST_ACCOUNT_ID, {
      documentSlug: slug,
      topicId: parentTerm,
      q: TOKEN,
    });
    expect(suppressed).toBe(2);
    expect(kindCounts).toEqual({ item: 2 });
  });

  test("an unfiltered search still reaches this document", async () => {
    // No documentSlug: the ranked path's WHERE is empty now that the
    // page-text floor lives in the view, and a bare `WHERE` is a syntax
    // error. The token is unique, so the assertion stays scoped.
    const { items } = await listKnowledgeItems(TEST_ACCOUNT_ID, { q: TOKEN });
    const mine = items.filter((i) => i.document_slug === slug);
    expect(new Set(idsOf(mine))).toEqual(
      new Set([
        ids.onParent,
        ids.onChild,
        ids.placeholderItem,
        `${ids.documentId}:0`,
        ids.assetOnWipPage,
      ]),
    );
  });
});
