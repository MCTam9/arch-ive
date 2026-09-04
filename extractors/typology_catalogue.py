"""Extractor for the typology-catalogue doc_kind (doc_kind='guideline_report').

Targets typology-multifamily: a 413-page catalogue with a 261-entry bookmark
TOC and a decimal section-code system (e.g. '5.1.2') repeated in the page
furniture. Figures are coded 'FIGURE 5.1.2.1'.

Roughly a third of the document is not real content: a contiguous run of
lorem-ipsum / 'TEMPLATE ONLY' pages (engineering appendices, code review,
outline specs) and 37 pages stamped 'WIP' (almost the entire prototype
catalogue, pages ~109-140). `DocumentContext.pages[i]['content_status']` is
set by another module before this extractor ever runs -- this module never
infers it, it only *obeys* it: nothing is emitted whose citation would land
on a non-'real' page. That is stricter than merely flagging placeholder
content, but it is what CONTRACT.md's verification step asks for, and it is
the only safe default for something described as architectural guidance.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, register

DOC_KINDS = ("guideline_report",)

_FIGURE_RE = re.compile(r"FIGURE\s+[\d.]+", re.I)
_CODE_LINE_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,3}$")
_DISCLAIMER_PATTERNS = (
    "All Designs must comply with applicable Local Building Codes and Fire Regulations.",
    "Drawings are indicative only and not to scale.",
)
_FURNITURE_LINE_RES = (
    re.compile(r"^[A-Z]{2,6}\s+Multifamily Housing\s*$", re.I),  # running header (client abbrev + title)
    re.compile(r"^/?\s*\d+\s*$"),                      # bare/prefixed page numbers
    re.compile(r"^\d{2}\s*$"),
    re.compile(r"^[A-Z](\s[A-Z0-9+])+\s*$"),            # spaced-out running titles
    re.compile(r"^\*+\s*(All Designs|Drawings)", re.I),  # disclaimer, pulled out separately
)

_PRIMARY_TYPES = (
    ("STH", "Stacked Townhouse"),
    ("SL", "Single Loaded"),
    ("DL", "Double Loaded"),
    ("PC", "Point Core"),
)

# The document's own declared 7-facet prototype vocabulary (its design-
# variables page names the facets; this is the value list it uses across the
# prototype pages -- see CONTRACT/task brief, verified against the real PDF's
# facet header row on the Prototypes methodology page).
_DESIGN_VARIABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("access_typology", "ACCESS TYPOLOGY",
     ("Private Door Entrance", "Single Loaded", "Double Loaded", "Point Core")),
    ("unit_typology", "UNIT TYPOLOGY",
     ("Simplex", "Duplex", "Stepped Duplex", "Triplex")),
    ("development_height", "DEVELOPMENT HEIGHT",
     ("Low Rise", "Mid Rise", "High Rise")),
    ("parking_configuration", "PARKING CONFIGURATION",
     ("At-Grade", "Podium", "Half Basement", "Basement")),
    ("private_external_amenity", "PRIVATE EXTERNAL AMENITY",
     ("External Balconies", "Garden", "Roof Terrace", "Enclosed Terraces")),
    ("ground_floor_condition", "GROUND FLOOR CONDITION",
     ("Retail", "Garden Simplex", "Garden Duplex", "Boundary Wall")),
    ("entry_condition", "ENTRY CONDITION",
     ("Street Entry", "Basement Entry")),
)

_ACCESS_BY_PREFIX = {"TH": "private_door_entrance", "SL": "single_loaded", "DL": "double_loaded", "PC": "point_core"}

_URBAN_CLUSTERS = (
    ("PCS", "Pavilion Cluster - Square", "pavilion"),
    ("PCRE", "Pavilion Cluster - Regular", "pavilion"),
    ("PCRA", "Pavilion Cluster - Random", "pavilion"),
    ("LCPA", "Linear Cluster - Parallel", "linear"),
    ("LCPE", "Linear Cluster - Perpendicular", "linear"),
    ("LCB", "Linear Cluster - Border", "linear"),
    ("CCC", "Courtyard Cluster - Closed", "courtyard"),
    ("CCO", "Courtyard Cluster - Open", "courtyard"),
    ("CCU", "Courtyard Cluster - U-Shaped", "courtyard"),
)

_DEFINITION_CATEGORY_MARKERS = (
    ("TECHNICAL + ADMIN TERMS", "TECHNICAL + ADMINISTRATIVE TERMS"),
    ("EXPLANATORY TERMS", "EXPLANATORY TERMS"),
    ("LAND USE TERMS", "LAND USE TERMS"),
)
_TERM_RE = re.compile(
    r"(?P<term>[A-Z][A-Z0-9 /\-\+\.\(\)'’]{1,70}?):\s+"
    r"(?P<def>.*?)(?=(?:\n[A-Z][A-Z0-9 /\-\+\.\(\)'’]{1,70}?:\s)|\Z)",
    re.S,
)

# (metric_id, label, regex, unit_id, value_template)
_BENCHMARK_PATTERNS = (
    ("far_by_storeys", "F.A.R. band",
     re.compile(r"(\d+(?:-\d+)?)\s*STOREYS?\s*APPROX\s*(\d+):(\d+)\s*F\.A\.R\.", re.I),
     "ratio"),
    ("plot_coverage_max", "Maximum plot coverage",
     re.compile(r"plot coverage is considered at a maximum of\s*(\d+)\s*%", re.I),
     "pct"),
    ("floor_to_floor_height_min", "Minimum floor-to-floor height (ground level)",
     re.compile(r"floor to floor height in this case should be a minimum of\s*(\d+(?:\.\d+)?)\s*m\b", re.I),
     "m"),
    ("ceiling_height_min", "Minimum ceiling height",
     re.compile(r"minimum ceiling heights? of\s*(\d+(?:\.\d+)?)\s*m\b", re.I),
     "m"),
    ("ceiling_height_min", "Minimum ceiling height (non-habitable)",
     re.compile(r"ceiling heights? of minimum\s*(\d+(?:\.\d+)?)\s*m\b", re.I),
     "m"),
    ("balustrade_height", "Balustrade height",
     re.compile(r"balustrade height\s*\(\s*(\d+)\s*-\s*(\d+)\s*mm\s*\)", re.I),
     "mm"),
    ("window_glazed_area_min", "Minimum net glazed area",
     re.compile(r"net glazed area shall be not less than\s*(\d+)\s*percent", re.I),
     "pct"),
    ("window_openable_min", "Minimum openable window area",
     re.compile(r"movable part of the window shall not be less than\s*(\d+)\s*percent", re.I),
     "pct"),
)


def _clean_body(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _CODE_LINE_RE.match(s):
            continue
        if any(p.match(s) for p in _FURNITURE_LINE_RES):
            continue
        lines.append(s)
    return " ".join(lines)


def _section_code(text: str) -> str | None:
    candidates = [ln.strip() for ln in text.splitlines() if _CODE_LINE_RE.match(ln.strip())]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.count("."))


def _figure_codes(text: str) -> list[str]:
    seen: list[str] = []
    for m in _FIGURE_RE.findall(text):
        code = m.upper()
        if code not in seen:
            seen.append(code)
    return seen


def _disclaimer(text: str) -> str | None:
    found = [p for p in _DISCLAIMER_PATTERNS if p.lower() in text.lower()]
    return " ".join(found) if found else None


class TypologyCatalogueExtractor:
    doc_kinds: tuple[str, ...] = DOC_KINDS

    def extract(self, ctx: DocumentContext) -> Extraction:
        ex = Extraction()
        doc = pymupdf.open(ctx.path)
        page_map = {p["page_index"]: p for p in ctx.pages}

        def text_of(idx: int) -> str:
            row = page_map.get(idx)
            if row and row.get("text"):
                return row["text"]
            if 1 <= idx <= doc.page_count:
                return doc[idx - 1].get_text()
            return ""

        def is_real(idx: int) -> bool:
            row = page_map.get(idx)
            return bool(row) and row.get("content_status", "real") == "real"

        def real_range(lo: int, hi: int) -> list[int]:
            return [i for i in range(lo, hi + 1) if is_real(i)]

        toc_nodes, path_ref, ref_range = self._build_toc(ex, doc, text_of)

        for var_id, var_name, values in _DESIGN_VARIABLES:
            ex.design_variables.append({"id": var_id, "name": var_name, "ordinal": 0})
            for i, v in enumerate(values, start=1):
                ex.design_variable_values.append({
                    "id": f"{var_id}.{_slug(v)}", "variable_id": var_id, "label": v, "ordinal": i,
                })

        self._definitions(ex, path_ref, ref_range, text_of, real_range)
        self._typologies(ex, path_ref, ref_range, text_of, real_range)
        self._prototypes(ex, path_ref, ref_range, text_of, real_range)
        self._urban_clusters(ex, path_ref, ref_range, text_of, real_range)
        self._unit_types(ex, path_ref, ref_range, text_of, real_range)
        self._guidance(ex, ref_range, text_of, real_range)
        self._benchmarks(ex, page_map, text_of)

        doc.close()
        ex.stats = self._counts(ex)
        return ex

    # ── structure: doc_node tree from the bookmark TOC ────────────────────

    def _build_toc(self, ex: Extraction, doc, text_of):
        """Returns (nodes, path_ref, ref_range):
        - path_ref: (title1, title2, ...) breadcrumb -> Node.ref, for the
          handful of subsections other passes need to point node_ref at.
        - ref_range: Node.ref -> (page_from, page_to).
        """
        toc = doc.get_toc()
        kind_by_level = {1: "chapter", 2: "section", 3: "subsection"}
        path_ref: dict[tuple[str, ...], str] = {}
        ref_range: dict[str, tuple[int, int]] = {}
        stack: list[tuple[int, str, str]] = []  # (level, ref, title)

        for i, (level, title, page) in enumerate(toc):
            title = " ".join(title.split())
            # page_to must span every descendant, not just stop at the next
            # TOC row -- a non-leaf entry's very next row is usually its own
            # first child, starting on the same page.
            next_page = doc.page_count + 1
            for j in range(i + 1, len(toc)):
                if toc[j][0] <= level:
                    next_page = toc[j][2]
                    break
            page_to = max(page, next_page - 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_ref = stack[-1][1] if stack else None
            ref = f"toc-{i}"
            code = _section_code(text_of(page))
            ex.nodes.append(Node(
                node_kind=kind_by_level.get(level, "subsection"), title=title, code=code,
                ordinal=i, page_from=page, page_to=page_to, parent_ref=parent_ref, ref=ref,
            ))
            path = tuple(t for _l, _r, t in stack) + (title,)
            path_ref[path] = ref
            ref_range[ref] = (page, page_to)
            stack.append((level, ref, title))
        return ex.nodes, path_ref, ref_range

    @staticmethod
    def _find_ref(path_ref: dict, *breadcrumb: str) -> str | None:
        needle = tuple(b.lower() for b in breadcrumb)
        for path, ref in path_ref.items():
            low = tuple(p.lower() for p in path)
            if len(low) >= len(needle) and low[-len(needle):] == needle:
                return ref
        return None

    # ── glossary (definition) ──────────────────────────────────────────

    def _definitions(self, ex: Extraction, path_ref, ref_range, text_of, real_range) -> None:
        ref = self._find_ref(path_ref, "Definitions + Terms")
        if ref is None:
            ex.warnings.append("could not locate 'Definitions + Terms' in the TOC")
            return
        lo, hi = ref_range[ref]
        found_any = False
        for idx in real_range(lo, hi):
            text = text_of(idx)
            category = None
            for marker, canonical in _DEFINITION_CATEGORY_MARKERS:
                if marker in text:
                    category = canonical
                    break
            if category is None:
                continue  # e.g. the abbreviations pages, which are not term:definition prose
            found_any = True
            body = _clean_body_keep_colons(text)
            for m in _TERM_RE.finditer(body):
                term = m.group("term").strip()
                definition = " ".join(m.group("def").split()).strip(" .") + "."
                if len(term) < 2 or len(definition) < 4:
                    continue
                ex.items.append(Item(
                    item_type="definition",
                    title=term,
                    payload={"term": term, "definition": definition, "category": category},
                    node_ref=ref,
                    citations=[Citation(page_index=idx)],
                ))
        if not found_any:
            ex.warnings.append("Definitions + Terms range had no real page with a recognised category header")

    # ── typology (primary + secondary variants) ──────────────────────────

    def _typologies(self, ex: Extraction, path_ref, ref_range, text_of, real_range) -> None:
        primary_ref: dict[str, str] = {}
        for code, name in _PRIMARY_TYPES:
            ref = self._find_ref(path_ref, "Primary Typologies", name)
            if ref is None:
                ex.warnings.append(f"typology primary type {name!r} not found in TOC")
                continue
            lo, hi = ref_range[ref]
            pages = real_range(lo, hi)
            if not pages:
                ex.warnings.append(f"typology primary type {name!r}: no real page in range {lo}-{hi}")
                continue
            text = " ".join(text_of(p) for p in pages)
            cap = re.search(r"[Uu]p to G\s*\+\s*(\d+)", text)
            item_ref = f"typology-{code}"
            ex.items.append(Item(
                item_type="pattern",
                title=f"{name} ({code})",
                summary=f"Primary multifamily typology: {name}, {'up to G+' + cap.group(1) if cap else 'height cap not found'}.",
                payload={
                    "pattern_kind": "typology",
                    "code": code,
                    "name": name,
                    "parent_pattern_id": None,
                    "attributes": {"height_cap": f"G+{cap.group(1)}" if cap else None},
                },
                node_ref=ref,
                citations=[Citation(page_index=p) for p in pages],
                ref=item_ref,
            ))
            primary_ref[code] = item_ref

        variants_ref = self._find_ref(path_ref, "Multifamily Typologies", "Methodology", "Design Variables")
        if variants_ref is None:
            ex.warnings.append("typology variant legend page ('Design Variables' under Methodology) not found in TOC")
            return
        lo, hi = ref_range[variants_ref]
        pages = real_range(lo, hi)
        if not pages:
            ex.warnings.append(f"typology variants: no real page in range {lo}-{hi}")
            return
        text = " ".join(text_of(p) for p in pages)
        seen: list[tuple[str, str]] = []
        for fam, letter in re.findall(r"\b(STH|SL|DL|PC)\.([A-J])\b", text):
            if (fam, letter) not in seen:
                seen.append((fam, letter))
        family_name = dict(_PRIMARY_TYPES)
        for fam, letter in seen:
            parent = primary_ref.get(fam)
            ex.items.append(Item(
                item_type="pattern",
                title=f"{family_name.get(fam, fam)} {fam}.{letter}",
                payload={
                    "pattern_kind": "typology",
                    "code": f"{fam}.{letter}",
                    "name": f"{family_name.get(fam, fam)} variant {letter}",
                    "parent_pattern_id": parent,
                    "attributes": {"family": fam, "variant": letter},
                },
                node_ref=variants_ref,
                citations=[Citation(page_index=p) for p in pages],
                ref=f"typology-{fam}-{letter}",
            ))

    # ── prototypes (7-facet mapping) ──────────────────────────────────────

    _PROTOTYPE_FAMILY_RE = re.compile(r"^(TH|SL|DL|PC)$")
    _BULLET_LABELS = ("privacy", "density", "unit_type", "parking", "amenity_pct", "balcony", "semiprivate_note")

    def _prototypes(self, ex: Extraction, path_ref, ref_range, text_of, real_range) -> None:
        ref = self._find_ref(path_ref, "Multifamily Prototypes", "Prototypes")
        if ref is None:
            # falls back to the chapter-level "Prototypes" entry if the nested one differs
            ref = self._find_ref(path_ref, "Prototypes")
        if ref is None:
            ex.warnings.append("prototypes section not found in TOC")
            return
        lo, hi = ref_range[ref]
        all_pages = list(range(lo, hi + 1))
        pages = real_range(lo, hi)
        skipped = [p for p in all_pages if p not in pages]
        codes_expected = (
            [f"TH{n}" for n in range(1, 7)] + [f"SL{n}" for n in range(1, 4)]
            + [f"DL{n}" for n in range(1, 11)] + [f"PC{n}" for n in range(1, 12)]
        )
        if not pages:
            ex.warnings.append(
                f"prototypes: all {len(all_pages)} pages in range {lo}-{hi} are non-real (WIP) -- "
                f"skipped {len(codes_expected)} expected prototype codes ({', '.join(codes_expected)}); "
                "nothing ingested as fact, per CONTRACT.md's placeholder rule"
            )
            return
        if skipped:
            ex.warnings.append(f"prototypes: skipped {len(skipped)} non-real page(s) in range {lo}-{hi}: {skipped}")

        for p in pages:
            text = text_of(p)
            m = re.search(r"TYPE\s+(STH|SL|DL|PC)(\d{1,2})\b", text)
            if not m:
                continue
            fam_raw, num = m.group(1), m.group(2)
            fam = "TH" if fam_raw == "STH" else fam_raw
            code = f"{fam}{num}"
            bullets = self._prototype_bullets(text)
            attrs = self._map_prototype_facets(fam, bullets)
            ex.items.append(Item(
                item_type="pattern",
                title=f"Prototype {code}",
                summary=f"Multifamily prototype {code}: " + "; ".join(bullets) if bullets else f"Multifamily prototype {code}",
                payload={
                    "pattern_kind": "prototype",
                    "code": code,
                    "name": f"Prototype {code}",
                    "parent_pattern_id": None,
                    "attributes": {"raw_bullets": bullets, "design_variable_values": attrs},
                },
                node_ref=ref,
                citations=[Citation(page_index=p)],
            ))

    @staticmethod
    def _prototype_bullets(text: str) -> list[str]:
        """The stable ordered bullet block on a prototype page: privacy,
        density, unit type, parking, garden/amenity %, balcony, semi-private
        amenity note -- recovered as the short descriptive phrases between the
        page furniture and the 'TYPE ...' / 'FIGURE ...' markers."""
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not s or _CODE_LINE_RE.match(s) or any(p.match(s) for p in _FURNITURE_LINE_RES):
                continue
            if re.match(r"^(TYPE|FIGURE|BUILDING|SECTION|GF|CLUSTERING|BUILDING MASSING|WIP|PROTOTYPES|"
                        r"MULTIFAMILY PROTOTYPES|STACKED TOWNHOUSE|SINGLE LOADED|DOUBLE LOADED|POINT CORE|"
                        r"LOWER GF / BASEMENT)\b", s, re.I):
                continue
            out.append(s)
        return out

    @classmethod
    def _map_prototype_facets(cls, fam: str, bullets: list[str]) -> dict[str, Any]:
        joined = " | ".join(bullets).lower()
        result: dict[str, Any] = {"access_typology": _ACCESS_BY_PREFIX.get(fam)}
        density_map = {"low density": "low_rise", "medium density": "mid_rise", "high density": "high_rise"}
        for phrase, value_id in density_map.items():
            if phrase in joined:
                result["development_height"] = f"development_height.{value_id}"
                break
        unit_map = ["stepped duplex", "duplex", "triplex", "simplex"]
        for u in unit_map:
            if u in joined:
                result["unit_typology"] = f"unit_typology.{_slug(u)}"
                break
        parking: list[str] = []
        if "half basement" in joined:
            parking.append("half_basement")
        elif "basement" in joined:
            parking.append("basement")
        if "podium" in joined:
            parking.append("podium")
        if "grade" in joined or "street parking" in joined:
            parking.append("at-grade")
        if parking:
            result["parking_configuration"] = [f"parking_configuration.{p.replace('-', '_')}" for p in parking]
        amenity: list[str] = []
        if "garden" in joined:
            amenity.append("garden")
        if "roof terrace" in joined:
            amenity.append("roof_terrace")
        if "balcon" in joined:
            amenity.append("external_balconies")
        if "enclosed terrace" in joined:
            amenity.append("enclosed_terraces")
        if amenity:
            result["private_external_amenity"] = [f"private_external_amenity.{a}" for a in amenity]
        if "retail" in joined:
            result["ground_floor_condition"] = "ground_floor_condition.retail"
        return {k: v for k, v in result.items() if v}

    # ── urban clusters ─────────────────────────────────────────────────

    def _urban_clusters(self, ex: Extraction, path_ref, ref_range, text_of, real_range) -> None:
        ref = self._find_ref(path_ref, "Urban Clustering")
        if ref is None:
            ex.warnings.append("Urban Clustering section not found in TOC")
            return
        lo, hi = ref_range[ref]
        pages = real_range(lo, hi)
        if not pages:
            ex.warnings.append(f"urban clusters: no real page in range {lo}-{hi}")
            return
        for code, name, family in _URBAN_CLUSTERS:
            cited = [p for p in pages if f"({code})" in text_of(p)]
            if not cited:
                ex.warnings.append(f"urban cluster {code} not found on any real page")
                continue
            ex.items.append(Item(
                item_type="pattern",
                title=name,
                payload={
                    "pattern_kind": "urban_cluster",
                    "code": code,
                    "name": name,
                    "parent_pattern_id": None,
                    "attributes": {"family": family, "heights": ["G+3", "G+5", "G+8"]},
                },
                node_ref=ref,
                citations=[Citation(page_index=p) for p in cited],
            ))

    # ── unit types ──────────────────────────────────────────────────────

    def _unit_types(self, ex: Extraction, path_ref, ref_range, text_of, real_range) -> None:
        for bed_label in ("One Bed Unit", "Two Bed Unit", "Three Bed Unit", "Four Bed Unit", "Five Bed Unit"):
            ref = self._find_ref(path_ref, "Typical Unit Layouts", bed_label)
            if ref is None:
                ex.warnings.append(f"unit type subsection {bed_label!r} not found in TOC")
                continue
            lo, hi = ref_range[ref]
            pages = real_range(lo, hi)
            if not pages:
                ex.warnings.append(f"unit type {bed_label!r}: no real page in range {lo}-{hi}")
                continue
            text = " ".join(text_of(p) for p in pages)
            codes: list[str] = []
            for c in re.findall(r"\b([1-5]BD-\d{2})\b", text):
                if c not in codes:
                    codes.append(c)
            grid = re.search(r"(\d(?:\.\d)?x\d)\s*grid module", text)
            bed_count = int(bed_label.split()[0].replace("One", "1").replace("Two", "2")
                             .replace("Three", "3").replace("Four", "4").replace("Five", "5")) \
                if bed_label.split()[0] in ("One", "Two", "Three", "Four", "Five") else None
            for code in codes:
                ex.items.append(Item(
                    item_type="pattern",
                    title=f"Unit type {code}",
                    payload={
                        "pattern_kind": "unit_type",
                        "code": code,
                        "name": f"{bed_label} {code}",
                        "parent_pattern_id": None,
                        "attributes": {
                            "bed_count": bed_count,
                            "grid_module": grid.group(1) if grid else None,
                        },
                    },
                    node_ref=ref,
                    citations=[Citation(page_index=p) for p in pages],
                ))

    # ── guidance per real subsection ───────────────────────────────────

    def _guidance(self, ex: Extraction, ref_range, text_of, real_range) -> None:
        for node in ex.nodes:
            if node.node_kind != "subsection":
                continue
            lo, hi = ref_range[node.ref]
            pages = real_range(lo, hi)
            if not pages:
                continue
            raw = "\n".join(text_of(p) for p in pages)
            body = _clean_body(raw)
            if len(body) < 40:
                continue  # index/divider pages with essentially no prose
            ex.items.append(Item(
                item_type="guidance",
                title=node.title,
                statement=body[:500],
                payload={
                    "body_md": body,
                    "figure_ids": [],
                    "legend_tokens": _figure_codes(raw),
                    "disclaimer": _disclaimer(raw),
                },
                node_ref=node.ref,
                citations=[Citation(page_index=p) for p in pages],
            ))

    # ── benchmarks: FAR, plot coverage, floor-to-floor, balustrade, window ─

    def _benchmarks(self, ex: Extraction, page_map: dict, text_of) -> None:
        seen: set[tuple[str, str]] = set()
        for idx, row in sorted(page_map.items()):
            if row.get("content_status", "real") != "real":
                continue
            # de-hyphenate line-wrapped words ("consid-\nered" -> "considered")
            # so a benchmark sentence split across two lines still matches
            text = re.sub(r"(\w)-\n(\w)", r"\1\2", text_of(idx))
            for metric_id, label, pattern, unit_id in _BENCHMARK_PATTERNS:
                for m in pattern.finditer(text):
                    verbatim = m.group(0).strip()
                    key = (metric_id, verbatim)
                    if key in seen:
                        continue
                    seen.add(key)
                    groups = m.groups()
                    value_numeric = value_min = value_max = None
                    if metric_id == "far_by_storeys":
                        value_text = f"{groups[0]} storeys ~ {groups[1]}:{groups[2]} F.A.R."
                        value_numeric = int(groups[1]) / int(groups[2])
                    elif metric_id == "balustrade_height":
                        value_min, value_max = float(groups[0]), float(groups[1])
                        value_text = f"{groups[0]}-{groups[1]}mm"
                    else:
                        value_text = f"{groups[0]}{'%' if unit_id == 'pct' else unit_id if unit_id != 'ratio' else ''}"
                        try:
                            value_numeric = float(groups[0])
                        except ValueError:
                            pass
                    ex.items.append(Item(
                        item_type="benchmark",
                        title=label,
                        payload={
                            "metric_id": metric_id,
                            "value_numeric": value_numeric,
                            "value_min": value_min,
                            "value_max": value_max,
                            "value_text": value_text,
                            "unit_id": unit_id,
                            "comparator": "range" if value_min is not None else "none",
                            "is_placeholder": False,
                            "caveat_text": None,
                        },
                        citations=[Citation(page_index=idx)],
                    ))

    @staticmethod
    def _counts(ex: Extraction) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for it in ex.items:
            if it.item_type == "pattern":
                k = it.payload.get("pattern_kind", "?")
                by_kind[k] = by_kind.get(k, 0) + 1
        return {
            "nodes": len(ex.nodes),
            "patterns_by_kind": by_kind,
            "definitions": sum(1 for it in ex.items if it.item_type == "definition"),
            "guidance": sum(1 for it in ex.items if it.item_type == "guidance"),
            "benchmarks": sum(1 for it in ex.items if it.item_type == "benchmark"),
        }


_INTRO_BULLET_RE = re.compile(r"TERMS:\s*defines\b", re.I)


def _clean_body_keep_colons(text: str) -> str:
    """Like _clean_body, but keeps line breaks (the glossary term regex needs
    a line-oriented view) and drops the page's category-marker header plus
    the three-bullet 'TECHNICAL + ADMINISTRATIVE TERMS: defines ...' preamble
    that introduces the categories -- text that would otherwise parse as a
    bogus glossary entry in its own right."""
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _CODE_LINE_RE.match(s):
            continue
        if any(p.match(s) for p in _FURNITURE_LINE_RES):
            continue
        if any(marker in s for marker, _c in _DEFINITION_CATEGORY_MARKERS):
            continue
        if _INTRO_BULLET_RE.search(s):
            continue
        lines.append(s)
    # re-join with newlines (not spaces) so the term regex's line-start lookahead works
    return "\n".join(lines)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


TYPOLOGY_CATALOGUE = TypologyCatalogueExtractor()
register(TYPOLOGY_CATALOGUE)
