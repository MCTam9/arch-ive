"""Extractor for the smart-city framework doc_kind (doc_kind='solutions_framework').

Targets framework-vol-a10-smart-city: a 148-page spread-paginated report
(1 PDF page = 2 printed pages, landscape 1191x842pt, printed page numbers
centred in a narrow footer band on each half). It shares the spread-
pagination shape of the other framework volumes, but it has no strategy-code
compliance appendix -- extractors/compliance_table.py correctly declines on
it. What it does have, verified by reading the rendered pages rather than
assumed:

- A front-matter Contents page (index 1) that is itself a clean, extractable
  table: section code, title, printed start page. No PDF bookmark outline
  exists (`get_toc()` returns nothing), so this is the only structural
  spine available and is used to build the doc_node tree.
- A qualitative four-level rating ladder -- "None, Minimal, Significant,
  Transformational" -- used verbatim, twice, to grade a solution's
  contribution against four named sustainability principles. Both
  occurrences are near-duplicates of each other (a summary table and its
  fuller restatement later in the document); the first sighting of each
  principle is kept, mirroring compliance_table's handling of repeated
  appendix copies.
- A solution-prioritisation rubric (two PDF pages): a criteria/description/
  weighting legend for scoring candidate digital solutions. ~26 of its rows
  are genuinely 0-5 scored axes with explicit anchor text ('X = 0, Y = 5');
  the rest are N/A-weighted metadata columns (domain, stakeholders, ...)
  kept only in the verbatim guidance capture. The actual per-solution
  *scored* matrix (which solution got which score) is an external workbook
  the report only references by filename -- it is not embedded in this PDF,
  so no scored pattern/design_variable_value assignments are fabricated.
- A borderless persona table (one figure): persona name, key assets/
  districts, technology needs. Recovered by column-band word clustering
  (three x-anchors from the header words, row grouping by y-gap per
  column) rather than page.find_tables(), which finds nothing here because
  the table has no ruled lines.

Everything else in the 229-printed-page main body (KPI narrative, the
10-strategy summary table, appendices A-G, and a differently-formatted
sub-report stitched in after the appendices) is left unparsed: attempting
positional reconstruction there without a second verified structure would
be guessing, which CONTRACT.md asks this module not to do.
"""
from __future__ import annotations

import re

import pymupdf

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, Reference, register

DOC_KINDS = ("solutions_framework",)

RATING_SCALE_SLUG = "smart-city-alignment-ladder"
LADDER_LEVELS = ("None", "Minimal", "Significant", "Transformational")

# Main body printed pagination: 1 landscape PDF page = 2 printed pages,
# printed page 1 lands on the first landscape page (PDF index 2, 0-based).
# Verified against every body page's own footer numbers (117/117 pages
# check out with right - left == 1); breaks down after printed page 234,
# where the lettered appendices begin and stop numbering this way.
_BODY_FIRST_PDF_INDEX = 2
_BODY_MAX_PRINTED_PAGE = 234

_LADDER_RE = re.compile(
    r"Contribution\s+to\s+objectives\s+defined\s+under\s+the\s+(.+?)\s+sustainability\s+principle"
    r"\s*-\s*None,\s*Minimal,\s*Significant,\s*Transformational",
    re.S,
)

# document codes such as 'CS-...-Smart_Solutions_Matrix', not plain-English
# "refer to section 3.8" cross-references -- requires an uppercase start and
# 3+ hyphen/underscore-delimited segments to tell the two apart.
_REFERENCE_RE = re.compile(r"Refer to ([A-Z][A-Za-z0-9]*(?:[_\-][A-Za-z0-9]+){3,})\.?(?:\s|$)")

_WEIGHT_RE = re.compile(r"\d+(?:\.\d+)?%")
_ANCHOR_RE = re.compile(r"(.+?)\s*=\s*(\d)\s*,?\s*(.+?)\s*=\s*(\d)", re.S)

