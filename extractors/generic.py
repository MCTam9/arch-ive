"""Fallback extractor. doc_kinds = ("unknown", "guideline_report", "deck", "standard")

Registered for "unknown" so `pipeline.for_doc_kind` always resolves to
*something* -- this is what guarantees an unrecognised document still gets
ingested as searchable text rather than silently dropped. It is also the
extractor for shapes nobody has written a dedicated one for yet
(guideline_report, deck, standard): one `Item(item_type="guidance")` per
structural section, or per page when the document has no section structure,
each carrying a citation and the page's text.

Must never fail: an exception here means an entire document goes dark. Every
external call is wrapped so one bad page degrades to "skip that page,"
never "crash the extraction."

Pages whose `content_status` is not "real" (lorem/template/wip stamps,
already flagged upstream by whatever set ctx.pages[i]['content_status']) are
skipped outright -- ingesting placeholder text as guidance would be worse
than not ingesting it.
"""
from __future__ import annotations

import re

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, register

MAX_STATEMENT_CHARS = 4000

# stamps that mean "this page is not real content", independent of whatever
# content_status the page row already carries -- belt and braces, since this
# extractor is the one place a bad document cannot be allowed to look fine.
PLACEHOLDER_RE = re.compile(
    r"\bTEMPLATE\s+ONLY\b|\bWIP\b|\blorem\s+ipsum\b", re.I
)


def _looks_like_placeholder(text: str) -> bool:
    if not text:
        return False
    if PLACEHOLDER_RE.search(text):
        return True
    # lorem ipsum without the literal words: a handful of its stock tokens
    # co-occurring is a strong enough signal without hardcoding a whole copy
    # of the passage.
    tokens = ("dolor", "consectetur", "adipiscing", "elit", "tempor", "incididunt")
    hits = sum(1 for t in tokens if t in text.lower())
    return hits >= 3


def _clean_title(text: str, limit: int = 120) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t[:limit].rstrip()


class GenericExtractor:
    doc_kinds = ("unknown", "guideline_report", "deck", "standard")

    def extract(self, ctx: DocumentContext) -> Extraction:
        ext = Extraction()
        try:
            self._extract(ctx, ext)
        except Exception as exc:  # noqa: BLE001 - this extractor must never raise
            ext.warnings.append(
                f"{ctx.slug}: generic extraction hit an unexpected error "
                f"({type(exc).__name__}); returning whatever was recovered "
                f"before it"
            )
        return ext

    def _extract(self, ctx: DocumentContext, ext: Extraction) -> None:
        root_ref = f"{ctx.slug}-root"
        ext.nodes.append(Node(node_kind="volume", title=ctx.slug, ordinal=0,
                               page_from=1, page_to=max(ctx.page_count, 1), ref=root_ref))

        sections = self._structural_sections(ctx)
        n_items = 0
        n_skipped = 0

        if sections:
            for sec in sections:
                node_ref = f"{ctx.slug}-sec-{sec['ordinal']}"
                ext.nodes.append(Node(
                    node_kind="section", title=sec["title"], ordinal=sec["ordinal"],
                    page_from=sec["page_from"], page_to=sec["page_to"],
                    parent_ref=root_ref, ref=node_ref,
                ))
                pages_in_section = [p for p in ctx.pages
                                     if sec["page_from"] <= p.get("page_index", 0) <= sec["page_to"]]
                added, skipped = self._emit_pages(ctx, pages_in_section, node_ref, sec["title"], ext)
                n_items += added
                n_skipped += skipped
        else:
            added, skipped = self._emit_pages(ctx, ctx.pages, root_ref, None, ext)
            n_items += added
            n_skipped += skipped

        ext.stats[f"{ctx.slug}_guidance_items"] = n_items
        ext.stats[f"{ctx.slug}_pages_skipped_not_real"] = n_skipped
        if n_items == 0:
            ext.warnings.append(
                f"{ctx.slug}: no guidance items produced -- either every page "
                f"was flagged not-real, or the document had no pages/text at all"
            )

    @staticmethod
    def _structural_sections(ctx: DocumentContext) -> list[dict]:
        """Best-effort section list from whatever doc_node-shaped hints are
        already on the context (build_structure's output, if this extractor
        runs after it). No structure is a normal outcome, not an error --
        every page just becomes its own item."""
        raw = ctx.meta.get("sections") if isinstance(ctx.meta, dict) else None
        if not raw:
            return []
        out = []
        for i, sec in enumerate(raw):
            try:
                pf = int(sec.get("page_from"))
                pt = int(sec.get("page_to", pf))
                title = str(sec.get("title") or f"Section {i + 1}")
            except (TypeError, ValueError, AttributeError):
                continue
            out.append({"ordinal": i, "title": title, "page_from": pf, "page_to": pt})
        return out

    def _emit_pages(self, ctx: DocumentContext, pages: list[dict], node_ref: str,
                     section_title: str | None, ext: Extraction) -> tuple[int, int]:
        added = 0
        skipped = 0
        for page in sorted(pages, key=lambda p: p.get("page_index", 0)):
            page_index = page.get("page_index")
            if page_index is None:
                continue
            status = page.get("content_status", "real")
            text = page.get("text") or ""
            if status != "real" or _looks_like_placeholder(text):
                skipped += 1
                continue
            if not text.strip():
                skipped += 1
                continue

            statement = text.strip()
            truncated = len(statement) > MAX_STATEMENT_CHARS
            if truncated:
                statement = statement[:MAX_STATEMENT_CHARS].rstrip()

            title = section_title or _clean_title(statement.splitlines()[0]) or None
            ext.items.append(Item(
                item_type="guidance",
                title=_clean_title(title) if title else None,
                statement=statement,
                summary=None,
                node_ref=node_ref,
                content_status=status,
                confidence=0.5,
                citations=[Citation(page_index=page_index,
                                     printed_page_label=page.get("printed_page_label"))],
                payload={
                    "body_md": None,
                    "figure_ids": [],
                    "legend_tokens": [],
                    "disclaimer": "truncated by the generic fallback extractor" if truncated else None,
                },
            ))
            added += 1
        return added, skipped


GENERIC = GenericExtractor()
register(GENERIC)
