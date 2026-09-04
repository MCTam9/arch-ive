"""Extractor for the masterplan framework volumes.
doc_kinds = ("implementation_plan", "framework")

The highest-value artefact in the corpus is a clean appendix table with
exactly three columns that matter: strategy reference code (e.g. 'NF1.1',
'RE4.1'), compliance requirement text, and target (a number, or the literal
'Y' meaning a named deliverable document is required). It is produced by a
different tool than the body of the document and rendered as a real ruled
table, so `page.find_tables()` recovers it directly; the appendix itself
spans a couple of PDF pages and is reassembled by concatenating rows across
them (each page's rows overwrite nothing -- there is no repeated header to
dedupe against).

The rest of the volume is spread-paginated (1 PDF page = 2 printed pages)
prose with per-strategy 'KEY PERFORMANCE INDICATOR' / 'MINIMUM TARGET' /
'COMPLIANCE REQUIREMENT' call-outs, produced by a different tool with no
ruled tables. Positional reconstruction there is unreliable, so this module
is deliberately conservative: it only emits a KPI/target pair when a header
label and its value sit close enough, in the same column, to be confident,
and everything it does emit gets a lower confidence than the appendix rows.
See CONTRACT.md: "extract fewer with high confidence rather than many with
low."

Ref convention: see extractors/crib_sheet.py's module docstring -- the same
`ref`/`parent_ref`/`*_ref` convention is used here for framework, criterion
and the requirement payload's criterion_id.
"""
from __future__ import annotations

import re

import pymupdf

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, register

FRAMEWORK_SLUG = "masterplan-sustainability"

CODE_RE = re.compile(r"^([A-Z]{2,3})(\d)\.(\d)$")
PRINCIPLE_RE = re.compile(r"^([A-Z ,&/-]+?)\s*\[([A-Z]{2,3})\]\s*$")
SECTION_RE = re.compile(r"^([A-Z]{2,3}\d)\s+(.+)$")

NUM = r"-?\d+(?:\.\d+)?"


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def _unit_from_requirement(text: str) -> tuple[str, str] | None:
    m = re.search(r"\((%|m|km|m2|m²|dB|dBA)\)", text)
    if not m:
        return None
    raw = m.group(1)
    mapping = {"%": ("pct", "%"), "m": ("m", "m"), "km": ("km", "km"),
               "m2": ("m2", "m2"), "m²": ("m2", "m2"), "dB": ("db", "dB"),
               "dBA": ("db", "dBA")}
    return mapping.get(raw)


def _comparator_from_requirement(text: str) -> str:
    low = text.lower()
    if low.startswith("minimum") or " minimum " in low:
        return "gte"
    if low.startswith("maximum") or " maximum " in low:
        return "lte"
    return "none"


def _parse_target(target_text: str) -> tuple[float | None, bool]:
    """A single bare number parses; anything compound ('800 (primary...) 400
    (secondary...)') is kept verbatim only -- see module docstring."""
    t = target_text.strip()
    if re.fullmatch(NUM, t):
        return float(t), True
    return None, False


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return s or "x"