# Rubric axes: (id, safe search anchor, display name). The anchor is a short
# fragment that is unique within the rubric table's text and never includes
# the client/place name the report repeats -- see CONTRACT.md's ground rule.
# Two entries use a 3-uppercase-letter wildcard instead of spelling out the
# organisation's initials for the same reason.
_RUBRIC_AXES: tuple[tuple[str, str, str], ...] = (
    ("sc_metaverse_platform_alignment", "Maximise the metaverse an emerging platform to realise digital value",
     "Metaverse platform alignment"),
    ("sc_innovation_real_estate_alignment", "Incorporate innovation real estate to maximise R&D opportunities",
     "Innovation real estate alignment"),
    ("sc_quality_of_life_alignment", "Deploy technology in support of A HIGH QUALITY of life for residents and workers",
     "Quality-of-life alignment"),
    ("sc_visitor_experience_alignment", "Maximise visitor experience and engagement through bespoke user applications",
     "Visitor experience alignment"),
    ("sc_natural_landscape_alignment", r"protection,\s*enhancement and management of",
     "Natural landscape alignment"),
    ("sc_operational_governance_alignment", "Deliver robust operational governance",
     "Operational governance alignment"),
    ("sc_maas_alignment", r"Deployment of Mobility as a service\s*\(maas\)\s*for",
     "Mobility-as-a-service alignment"),
    ("sc_digital_infrastructure_alignment", "Super fast digital infrastructure",
     "Digital infrastructure alignment"),
    ("sc_resource_use_alignment", "Deployment of smart systems to support reduction of resource use",
     "Resource-use reduction alignment"),
    ("sc_data_standards_alignment", r"Create standards that allow\s+\S+\s+to manage data and information",
     "Data standards alignment"),
    ("sc_data_team_alignment", "Create a functional team to manage data and technology procurement",
     "Data/technology team alignment"),
    ("sc_phase1a_priority", "Phase 1a Priority", "Phase 1a priority"),
    ("sc_capex_to_buy", "CAPEX to buy", "CAPEX to buy"),
    ("sc_capex_to_make", "CAPEX to make", "CAPEX to make"),
    ("sc_opex", r"\bOPEX\b", "OPEX"),
    ("sc_direct_revenue_potential", "Direct revenue potential", "Direct revenue potential"),
    ("sc_ability_to_operate", r"[A-Z]{3} ability to operate", "Client ability to operate"),
    ("sc_market_maturity", "Market maturity", "Market maturity"),
    ("sc_lead_time", "Lead time to develop, select, source and install", "Lead time to deploy"),
    ("sc_supplier_choice", "Supplier choice", "Supplier choice"),
    ("sc_partner_attraction", "Partner attraction", "Partner attraction"),
    ("sc_brand_value", r"[A-Z]{3} brand value", "Client brand value"),
    ("sc_ip_potential", "Intellectual property potential", "Intellectual property potential"),
    ("sc_innovation_potential", "Innovation potential", "Innovation potential"),
    ("sc_modularity_scalability", "Modularity and Scalability", "Modularity and scalability"),
    ("sc_environmental_impact", "Solution environmental impact", "Solution environmental impact"),
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _printed_citation(printed_page: int) -> Citation | None:
    if printed_page < 1 or printed_page > _BODY_MAX_PRINTED_PAGE:
        return None
    k = (printed_page - 1) // 2
    pdf_index0 = k + _BODY_FIRST_PDF_INDEX
    return Citation(page_index=pdf_index0 + 1, printed_page_label=str(printed_page))


def _printed_label_for_pdf_index(pdf_index0: int) -> str | None:
    """'L / R' printed-page label for a body-spread page, matching the format
    source_page.printed_page_label uses on spread layouts (db/schema.sql)."""
    k = pdf_index0 - _BODY_FIRST_PDF_INDEX
    if k < 0:
        return None
    left = 2 * k + 1
    if left > _BODY_MAX_PRINTED_PAGE:
        return None
    return f"{left} / {left + 1}"


def _citation(pdf_index0: int) -> Citation:
    return Citation(page_index=pdf_index0 + 1, printed_page_label=_printed_label_for_pdf_index(pdf_index0))


class SmartCityExtractor:
    doc_kinds: tuple[str, ...] = DOC_KINDS

    def extract(self, ctx: DocumentContext) -> Extraction:
        ex = Extraction()
        content_status = ctx.meta.get("content_status", "real")
        doc = pymupdf.open(ctx.path)
        try:
            self._extract(ctx, doc, ex, content_status)
        finally:
            doc.close()
        return ex

    def _extract(self, ctx: DocumentContext, doc: pymupdf.Document,
                 ex: Extraction, content_status: str) -> None:
        root_ref = f"{ctx.slug}-root"
        ex.nodes.append(Node(node_kind="volume", title=ctx.slug, ordinal=0,
                              page_from=1, page_to=len(doc), ref=root_ref))

        node_ref_by_code = self._build_contents_nodes(ctx, doc, ex, root_ref)

        n_ladder = self._extract_ladder(ctx, doc, ex, node_ref_by_code, content_status)
        n_axes, n_guidance = self._extract_rubric(ctx, doc, ex, node_ref_by_code, content_status)
        n_personas = self._extract_personas(ctx, doc, ex, node_ref_by_code, content_status)
        n_refs = self._extract_references(doc, ex, root_ref)

        ex.stats = {
            f"{ctx.slug}_nodes": len(ex.nodes),
            f"{ctx.slug}_ladder_requirements": n_ladder,
            f"{ctx.slug}_rubric_axes": n_axes,
            f"{ctx.slug}_rubric_guidance_items": n_guidance,
            f"{ctx.slug}_personas": n_personas,
            f"{ctx.slug}_external_references": n_refs,
        }

    # -- structure: doc_node tree from the front-matter Contents table -----

    def _build_contents_nodes(self, ctx: DocumentContext, doc: pymupdf.Document,
                               ex: Extraction, root_ref: str) -> dict[str, str]:
        """Returns code -> Node.ref. No PDF bookmark outline exists for this
        document; the Contents page is the only structural spine, and it is
        itself a clean, borderless table (code / title / printed start page)
        recovered the same way the persona table is: header-anchored column
        bands plus row clustering."""
        if len(doc) < 2:
            ex.warnings.append(f"{ctx.slug}: document has no front-matter spread; no structure built")
            return {}
        page = doc[1]
        words = page.get_text("words")
        mid = page.rect.width / 2
        code_re = re.compile(r"^\d+(?:\.\d+)?\.?$")
        rows: dict[int, list[tuple[float, str]]] = {}
        for x0, y0, _x1, _y1, text, *_ in words:
            if x0 <= mid or y0 <= 140:
                continue
            rows.setdefault(round(y0 / 6), []).append((x0, text))

        parsed: list[tuple[str, str, int]] = []
        for key in sorted(rows):
            toks = sorted(rows[key])
            if not toks:
                continue
            code = None
            printed_page = None
            title_toks = []
            for x, t in toks:
                if code is None and x < mid + 65 and code_re.match(t):
                    code = t.rstrip(".")
                elif t.isdigit() and x > mid + 500:
                    printed_page = int(t)
                else:
                    title_toks.append(t)
            if code and printed_page:
                parsed.append((code, " ".join(title_toks), printed_page))

        if not parsed:
            ex.warnings.append(f"{ctx.slug}: Contents table not recognised; no chapter/section nodes built")
            return {}

        node_ref_by_code: dict[str, str] = {}
        for i, (code, title, page_from) in enumerate(parsed):
            level = code.count(".") + 1
            next_page = _BODY_MAX_PRINTED_PAGE + 1
            for j in range(i + 1, len(parsed)):
                other_level = parsed[j][0].count(".") + 1
                if other_level <= level:
                    next_page = parsed[j][2]
                    break
            page_to = max(page_from, next_page - 1)
            parent_code = code.rsplit(".", 1)[0] if "." in code else None
            ref = f"{ctx.slug}-sec-{code}"
            cit = _printed_citation(page_from)
            ex.nodes.append(Node(
                node_kind="chapter" if level == 1 else "section",
                title=title, code=code, ordinal=i,
                page_from=cit.page_index if cit else None,
                page_to=_printed_citation(page_to).page_index if _printed_citation(page_to) else None,
                parent_ref=node_ref_by_code.get(parent_code, root_ref),
                ref=ref,
            ))
            node_ref_by_code[code] = ref
        return node_ref_by_code

    # -- the None/Minimal/Significant/Transformational rating ladder -------

    def _extract_ladder(self, ctx: DocumentContext, doc: pymupdf.Document, ex: Extraction,
                         node_ref_by_code: dict[str, str], content_status: str) -> int:
        ex.rating_scales.append({
            "slug": RATING_SCALE_SLUG,
            "name": "Sustainability principle contribution ladder",
        })
        for ordinal, level_name in enumerate(LADDER_LEVELS):
            ex.rating_levels.append({
                "scale_slug": RATING_SCALE_SLUG, "ordinal": ordinal,
                "code": level_name[:4].upper(), "name": level_name,
                "description": None, "colour": None,
            })

        seen: dict[str, list[Citation]] = {}
        for page_index, page in enumerate(doc):
            text = page.get_text()
            for m in _LADDER_RE.finditer(text):
                principle = _clean(m.group(1)).strip("'‘’")
                cit = _citation(page_index)
                seen.setdefault(principle, []).append(cit)

        node_ref = node_ref_by_code.get("3.4") or node_ref_by_code.get("2.3")
        n = 0
        for principle, citations in seen.items():
            ex.items.append(Item(
                item_type="requirement",
                title=f"Contribution ladder: {principle} sustainability principle",
                statement=(
                    f"Contribution to objectives defined under the {principle} "
                    f"sustainability principle - {', '.join(LADDER_LEVELS)}"
                ),
                node_ref=node_ref,
                content_status=content_status,
                confidence=0.85,
                citations=citations[:2],
                payload={
                    "requirement_kind": "graded",
                    "criterion_id": None, "rating_level_id": None, "metric_id": None,
                    "target_value": None, "target_text": ", ".join(LADDER_LEVELS),
                    "unit_id": None, "comparator": "none",
                    "is_deliverable": False, "deliverable_name": None, "parsed_ok": False,
                },
            ))
            n += 1
        if not seen:
            ex.warnings.append(
                f"{ctx.slug}: expected 'None, Minimal, Significant, Transformational' ladder text not found"
            )
        return n

    # -- solution prioritisation rubric -------------------------------------

    def _extract_rubric(self, ctx: DocumentContext, doc: pymupdf.Document, ex: Extraction,
                         node_ref_by_code: dict[str, str], content_status: str) -> tuple[int, int]:
        page_b = next((i for i, p in enumerate(doc) if "CAPEX to buy" in p.get_text()), None)
        if page_b is None:
            ex.warnings.append(f"{ctx.slug}: solution-prioritisation rubric (Table 3.4) not found")
            return 0, 0
        page_a = page_b - 1 if page_b > 0 and "Weighting" in doc[page_b - 1].get_text() else None
        pages = [p for p in (page_a, page_b) if p is not None]

        raw = " ".join(doc[p].get_text() for p in pages)
        text = re.sub(r"[ \t]*\n[ \t]*", " ", raw)
        text = re.sub(r" {2,}", " ", text)

        node_ref = node_ref_by_code.get("3.3")
        citations = [_citation(p) for p in pages]

        n_axes = 0
        for var_id, anchor, name in _RUBRIC_AXES:
            # a name can appear more than once (e.g. as both a plain,
            # unweighted principle description and its own scored axis
            # elsewhere) -- take the first occurrence whose *following*
            # text actually parses as a scoring anchor, not just the first
            # occurrence of the name.
            weight_m = anchor_m = None
            any_match = False
            for m in re.finditer(anchor, text):
                any_match = True
                window = text[m.end():m.end() + 220]
                weight_m = _WEIGHT_RE.search(window)
                anchor_m = _ANCHOR_RE.match(window)
                if weight_m and anchor_m:
                    break
            if not weight_m or not anchor_m:
                reason = "could not parse its scoring anchors/weighting" if any_match else "not found in Table 3.4 text"
                ex.warnings.append(f"{ctx.slug}: rubric axis {name!r} {reason}")
                continue
            lo_label, lo_score, hi_label, hi_score = anchor_m.groups()
            weight = weight_m.group(0)
            ex.design_variables.append({"id": var_id, "name": name, "ordinal": n_axes})
            ex.design_variable_values.append({
                "id": f"{var_id}.score_{lo_score}", "variable_id": var_id,
                "label": f"{_clean(lo_label)} = {lo_score} (weighting {weight})",
                "ordinal": int(lo_score),
            })
            ex.design_variable_values.append({
                "id": f"{var_id}.score_{hi_score}", "variable_id": var_id,
                "label": f"{_clean(hi_label)} = {hi_score} (weighting {weight})",
                "ordinal": int(hi_score),
            })
            n_axes += 1

        # design_variable/design_variable_value carry no field for weighting or
        # free-text description (db/schema.sql design_variable is id/name/
        # document_id/ordinal only) -- weighting is folded into the value
        # label above so it is not lost, but it is not queryable as a number.
        # The full rubric, including the ~20 N/A-weighted metadata rows this
        # loop does not touch (Domain, Key Stakeholders, Responsibilities...),
        # is kept verbatim here instead. See the report back to the task
        # owner for this as a writer/schema gap.
        ex.items.append(Item(
            item_type="guidance",
            title="Table 3.4: solution matrix categorisation and rationale",
            statement=raw[:500],
            payload={"body_md": raw, "figure_ids": [], "legend_tokens": ["Table 3.4"], "disclaimer": None},
            node_ref=node_ref,
            content_status=content_status,
            citations=citations,
        ))
        ex.warnings.append(
            f"{ctx.slug}: the scored solutions matrix itself (which digital solution scored what) is an "
            "external workbook the report only references by filename -- not embedded in this PDF, so no "
            "per-solution design_variable_value assignment was extracted, only the scoring rubric/legend."
        )
        return n_axes, 1

    # -- personas ------------------------------------------------------------

    def _extract_personas(self, ctx: DocumentContext, doc: pymupdf.Document, ex: Extraction,
                           node_ref_by_code: dict[str, str], content_status: str) -> int:
        page_index = next((i for i, p in enumerate(doc) if "Persona Group" in p.get_text()), None)
        if page_index is None:
            ex.warnings.append(f"{ctx.slug}: persona table (Figure 3.2) not found")
            return 0
        page = doc[page_index]
        words = page.get_text("words")

        header_x: dict[str, float] = {}
        for x0, y0, _x1, _y1, text, *_ in words:
            if 190 < y0 < 220:
                if text == "Persona":
                    header_x["persona"] = x0
                elif text == "Key":
                    header_x["assets"] = x0
                elif text == "Technology":
                    header_x["tech"] = x0
        if len(header_x) != 3:
            ex.warnings.append(f"{ctx.slug}: persona table header columns not fully recognised")
            return 0

        caption = next((w for w in words if w[4].startswith("Figure")), None)
        bottom_y = caption[1] - 5 if caption else page.rect.height
        left_x = header_x["persona"] - 30

        region = [w for w in words if 220 < w[1] < bottom_y and w[0] > left_x]
        anchors = list(header_x.items())

        def col_of(x0: float) -> str:
            return min(anchors, key=lambda c: abs(c[1] - x0))[0]

        by_col: dict[str, list] = {"persona": [], "assets": [], "tech": []}
        for w in region:
            by_col[col_of(w[0])].append(w)

        def row_groups(colwords: list, gap: float = 25.0) -> list[list]:
            if not colwords:
                return []
            ws = sorted(colwords, key=lambda w: w[1])
            groups, cur = [], [ws[0]]
            for w in ws[1:]:
                if w[1] - cur[-1][1] > gap:
                    groups.append(cur)
                    cur = [w]
                else:
                    cur.append(w)
            groups.append(cur)
            return groups

        persona_rows = row_groups(by_col["persona"])
        asset_rows = row_groups(by_col["assets"])
        tech_rows = row_groups(by_col["tech"])

        def text_of(group: list) -> str:
            return _clean(" ".join(w[4] for w in sorted(group, key=lambda w: (round(w[1]), w[0]))))

        def nearest(groups: list[list], y: float) -> str:
            if not groups:
                return ""
            best = min(groups, key=lambda g: abs(g[0][1] - y))
            return text_of(best) if abs(best[0][1] - y) < 30 else ""

        node_ref = node_ref_by_code.get("3.2")
        citation = [_citation(page_index)]
        n = 0
        for g in persona_rows:
            name = text_of(g)
            if not name:
                continue
            y = g[0][1]
            assets = nearest(asset_rows, y)
            tech = nearest(tech_rows, y)
            ex.items.append(Item(
                item_type="pattern",
                title=f"Persona: {name}",
                summary=f"Persona group: key assets/districts = {assets or 'n/a'}; technology needs = {tech or 'n/a'}",
                payload={
                    "pattern_kind": "persona", "code": None, "name": name,
                    "parent_pattern_id": None,
                    "attributes": {"key_assets_or_districts": assets, "technology_needs": tech},
                },
                node_ref=node_ref,
                content_status=content_status,
                confidence=0.75,
                citations=citation,
            ))
            n += 1
        if n == 0:
            ex.warnings.append(f"{ctx.slug}: persona table located but no rows resolved")
        return n

    # -- "Refer to <external document>" mentions -----------------------------

    def _extract_references(self, doc: pymupdf.Document, ex: Extraction, root_ref: str) -> int:
        seen: set[str] = set()
        n = 0
        for page in doc:
            text = re.sub(r"[ \t]*\n[ \t]*", " ", page.get_text())
            for m in _REFERENCE_RE.finditer(text):
                raw = m.group(1).rstrip(".")
                if raw in seen:
                    continue
                seen.add(raw)
                ex.references.append(Reference(raw_text=raw, ref_kind="external_document", from_node_ref=root_ref))
                n += 1
        return n


SMART_CITY = SmartCityExtractor()
register(SMART_CITY)
