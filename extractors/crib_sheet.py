"""Extractor for the crib-sheet grid PDFs. doc_kinds = ("crib_sheet",)

Each sheet is 2 pages, A3 landscape: page 1 is a reference page (citations,
sometimes a typology benchmark table); page 2 is a maturity matrix -- rows are
criteria, the four right-hand columns are performance levels, cells hold 0..n
requirement statements.

Geometry (column x-bands, row anchors, sub-criteria indentation) is measured
per file from `page.get_drawings()` and word positions -- never hardcoded.
Five of the six sheets share a layout; `crib-climate-resilience` does not
(different band x-range, no sub-criteria indentation, different header y).
This module makes no assumption that would break on that file.

Cross-record links: this module reuses the Node `ref`/`parent_ref` convention
documented in tools/pipeline.py for the criterion/rating_scale/rating_level/
framework/unit/metric dicts too, since the contract only spells that
mechanism out for Node<->Item. Concretely:
  - `criteria`, `rating_scales`, `rating_levels`, `frameworks` dicts each
    carry a `ref` string and, where they point at another lookup row, a
    `<thing>_ref` string (e.g. `rating_level["scale_ref"]`).
  - `Item.payload["criterion_id"]` / `["rating_level_id"]` hold that same ref
    string, not a uuid -- the writer is expected to resolve them exactly like
    `node_ref`, extended to cover the lookup rows an Extraction carries.
  - `metric_id` / `unit_id` are different: those PKs are natural text ids in
    the schema already (`metric.id`, `unit.id`), so the payload holds the
    literal id and no resolution step is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, Reference, register

RATING_SCALE_SLUG = "crib-levels"
FRAMEWORK_SLUG = "practice-crib-sheets"

QUALIFIER_WORDS = {"CTO", "CONTRIBUTIVE", "HIGH", "PERFORMANCE", "EXEMPLAR"}

MODULE_REF_RE = re.compile(r"\(Module\s+\d+[^)]*\)", re.I)
MODULE_PAGE_RE = re.compile(r"Module\s+\d+\s*P\d+(?:\s*-\s*P?\d+)?", re.I)

NUM = r"-?\d+(?:\.\d+)?"

# free-text unit -> (unit_id, symbol). Anything unseen gets a slugified ad-hoc id.
UNIT_ALIASES = {
    "l/p/day": ("lpd", "l/p/day"),
    "ppm": ("ppm", "ppm"),
    "ppb": ("ppb", "ppb"),
    "%": ("pct", "%"),
    "kgco2e/kg": ("kgco2e_kg", "kgCO2e/kg"),
    "µg/m3": ("ug_m3", "µg/m³"),
    "mg/m3": ("mg_m3", "mg/m³"),
    "db": ("db", "dB"),
    "dba": ("db", "dBA"),
    "sec": ("s", "sec"),
    "years": ("yr", "years"),
    "kwh/m2.year": ("kwh_m2_yr", "kWh/m2.year"),
    "kgco2e/m2gia": ("kgco2e_m2_gia", "kgCO2e/m2GIA"),
}


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return s or "x"


def _dedupe_repeat(text: str) -> str:
    """Collapse the sheets' embossed-text rendering: a string repeated back
    to back with no separator ('or select equivalent water target' x3)."""
    t = text.strip()
    n = len(t)
    for period in range(1, n // 2 + 1):
        if n % period:
            continue
        unit = t[:period]
        if unit * (n // period) == t:
            return unit
    return t


def _fill_hex(fill: tuple[float, float, float] | None) -> str | None:
    if not fill:
        return None
    r, g, b = (max(0, min(255, round(c * 255))) for c in fill)
    return f"#{r:02x}{g:02x}{b:02x}"


def _norm_unit(raw: str) -> str | None:
    key = raw.strip().lower().replace(" ", "")
    return key or None


def _unit_lookup(raw: str) -> tuple[str, str] | None:
    key = _norm_unit(raw)
    if not key:
        return None
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    if re.fullmatch(r"[a-z0-9/.%µ°²·-]+", key):
        return (_slugify(key), raw.strip())
    return None


@dataclass
class Band:
    ordinal: int
    x0: float
    x1: float
    header_y0: float
    header_y1: float
    colour: str | None
    name: str | None = None


def _dict_blocks(page: pymupdf.Page) -> list[dict]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        lines = b.get("lines", [])
        if not lines:
            continue
        text = "".join(s["text"] for l in lines for s in l["spans"])
        if not text.strip():
            continue
        out.append({"bbox": b["bbox"], "text": text, "lines": lines})
    return out


def _detect_bands(page: pymupdf.Page, warnings: list[str]) -> list[Band]:
    """Recover the 4 level-column x-bands from filled rects, per file."""
    drawings = page.get_drawings()
    by_x: dict[int, list[tuple[pymupdf.Rect, tuple]]] = {}
    for d in drawings:
        fill = d.get("fill")
        rect = d.get("rect")
        if fill is None or rect is None:
            continue
        if rect.width < 150 or rect.height < 150:
            continue
        # exclude near-white / near-black decoration
        if all(c > 0.95 for c in fill) or all(c < 0.05 for c in fill):
            continue
        key = round(rect.x0 / 3)
        by_x.setdefault(key, []).append((rect, fill))

    bodies: list[tuple[pymupdf.Rect, tuple]] = []
    for group in by_x.values():
        tallest = max(group, key=lambda rg: rg[0].height)
        bodies.append(tallest)
    bodies.sort(key=lambda rg: rg[0].x0)

    if len(bodies) != 4:
        warnings.append(
            f"expected 4 level-column bands from drawings, found {len(bodies)}"
        )

    bands: list[Band] = []
    for i, (rect, _fill) in enumerate(bodies, start=1):
        # header swatch: same x-cluster, sitting above the body rect
        header_rects = [
            (r, f) for r, f in
            [(dd.get("rect"), dd.get("fill")) for dd in drawings]
            if r is not None and f is not None
            and abs(r.x0 - rect.x0) < 5 and r.y1 <= rect.y0 + 2
            and 20 < r.height < 90 and r.width > 100
        ]
        header_colour = header_rects[0][1] if header_rects else _fill
        header_y0 = min((r.y0 for r, _ in header_rects), default=max(0.0, rect.y0 - 70))
        header_y1 = min((r.y1 for r, _ in header_rects), default=rect.y0)
        bands.append(Band(i, rect.x0, rect.x1, header_y0, header_y1, _fill_hex(header_colour)))
    return bands


def _assign_level_names(page: pymupdf.Page, bands: list[Band]) -> None:
    """Match qualifier phrases ('HIGH PERFORMANCE', ...) in the header zone
    to the nearest band by x-centre. LEVEL 1 is left unlabelled unless
    evidence says otherwise."""
    if not bands:
        return
    top = min(b.header_y0 for b in bands) - 5
    bottom = max(b.header_y1 for b in bands) + 5
    words = [w for w in page.get_text("words") if top <= w[1] <= bottom]
    qualifier_words = [w for w in words if w[4].upper() in QUALIFIER_WORDS]
    # group into phrases by (rounded baseline y, word block/line ids)
    qualifier_words.sort(key=lambda w: (w[6], w[0]))
    phrases: list[tuple[float, float, str]] = []  # (x0, x1, text)
    cur: list[tuple] = []
    for w in qualifier_words:
        if cur and (w[6] != cur[-1][6] or w[0] - cur[-1][2] > 40):
            phrases.append((cur[0][0], cur[-1][2], " ".join(c[4] for c in cur)))
            cur = []
        cur.append(w)
    if cur:
        phrases.append((cur[0][0], cur[-1][2], " ".join(c[4] for c in cur)))

    for x0, x1, text in phrases:
        centre = (x0 + x1) / 2
        best = min(bands, key=lambda b: abs((b.x0 + b.x1) / 2 - centre))
        if best.name:
            best.name = f"{best.name} {text}"
        else:
            best.name = text


def _row_badges(page: pymupdf.Page, label_right: float, body_top: float) -> list[float]:
    """Small square rects inside the body region, hard-left of the columns:
    the row-ordinal badges. Colour differs per sheet -- shape is what's
    stable."""
    ys = []
    for d in page.get_drawings():
        rect, fill = d.get("rect"), d.get("fill")
        if rect is None or fill is None:
            continue
        if all(c > 0.95 for c in fill) or all(c < 0.05 for c in fill):
            continue
        if 14 <= rect.width <= 36 and 14 <= rect.height <= 36 and abs(rect.width - rect.height) < 4:
            if rect.x1 < label_right and rect.y0 >= body_top - 10:
                ys.append(rect.y0)
    return sorted(ys)


def _cluster_1d(values: list[float], gap: float = 10.0) -> list[list[float]]:
    if not values:
        return []
    vals = sorted(values)
    groups = [[vals[0]]]
    for v in vals[1:]:
        if v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return groups


def _finish_run(band: Band | None, words: list) -> dict:
    text = " ".join(w[4] for w in words)
    y0 = min(w[1] for w in words)
    y1 = max(w[3] for w in words)
    block_no = words[0][5]
    return {"band": band, "y0": y0, "y1": y1, "text": text, "block_no": block_no}


def _cell_statements(page: pymupdf.Page, bands: list[Band], leaves: list[dict],
                      body_top: float) -> list[tuple[Band, dict, str]]:
    """Reconstruct per-(band, leaf-row) statements from words, not blocks.

    Blocks are not reliable cell boundaries on these sheets: the embossed-
    text rendering sometimes duplicates one statement across two adjacent
    bands as a single wide block, and some rows pack up to three distinct
    per-band statements onto one physical line ('Reduce ... 15%Reduce ...
    25%Reduce ... 30%'). Both only resolve at word level, so every line is
    rebuilt from words and re-split wherever it crosses a band boundary,
    then re-grouped into paragraphs on vertical gaps within one band+row.
    """
    words = page.get_text("words")
    band_lo, band_hi = bands[0].x0 - 3, bands[-1].x1 + 3

    def band_for_x(x0: float, x1: float) -> Band | None:
        centre = (x0 + x1) / 2
        for b in bands:
            if b.x0 - 3 <= centre <= b.x1 + 3:
                return b
        return None

    lines: dict[tuple[int, int], list] = {}
    for w in words:
        x0, y0, x1, y1, _text, block_no, line_no = w[0], w[1], w[2], w[3], w[4], w[5], w[6]
        if y0 < body_top - 5 or x1 < band_lo or x0 > band_hi:
            continue
        lines.setdefault((block_no, line_no), []).append(w)

    runs: list[dict] = []
    for ws in lines.values():
        ws = sorted(ws, key=lambda w: w[0])
        cur_band, cur = None, []
        for w in ws:
            b = band_for_x(w[0], w[2])
            if b is None or (cur and b.ordinal != cur_band.ordinal):
                if cur:
                    runs.append(_finish_run(cur_band, cur))
                cur = []
            if b is None:
                cur_band = None
                continue
            cur_band = b
            cur.append(w)
        if cur:
            runs.append(_finish_run(cur_band, cur))

    for r in runs:
        y_mid = (r["y0"] + r["y1"]) / 2
        r["leaf"] = next((l for l in leaves if l["y0"] - 1 <= y_mid < l["y1"] + 1), None)
    runs = [r for r in runs if r["leaf"] is not None]

    # group lines into statements by shared source block_no, not by vertical
    # gap: on these sheets a paragraph's own line-wrap gap can be *larger*
    # than the gap between two genuinely separate bullets (observed: 11pt
    # wrap vs. 3.6pt between bullets), so gap size is not a usable signal.
    # block_no is -- pymupdf keeps one logical text run's lines under one
    # block_no even when (per the module docstring) that run's *bands*
    # differ line to line.
    groups: dict[tuple[int, str, int], list[dict]] = {}
    for r in runs:
        key = (r["band"].ordinal, r["leaf"]["ref"], r["block_no"])
        groups.setdefault(key, []).append(r)

    out: list[tuple[Band, dict, str]] = []
    for key, group in groups.items():
        group.sort(key=lambda r: r["y0"])
        text = _dedupe_repeat(re.sub(r"\s+", " ", " ".join(g["text"] for g in group))).strip()
        if text:
            out.append((group[0]["band"], group[0]["leaf"], text))
    out.sort(key=lambda t: (t[0].ordinal, t[1]["ref"], t[1]["y0"]))
    return out


def _parse_requirement(text: str) -> tuple[float | None, str | None, str, bool]:
    """Best-effort numeric parse of a requirement cell. Always returns
    (target_value, unit_id, comparator, parsed_ok); caller keeps target_text
    verbatim regardless of whether this parses anything."""
    t = text.strip()

    m = re.search(rf"({NUM})\s*-\s*({NUM})\s*([%a-zA-Zµ°/²·]*)", t)
    if m and t[max(0, m.start() - 1)] not in "A-Za-z0-9":
        return None, None, "range", False

    m = re.search(rf"[<≤]\s*({NUM})\s*([%a-zA-Zµ°/²·]*)", t)
    if m:
        unit = _unit_lookup(m.group(2)) if m.group(2) else None
        return float(m.group(1)), (unit[0] if unit else None), "lte", True

    m = re.search(rf"[>≥]\s*({NUM})\s*([%a-zA-Zµ°/²·]*)", t)
    if m:
        unit = _unit_lookup(m.group(2)) if m.group(2) else None
        return float(m.group(1)), (unit[0] if unit else None), "gte", True

    m = re.search(rf"({NUM})\s*%", t)
    if m:
        return float(m.group(1)), "pct", "none", True

    m = re.fullmatch(rf"({NUM})\s*([a-zA-Zµ°/²·]+)", t)
    if m:
        unit = _unit_lookup(m.group(2))
        return float(m.group(1)), (unit[0] if unit else None), "none", True

    return None, None, "none", False


class CribSheetExtractor:
    doc_kinds = ("crib_sheet",)

    def extract(self, ctx: DocumentContext) -> Extraction:
        ext = Extraction()
        doc = pymupdf.open(ctx.path)
        try:
            if len(doc) < 2:
                ext.warnings.append(f"{ctx.slug}: expected 2 pages, found {len(doc)}")
                return ext
            self._extract(ctx, doc, ext)
        finally:
            doc.close()
        return ext

    # -- top-level structure ------------------------------------------------

    def _extract(self, ctx: DocumentContext, doc: pymupdf.Document, ext: Extraction) -> None:
        root_ref = f"{ctx.slug}-root"
        p1_ref = f"{ctx.slug}-p1"
        p2_ref = f"{ctx.slug}-p2"
        ext.nodes.append(Node(node_kind="sheet", title=ctx.slug, ordinal=0,
                               page_from=1, page_to=2, ref=root_ref))
        ext.nodes.append(Node(node_kind="section", title="Reference", ordinal=1,
                               page_from=1, page_to=1, parent_ref=root_ref, ref=p1_ref))
        ext.nodes.append(Node(node_kind="matrix", title="Maturity Matrix", ordinal=2,
                               page_from=2, page_to=2, parent_ref=root_ref, ref=p2_ref))

        ext.rating_scales.append({"ref": RATING_SCALE_SLUG, "slug": RATING_SCALE_SLUG,
                                   "name": "Crib sheet maturity levels"})
        ext.frameworks.append({
            "ref": FRAMEWORK_SLUG, "slug": FRAMEWORK_SLUG,
            "name": "Practice crib sheets", "owner_org_id": None,
            "version": None, "rating_scale_ref": RATING_SCALE_SLUG,
            "document_ref": None,
        })

        page1, page2 = doc[0], doc[1]

        self._extract_page1(ctx, page1, p1_ref, ext)
        self._extract_matrix(ctx, page2, p2_ref, ext)

    # -- page 1: references + typology benchmark table ----------------------

    def _extract_page1(self, ctx: DocumentContext, page: pymupdf.Page,
                        node_ref: str, ext: Extraction) -> None:
        text = page.get_text("text")
        seen_raw = set()
        for pat, kind in ((MODULE_REF_RE, "module_chapter"), (MODULE_PAGE_RE, "page")):
            for m in pat.finditer(text):
                raw = re.sub(r"\s+", " ", m.group(0)).strip()
                if raw in seen_raw:
                    continue
                seen_raw.add(raw)
                ext.references.append(Reference(raw_text=raw, ref_kind=kind,
                                                  from_node_ref=node_ref))

        self._extract_typology_table(ctx, page, node_ref, ext)
        self._extract_goal_snippets(ctx, page, node_ref, ext)

    def _extract_typology_table(self, ctx: DocumentContext, page: pymupdf.Page,
                                 node_ref: str, ext: Extraction) -> None:
        """Generic typology x year benchmark table (embodied carbon,
        operational EUI). Column geometry is read from the '20xx' header
        words; if no such header is present the sheet simply has no such
        table and this is a no-op."""
        words = page.get_text("words")
        year_words = [w for w in words if re.fullmatch(r"(19|20)\d{2}", w[4])]
        if len(year_words) < 2:
            return
        # cluster year columns by x
        year_words.sort(key=lambda w: w[0])
        cols: list[list] = []
        for w in year_words:
            if cols and w[0] - cols[-1][-1][0] < 15:
                cols[-1].append(w)
            else:
                cols.append([w])
        if len(cols) < 2:
            return
        col_x = [ (min(w[0] for w in c), max(w[2] for w in c)) for c in cols]
        col_year = [int(c[0][4]) for c in cols]
        header_bottom = max(w[3] for c in cols for w in c)

        page_title = page.get_text("text")[:400].lower()
        if "embodied" in page_title:
            metric_id, unit_id, unit_symbol = "upfront_embodied_carbon", "kgco2e_m2_gia", "kgCO2e/m2GIA"
        elif "operational" in page_title or "eui" in page_title:
            metric_id, unit_id, unit_symbol = "eui", "kwh_m2_yr", "kWh/m2.year"
        else:
            metric_id, unit_id, unit_symbol = "benchmark_value", "unit_" + ctx.slug, "?"
            ext.warnings.append(f"{ctx.slug}: typology table found but metric could not "
                                 f"be identified from page title; using generic ids")

        ext.units.append({"id": unit_id, "symbol": unit_symbol, "dimension": None, "si_factor": None})
        ext.metrics.append({"id": metric_id, "name": metric_id.replace("_", " ").title(),
                             "definition": None, "default_unit_id": unit_id,
                             "formula": None, "higher_is_better": False})

        label_x1 = min(x0 for x0, _x1 in col_x) - 5
        # bound the label column on the left too: a typology name is a short
        # phrase, not the wider descriptive paragraphs that sit further left
        # on the same page (e.g. embodied carbon's "Regional materials
        # sources" body copy). 110pt covers the widest observed wrap
        # ("High street retail and dept store") while staying short of the
        # body-text column on both sheets this table shape appears on.
        label_x0_min = label_x1 - 110
        label_words = [w for w in words
                        if label_x0_min <= w[0] and w[2] <= label_x1
                        and w[1] > header_bottom - 5]

        value_words = [w for w in words
                       if any(cx0 - 3 <= w[0] <= cx1 + 3 for cx0, cx1 in col_x)
                       and w[1] > header_bottom and re.fullmatch(r"\d+(?:\.\d+)?", w[4])]
        row_ys = sorted({round(w[1], 0) for w in value_words})
        merged_rows: list[float] = []
        for y in row_ys:
            if merged_rows and y - merged_rows[-1] < 4:
                continue
            merged_rows.append(y)
        # the table ends where row spacing breaks -- anything past that gap
        # is unrelated page content (footnote markers, other tables) whose
        # numbers happen to fall inside the same x-columns.
        if len(merged_rows) > 3:
            gaps = [b - a for a, b in zip(merged_rows, merged_rows[1:])]
            typical = sorted(gaps[:5])[len(gaps[:5]) // 2]
            cutoff = len(merged_rows)
            for i, g in enumerate(gaps):
                if g > max(40.0, typical * 2.5):
                    cutoff = i + 1
                    break
            merged_rows = merged_rows[:cutoff]

        n_emitted = 0
        for row_y in merged_rows:
            row_vals = [w for w in value_words if abs(w[1] - row_y) < 4]
            row_labels = [w for w in label_words if row_y - 3 <= w[1] <= row_y + 12]
            if not row_labels:
                continue
            label = _dedupe_repeat(re.sub(r"\s+", " ", " ".join(w[4] for w in
                                    sorted(row_labels, key=lambda w: (w[1], w[0]))))).strip()
            if not label:
                continue
            use_id = _slugify(label)
            for cx0, cx1 in col_x:
                match = [w for w in row_vals if cx0 - 3 <= w[0] <= cx1 + 3]
                if not match:
                    continue
                value_text = match[0][4]
                year = col_year[col_x.index((cx0, cx1))]
                try:
                    value_numeric = float(value_text)
                except ValueError:
                    value_numeric = None
                ext.items.append(Item(
                    item_type="benchmark",
                    title=f"{label} {year} target",
                    node_ref=node_ref,
                    content_status=ctx.meta.get("content_status", "real"),
                    confidence=0.85,
                    citations=[Citation(page_index=1)],
                    payload={
                        "metric_id": metric_id, "value_numeric": value_numeric,
                        "value_min": None, "value_max": None,
                        "value_text": value_text, "unit_id": unit_id,
                        "comparator": "none", "is_placeholder": False,
                        "caveat_text": None, "building_use_id": use_id,
                        "target_year": year, "region_id": None,
                        "standard_id": None, "baseline_relative_pct": None,
                    },
                ))
                n_emitted += 1
        ext.stats[f"{ctx.slug}_typology_benchmarks"] = n_emitted

    def _extract_goal_snippets(self, ctx: DocumentContext, page: pymupdf.Page,
                                node_ref: str, ext: Extraction) -> None:
        """Best-effort metric->goal snippets on page 1: short standalone
        blocks that read as a bare target ('10%*', '<900 ppm', 'X km of...',
        'GWP < 50'). Conservative on purpose -- see module docstring."""
        goal_re = re.compile(
            rf"^(?:X\s*%|X\s*km\b.*|X\s*no\.?\s*of\b.*|"
            rf"[<>≤≥]?\s*{NUM}(?:\s*-\s*{NUM})?\*?\s*[a-zA-Zµ°%/²·]*\*?)$"
        )
        n = 0
        for block in _dict_blocks(page):
            text = _dedupe_repeat(re.sub(r"\s+", " ", block["text"])).strip()
            if len(text) > 60 or not text:
                continue
            if not goal_re.match(text):
                continue
            is_placeholder = bool(re.match(r"^X\s*(%|km|no)", text, re.I))
            caveat = None
            body = text
            if body.endswith("*"):
                caveat = "footnoted with an asterisk on the source page; footnote text not " \
                         "positionally linked by this extractor"
                body = body[:-1].strip()
            num_m = re.search(NUM, body)
            value_numeric = float(num_m.group(0)) if (num_m and not is_placeholder) else None
            metric_id = f"page_goal_{ctx.slug.replace('-', '_')}"
            if not any(m["id"] == metric_id for m in ext.metrics):
                ext.metrics.append({"id": metric_id, "name": f"{ctx.slug} page-1 goal",
                                     "definition": "best-effort metric->goal snippet, "
                                                    "not attributable to a specific label",
                                     "default_unit_id": None, "formula": None,
                                     "higher_is_better": None})
            ext.items.append(Item(
                item_type="benchmark",
                title=None,
                node_ref=node_ref,
                content_status=ctx.meta.get("content_status", "real"),
                confidence=0.4,
                citations=[Citation(page_index=1)],
                payload={
                    "metric_id": metric_id, "value_numeric": value_numeric,
                    "value_min": None, "value_max": None, "value_text": text,
                    "unit_id": None, "comparator": "none",
                    "is_placeholder": is_placeholder, "caveat_text": caveat,
                    "building_use_id": None, "target_year": None,
                    "region_id": None, "standard_id": None,
                    "baseline_relative_pct": None,
                },
            ))
            n += 1
        ext.stats[f"{ctx.slug}_goal_snippets"] = n
        if n == 0:
            ext.warnings.append(f"{ctx.slug}: no page-1 metric->goal snippets matched "
                                 f"(conservative pattern; may under-count)")

    # -- page 2: maturity matrix ---------------------------------------------

    def _extract_matrix(self, ctx: DocumentContext, page: pymupdf.Page,
                         node_ref: str, ext: Extraction) -> None:
        bands = _detect_bands(page, ext.warnings)
        if len(bands) < 2:
            ext.warnings.append(f"{ctx.slug}: matrix geometry unrecoverable, "
                                 f"only {len(bands)} level bands found -- skipping page 2")
            return
        _assign_level_names(page, bands)
        for b in bands:
            if b.ordinal == 1 and b.name:
                ext.warnings.append(f"{ctx.slug}: LEVEL 1 unexpectedly carries a "
                                     f"qualifier word ({b.name!r}); recorded as observed")
            ext.rating_levels.append({
                "ref": f"{RATING_SCALE_SLUG}-{b.ordinal}", "scale_ref": RATING_SCALE_SLUG,
                "ordinal": b.ordinal, "code": f"L{b.ordinal}", "name": b.name,
                "description": None, "colour": b.colour,
            })

        label_right = bands[0].x0 - 2
        body_top = min(b.header_y1 for b in bands)
        body_bottom = max((r.y1 for d in page.get_drawings()
                            if (r := d.get("rect")) is not None and r.x0 >= label_right - 5
                            and r.width > 150), default=page.rect.height - 40)

        badge_ys = _row_badges(page, label_right, body_top)

        label_blocks = []
        aux_blocks = []
        for blk in _dict_blocks(page):
            x0, y0, x1, y1 = blk["bbox"]
            if x1 > label_right:
                continue
            if y1 <= body_top - 10:
                continue  # header zone
            text = blk["text"].strip()
            if re.fullmatch(r"\d{1,2}", text):
                continue  # the row-number badge itself, already have y from drawings
            width, height = x1 - x0, y1 - y0
            if width < 25 and height > 80:
                aux_blocks.append(blk)  # rotated side annotation, e.g. METHOD tags
                continue
            label_blocks.append(blk)

        if aux_blocks:
            ext.warnings.append(
                f"{ctx.slug}: {len(aux_blocks)} rotated side-label block(s) found in the "
                f"criteria column (e.g. method/grouping tags); not positionally attributed "
                f"to individual rows, recorded on the sheet-level node text instead"
            )

        clusters = _cluster_1d([b["bbox"][0] for b in label_blocks], gap=10.0)
        outer_x_max = clusters[0][-1] if clusters else 0.0
        outer_blocks = [b for b in label_blocks if b["bbox"][0] <= outer_x_max + 0.5]
        inner_blocks = [b for b in label_blocks if b["bbox"][0] > outer_x_max + 0.5]

        if not badge_ys:
            ext.warnings.append(f"{ctx.slug}: no row-ordinal badges recovered from drawings; "
                                 f"falling back to outer-title block y-positions for row anchors")
            badge_ys = sorted(b["bbox"][1] for b in outer_blocks)

        if not inner_blocks:
            ext.warnings.append(f"{ctx.slug}: no sub-criteria indentation detected -- "
                                 f"treating this sheet as flat (matches the documented "
                                 f"climate-resilience outlier; unexpected for other sheets)")

        # top-level rows, anchored on badges, titled by the nearest outer block
        top_rows = []
        for i, y0 in enumerate(badge_ys):
            y_end = badge_ys[i + 1] if i + 1 < len(badge_ys) else body_bottom
            title_blk = min(outer_blocks, key=lambda b: abs(b["bbox"][1] - y0),
                             default=None) if outer_blocks else None
            title = title_blk["text"].strip() if title_blk else ""
            top_rows.append({"ordinal": i + 1, "y0": y0, "y1": y_end, "title": title})

        # match page-1 numbered headings for title_alt, keyed by ordinal
        p1_headings = self._page1_headings(ctx, page.parent[0]) if page.parent else {}

        leaves = []  # each: code, title_primary, title_alt, parent_ref, y0, y1
        for row in top_rows:
            code = f"{ctx.slug}-{row['ordinal']}"
            ref = f"crit-{code}"
            children = [b for b in inner_blocks if row["y0"] - 1 <= b["bbox"][1] < row["y1"]]
            children.sort(key=lambda b: b["bbox"][1])
            ext.criteria.append({
                "ref": ref, "framework_ref": FRAMEWORK_SLUG, "parent_ref": None,
                "code": code, "title_primary": row["title"],
                "title_alt": p1_headings.get(row["ordinal"]),
                "ordinal": row["ordinal"],
            })
            if not children:
                leaves.append({"ref": ref, "y0": row["y0"], "y1": row["y1"]})
                continue
            # a top-level row can carry its own directly-graded content
            # *and* have sub-criteria below it (crib-water's "Resilience"
            # row does exactly this) -- keep the gap before the first child
            # attributed to the row itself rather than dropping it.
            if children[0]["bbox"][1] - row["y0"] > 8:
                leaves.append({"ref": ref, "y0": row["y0"], "y1": children[0]["bbox"][1]})
            for k, child in enumerate(children, start=1):
                c_y0 = child["bbox"][1]
                c_y1 = children[k]["bbox"][1] if k < len(children) else row["y1"]
                c_code = f"{code}.{k}"
                c_ref = f"crit-{c_code}"
                ext.criteria.append({
                    "ref": c_ref, "framework_ref": FRAMEWORK_SLUG, "parent_ref": ref,
                    "code": c_code, "title_primary": child["text"].strip(),
                    "title_alt": None, "ordinal": k,
                })
                leaves.append({"ref": c_ref, "y0": c_y0, "y1": c_y1})

        # cell content. Blocks are *not* reliable cell boundaries here: the
        # sheets' embossed-text rendering sometimes duplicates one statement
        # across two adjacent bands as a single wide block, and some rows
        # pack three distinct per-band statements onto one physical line
        # ("Reduce ... 15%Reduce ... 25%Reduce ... 30%"). Both only resolve
        # at word level, so cell content is reconstructed from words grouped
        # back into lines, each line re-split wherever it crosses a band
        # boundary.
        n_statements = 0
        for band, leaf, statement in _cell_statements(page, bands, leaves, body_top):
                target_value, unit_id, comparator, parsed_ok = _parse_requirement(statement)
                if unit_id and not any(u["id"] == unit_id for u in ext.units):
                    ext.units.append({"id": unit_id, "symbol": statement, "dimension": None,
                                       "si_factor": None})
                refs_found = []
                for pat, kind in ((MODULE_REF_RE, "module_chapter"), (MODULE_PAGE_RE, "page")):
                    for m in pat.finditer(statement):
                        refs_found.append((m.group(0), kind))
                for raw, kind in refs_found:
                    ext.references.append(Reference(raw_text=raw, ref_kind=kind,
                                                      from_node_ref=node_ref))
                ext.items.append(Item(
                    item_type="requirement",
                    statement=statement,
                    node_ref=node_ref,
                    content_status=ctx.meta.get("content_status", "real"),
                    confidence=0.8 if parsed_ok else 0.65,
                    citations=[Citation(page_index=2)],
                    payload={
                        "requirement_kind": "graded",
                        "criterion_id": leaf["ref"],
                        "rating_level_id": f"{RATING_SCALE_SLUG}-{band.ordinal}",
                        "metric_id": None, "target_value": target_value,
                        "target_text": statement, "unit_id": unit_id,
                        "comparator": comparator, "is_deliverable": False,
                        "deliverable_name": None, "parsed_ok": parsed_ok,
                    },
                ))
                n_statements += 1
        ext.stats[f"{ctx.slug}_criteria"] = len(ext.criteria)
        ext.stats[f"{ctx.slug}_requirement_statements"] = n_statements

    @staticmethod
    def _page1_headings(ctx: DocumentContext, page1: pymupdf.Page) -> dict[int, str]:
        """Numbered section headings on page 1 ('1Engagement with the
        Client (Module 3 Chapter 3)') keyed by ordinal, for title_alt."""
        out: dict[int, str] = {}
        for blk in _dict_blocks(page1):
            text = blk["text"].strip()
            m = re.match(r"^(\d)([A-Z][^(]*)", text)
            if not m:
                continue
            ordinal = int(m.group(1))
            heading = m.group(2).strip()
            if heading and ordinal not in out:
                out[ordinal] = heading
        return out


CRIB_SHEET = CribSheetExtractor()
register(CRIB_SHEET)