class ComplianceTableExtractor:
    doc_kinds = ("implementation_plan", "framework")

    def extract(self, ctx: DocumentContext) -> Extraction:
        ext = Extraction()
        doc = pymupdf.open(ctx.path)
        try:
            self._extract(ctx, doc, ext)
        finally:
            doc.close()
        return ext

    # -- top-level structure ------------------------------------------------

    def _extract(self, ctx: DocumentContext, doc: pymupdf.Document, ext: Extraction) -> None:
        root_ref = f"{ctx.slug}-root"
        ext.nodes.append(Node(node_kind="volume", title=ctx.slug, ordinal=0,
                               page_from=1, page_to=len(doc), ref=root_ref))

        ext.frameworks.append({
            "ref": FRAMEWORK_SLUG, "slug": FRAMEWORK_SLUG,
            "name": "Masterplan sustainability framework", "owner_org_id": None,
            "version": None, "rating_scale_ref": None, "document_ref": None,
        })

        principles: dict[str, str] = {}   # 'NF' -> full principle name
        sections: dict[str, str] = {}     # 'NF1' -> section name
        criteria_refs: set[str] = set()   # codes already emitted, across pages
        # the canonical strategy/target appendix is reproduced once per
        # contractor-role scope-of-work section (Concept Design, Design-
        # Build, Main Contractor, ...) plus once more as a fill-in-the-blank
        # checklist. Same codes, same targets, several times over -- keep
        # the first sighting of each code and flag it if a later copy
        # disagrees, rather than emitting the same requirement 4-5x.
        requirement_seen: dict[str, str] = {}

        appendix_pages = self._find_appendix_pages(doc)
        if not appendix_pages:
            ext.warnings.append(
                f"{ctx.slug}: no compliance-requirement appendix table found "
                f"(expected a ruled table with a 'Strategy Reference Code' "
                f"header); this document may not carry that appendix"
            )
        n_requirements = 0
        for page_index in appendix_pages:
            n_requirements += self._extract_appendix_page(
                ctx, doc, page_index, root_ref, principles, sections,
                criteria_refs, requirement_seen, ext
            )
        ext.stats[f"{ctx.slug}_appendix_requirements"] = n_requirements
        ext.stats[f"{ctx.slug}_appendix_pages"] = len(appendix_pages)
        ext.stats[f"{ctx.slug}_appendix_unique_codes"] = len(requirement_seen)

        n_kpi = self._extract_strategy_sheets(ctx, doc, root_ref, criteria_refs, ext)
        ext.stats[f"{ctx.slug}_strategy_sheet_kpis"] = n_kpi

    # -- the 3-column compliance appendix ------------------------------------

    @staticmethod
    def _find_appendix_pages(doc: pymupdf.Document) -> list[int]:
        """Pages whose ruled tables have a strategy-code-shaped first column
        across several rows -- the signature of the appendix, independent of
        which PDF page(s) it happens to land on."""
        hits = []
        for i, page in enumerate(doc):
            try:
                tabs = page.find_tables()
            except Exception:
                continue
            for t in tabs.tables:
                try:
                    rows = t.extract()
                except Exception:
                    continue
                code_rows = sum(1 for r in rows if r and r[0] and CODE_RE.match(_clean(r[0])))
                if code_rows >= 3:
                    hits.append(i)
                    break
        return hits

    def _extract_appendix_page(self, ctx: DocumentContext, doc: pymupdf.Document,
                                page_index: int, root_ref: str,
                                principles: dict[str, str], sections: dict[str, str],
                                criteria_refs: set[str], requirement_seen: dict[str, str],
                                ext: Extraction) -> int:
        page = doc[page_index]
        tabs = page.find_tables()
        n = 0
        for t in tabs.tables:
            rows = t.extract()
            current_principle_code: str | None = None
            for row in rows:
                if not row:
                    continue
                col0 = _clean(row[0]) if row[0] else ""
                # find the first non-empty text cell after col0 -- header
                # rows (principle/section banners) and code rows both use it,
                # just merged into different physical columns depending on
                # which half of the appendix produced them.
                rest = [c for c in row[1:] if c and _clean(c)]
                label = _clean(rest[0]) if rest else ""

                if not col0 and label:
                    pm = PRINCIPLE_RE.match(label)
                    if pm:
                        current_principle_code = pm.group(2)
                        principles[pm.group(2)] = _clean(pm.group(1))
                        self._ensure_criterion(ext, criteria_refs, code=pm.group(2),
                                                 parent_code=None, title=principles[pm.group(2)])
                        continue
                    sm = SECTION_RE.match(label)
                    if sm and (current_principle_code is None
                               or sm.group(1).startswith(current_principle_code)):
                        theme = re.match(r"[A-Z]{2,3}", sm.group(1)).group()
                        sections[sm.group(1)] = _clean(sm.group(2))
                        self._ensure_criterion(ext, criteria_refs, code=theme,
                                                 parent_code=None, title=principles.get(theme, theme))
                        self._ensure_criterion(ext, criteria_refs, code=sm.group(1),
                                                 parent_code=theme, title=sections[sm.group(1)])
                        continue
                    continue

                m = CODE_RE.match(col0)
                if not m:
                    continue
                theme, section_no, _leaf_no = m.groups()
                section_code = f"{theme}{section_no}"
                requirement_text = label
                # remaining non-empty cells after the requirement text: the
                # target/KPI value is the next one that looks like a bare
                # token (short, no sentence punctuation).
                target_text = None
                for c in rest[1:]:
                    ct = _clean(c)
                    if ct and len(ct) < 80:
                        target_text = ct
                        break
                if not requirement_text or target_text is None:
                    continue

                self._ensure_criterion(ext, criteria_refs, code=theme, parent_code=None,
                                         title=principles.get(theme, theme))
                self._ensure_criterion(ext, criteria_refs, code=section_code, parent_code=theme,
                                         title=sections.get(section_code, section_code))
                self._ensure_criterion(ext, criteria_refs, code=col0, parent_code=section_code,
                                         title=requirement_text[:120])

                if col0 in requirement_seen:
                    if requirement_seen[col0] != target_text:
                        ext.warnings.append(
                            f"{ctx.slug}: {col0} target disagrees across repeated "
                            f"appendix copies ({requirement_seen[col0]!r} vs "
                            f"{target_text!r}); kept the first"
                        )
                    continue
                requirement_seen[col0] = target_text

                target_value, parsed_ok = _parse_target(target_text)
                is_deliverable = target_text.strip().upper() == "Y"
                unit = _unit_from_requirement(requirement_text)
                comparator = _comparator_from_requirement(requirement_text) if parsed_ok else "none"
                deliverable_name = requirement_text.split(" developed ")[0].strip() \
                    if is_deliverable else None

                ext.items.append(Item(
                    item_type="requirement",
                    statement=requirement_text,
                    node_ref=root_ref,
                    content_status=ctx.meta.get("content_status", "real"),
                    confidence=0.9,
                    citations=[Citation(page_index=page_index + 1)],
                    payload={
                        "requirement_kind": "compliance",
                        "criterion_id": f"crit-{col0}",
                        "rating_level_id": None, "metric_id": None,
                        "target_value": target_value, "target_text": target_text,
                        "unit_id": unit[0] if unit else None,
                        "comparator": comparator,
                        "is_deliverable": is_deliverable,
                        "deliverable_name": deliverable_name,
                        "parsed_ok": parsed_ok,
                    },
                ))
                if unit and not any(u["id"] == unit[0] for u in ext.units):
                    ext.units.append({"id": unit[0], "symbol": unit[1],
                                       "dimension": None, "si_factor": None})
                n += 1
        return n

    @staticmethod
    def _ensure_criterion(ext: Extraction, seen: set[str], *, code: str,
                            parent_code: str | None, title: str) -> None:
        if code in seen:
            # the principle/section banner isn't always the row that
            # introduces a code (which repeated appendix copy comes first
            # varies) -- upgrade a bare code-as-title placeholder if a
            # later sighting brings the real heading.
            if title and title != code:
                existing = next((c for c in ext.criteria if c["ref"] == f"crit-{code}"), None)
                if existing and existing["title_primary"] == code:
                    existing["title_primary"] = title
            return
        seen.add(code)
        ext.criteria.append({
            "ref": f"crit-{code}", "framework_ref": FRAMEWORK_SLUG,
            "parent_ref": f"crit-{parent_code}" if parent_code else None,
            "code": code, "title_primary": title, "title_alt": None,
            "ordinal": 0,
        })

    # -- best-effort KPI / target pairs from the narrative strategy sheets ---

    def _extract_strategy_sheets(self, ctx: DocumentContext, doc: pymupdf.Document,
                                   root_ref: str, criteria_refs: set[str],
                                   ext: Extraction) -> int:
        n = 0
        pages_with_labels = 0
        for page_index, page in enumerate(doc):
            text = page.get_text("text")
            if "KEY PERFORMANCE INDICATOR" not in text and "MINIMUM TARGET" not in text:
                continue
            pages_with_labels += 1
            code_m = re.search(r"\b([A-Z]{2,3}\d\.\d)\b", text[:400])
            code = code_m.group(1) if code_m else None

            blocks = []
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                t = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
                if t:
                    blocks.append((b["bbox"], t))

            for label, kind in (("KEY PERFORMANCE INDICATOR", "kpi"),
                                 ("MINIMUM TARGET", "target")):
                header = next((bb for bb, t in blocks if label in t.upper()), None)
                if header is None:
                    continue
                hx0, hy0, hx1, hy1 = header
                # nearest block below the header, same column, short enough
                # to be a value rather than a paragraph
                candidates = [
                    (bb, t) for bb, t in blocks
                    if bb[1] > hy1 and bb[1] - hy1 < 120
                    and bb[0] >= hx0 - 20 and bb[0] <= hx1 + 40
                    and label not in t.upper() and len(t) < 200
                ]
                if not candidates:
                    continue
                value_bbox, value_text = min(candidates, key=lambda c: c[0][1] - hy1)
                value_text = _clean(value_text)
                if not value_text or not code:
                    continue
                section_code = re.match(r"[A-Z]{2,3}\d", code).group()
                theme = re.match(r"[A-Z]{2,3}", code).group()
                self._ensure_criterion(ext, criteria_refs, code=theme, parent_code=None, title=theme)
                self._ensure_criterion(ext, criteria_refs, code=section_code, parent_code=theme,
                                         title=section_code)
                self._ensure_criterion(ext, criteria_refs, code=code, parent_code=section_code,
                                         title=code)
                target_value, parsed_ok = _parse_target(value_text) if kind == "target" else (None, False)
                ext.items.append(Item(
                    item_type="requirement",
                    statement=value_text,
                    title=f"{code} {label.title()}",
                    node_ref=root_ref,
                    content_status=ctx.meta.get("content_status", "real"),
                    confidence=0.5,
                    citations=[Citation(page_index=page_index + 1)],
                    payload={
                        "requirement_kind": "graded" if kind == "kpi" else "compliance",
                        "criterion_id": f"crit-{code}", "rating_level_id": None,
                        "metric_id": None, "target_value": target_value,
                        "target_text": value_text, "unit_id": None,
                        "comparator": "none", "is_deliverable": False,
                        "deliverable_name": None, "parsed_ok": parsed_ok,
                    },
                ))
                n += 1
        if pages_with_labels and n == 0:
            ext.warnings.append(
                f"{ctx.slug}: {pages_with_labels} strategy-sheet page(s) carried "
                f"KPI/target headers but none resolved to a confident value -- "
                f"skipped rather than guessed (see module docstring)"
            )
        elif pages_with_labels:
            ext.warnings.append(
                f"{ctx.slug}: strategy-sheet KPI/target extraction is best-effort "
                f"positional reconstruction ({n} pairs from {pages_with_labels} "
                f"candidate pages); confidence set to 0.5, review before trusting"
            )
        return n


COMPLIANCE_TABLE = ComplianceTableExtractor()
register(COMPLIANCE_TABLE)
