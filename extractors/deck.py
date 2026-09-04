"""Extractor for the deck doc_kind (a PowerPoint export rendered to PDF).

Targets deck-early-stage-design: 38 slides, 26% image-only. Text extraction
alone loses most of an image-only slide, so this module emits only what the
text layer reliably carries and records everything else -- including which
slides are image-only -- in Extraction.warnings for a later vision pass.

Never records the deck's author or employer (see private/documents.yaml for
those); the source is referred to only by its slug.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf

from tools.pipeline import Citation, DocumentContext, Extraction, Item, Node, register

DOC_KINDS = ("deck",)

_FOOTER_RE = re.compile(r"COPYRIGHT.*?RESERVED\s*", re.I | re.S)
_IMAGE_ONLY_CHAR_THRESHOLD = 100

# Word-level heuristics for splitting a comfort measure into the deck's own
# passive / thermal-transition / active vocabulary. The source text itself
# names the two extremes inline ("... (passive evaporative cooling)",
# "... (active evaporative cooling)"); the buffer-space measures in between
# (sunken spaces, shelter walls) are what slide 9's text calls the
# "thermal transition zoning" -- a semi-conditioned space between the two.
_ACTIVE_HINTS = ("active", "mechanical")
_THERMAL_TRANSITION_MEASURES = {"sunken spaces", "shelter walls"}


def _clean(text: str) -> str:
    return _FOOTER_RE.sub("", text).strip()


def _measure_category(name: str) -> str:
    low = name.lower()
    if any(h in low for h in _ACTIVE_HINTS):
        return "active"
    if low.strip() in _THERMAL_TRANSITION_MEASURES:
        return "thermal_transition"
    return "passive"


def _page_text_by_index(ctx: DocumentContext) -> dict[int, dict]:
    return {p["page_index"]: p for p in ctx.pages}


def _is_real(page_row: dict | None) -> bool:
    return bool(page_row) and page_row.get("content_status", "real") == "real"


class DeckExtractor:
    doc_kinds: tuple[str, ...] = DOC_KINDS

    def extract(self, ctx: DocumentContext) -> Extraction:
        ex = Extraction()
        doc = pymupdf.open(ctx.path)
        pages_by_index = _page_text_by_index(ctx)

        real_pages = {
            i + 1 for i in range(doc.page_count)
            if _is_real(pages_by_index.get(i + 1, {"content_status": "real"}))
        }

        self._emit_slide_nodes(ex, doc, pages_by_index, real_pages)
        self._emit_comfort_tiers(ex, doc, real_pages)
        self._emit_strategy_cards(ex, doc, real_pages)
        self._emit_benchmarks(ex, doc, real_pages)

        ex.stats = {
            "slides": doc.page_count,
            "image_only_slides": sum(
                1 for i in range(doc.page_count)
                if len(_clean(doc[i].get_text())) < _IMAGE_ONLY_CHAR_THRESHOLD
            ),
            "patterns": sum(1 for it in ex.items if it.item_type == "pattern"),
            "guidance": sum(1 for it in ex.items if it.item_type == "guidance"),
            "benchmarks": sum(1 for it in ex.items if it.item_type == "benchmark"),
        }
        doc.close()
        return ex

    # ── slide nodes + image-only flagging ────────────────────────────────

    def _emit_slide_nodes(self, ex: Extraction, doc, pages_by_index: dict, real_pages: set[int]) -> None:
        for i in range(doc.page_count):
            n = i + 1
            text = _clean(doc[i].get_text())
            title = self._guess_title(text)
            ex.nodes.append(Node(
                node_kind="slide", title=title, ordinal=n,
                page_from=n, page_to=n, ref=f"slide-{n}",
                text=text or None,
            ))
            if len(text) < _IMAGE_ONLY_CHAR_THRESHOLD:
                ex.warnings.append(
                    f"slide {n} is image-only ({len(text)} chars, "
                    f"{len(doc[i].get_images())} images) -- needs a vision pass; "
                    f"text layer only gives: {text!r}"
                )
            if n not in real_pages:
                ex.warnings.append(f"slide {n} content_status is not real -- skipped for items")

    @staticmethod
    def _guess_title(text: str) -> str | None:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.isdigit() and len(line) <= 60:
                return line
        return None

    # ── Tier 1-5 comfort ladder (pattern / comfort_tier) ─────────────────

    def _emit_comfort_tiers(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        measures_page = self._find_page(doc, "Strategic Approach")
        outcomes_page = self._find_page(doc, "Comfortable hours")
        if measures_page is None and outcomes_page is None:
            ex.warnings.append("comfort tier ladder not found in text layer")
            return

        tier_measures: dict[int, list[str]] = {}
        if measures_page is not None:
            tier_measures = self._tier_measures(doc[measures_page])

        tier_pct: dict[int, float] = {}
        baseline_pct: float | None = None
        if outcomes_page is not None:
            tier_pct, baseline_pct = self._tier_outcomes(doc[outcomes_page])

        tier_nums = sorted(set(tier_measures) | set(tier_pct)) or [1, 2, 3, 4, 5]
        prev_ref = None
        for n in tier_nums:
            cites = []
            if measures_page is not None and measures_page + 1 in real_pages:
                cites.append(Citation(page_index=measures_page + 1))
            if outcomes_page is not None and outcomes_page + 1 in real_pages:
                cites.append(Citation(page_index=outcomes_page + 1))
            if not cites:
                continue  # neither source slide is real -- do not fabricate the tier

            measures = tier_measures.get(n, [])
            attributes = {
                "measures": [{"name": m, "category": _measure_category(m)} for m in measures],
                "comfortable_hours_pct": tier_pct.get(n),
                "metric": "UTCI",
            }
            if n == min(tier_nums) and baseline_pct is not None:
                attributes["baseline_comfortable_hours_pct"] = baseline_pct

            ref = f"comfort-tier-{n}"
            ex.items.append(Item(
                item_type="pattern",
                title=f"Tier {n}",
                summary=(
                    f"Tier {n} comfort strategy: {len(measures)} accumulated measure(s); "
                    f"{tier_pct.get(n, 'unknown')}% comfortable hours (UTCI)."
                    if tier_pct.get(n) is not None else
                    f"Tier {n} comfort strategy: {len(measures)} accumulated measure(s)."
                ),
                payload={
                    "pattern_kind": "comfort_tier",
                    "code": f"Tier {n}",
                    "name": f"Tier {n}",
                    "parent_pattern_id": prev_ref,
                    "attributes": attributes,
                },
                citations=cites,
                ref=ref,
            ))
            prev_ref = ref

        if measures_page is not None and not tier_measures:
            ex.warnings.append("Strategic Approach slide found but no per-tier measures parsed")

    @staticmethod
    def _find_page(doc, *needles: str) -> int | None:
        for i in range(doc.page_count):
            t = doc[i].get_text()
            if all(needle.lower() in t.lower() for needle in needles):
                return i
        return None

    @staticmethod
    def _tier_measures(page) -> dict[int, list[str]]:
        """Column-cluster the slide's measure blocks against its 'Tier N'
        headers by x-position -- text order alone interleaves the columns."""
        words = page.get_text("words")  # x0,y0,x1,y1,text,block,line,word_no
        tier_x: dict[int, float] = {}
        i = 0
        while i < len(words) - 1:
            w = words[i]
            if w[4] == "Tier" and i + 1 < len(words) and words[i + 1][4].isdigit():
                n = int(words[i + 1][4])
                cx = (w[0] + words[i + 1][2]) / 2
                tier_x[n] = cx
                i += 2
                continue
            i += 1
        if not tier_x:
            return {}

        skip_re = re.compile(
            r"^(Strategic Approach|Passive Strategies|Active Strategies|"
            r"Thermal Transition)\s*$", re.I
        )
        blocks = page.get_text("blocks")
        # (y, x, text) per tier, so continuation lines (a wrapped phrase split
        # into its own block, e.g. "Solar Shading (building self-shading /" +
        # " static shading)") can be re-joined in reading order below.
        raw: dict[int, list[tuple[float, float, str]]] = {n: [] for n in tier_x}
        for b in blocks:
            text = _clean(b[4]).strip()
            if not text or skip_re.match(text) or text.isdigit():
                continue
            # header blocks sometimes merge two adjacent "Tier N" labels into
            # one block ("Tier 1 \nTier 2 \n") -- drop those entirely rather
            # than let them become a bogus measure on whichever tier is nearer
            if not re.sub(r"Tier\s*\d+", "", text, flags=re.I).strip():
                continue
            cx = (b[0] + b[2]) / 2
            nearest = min(tier_x, key=lambda n: abs(tier_x[n] - cx))
            name = " ".join(text.split())
            raw[nearest].append((b[1], b[0], name))

        result: dict[int, list[str]] = {}
        for n, items in raw.items():
            items.sort(key=lambda t: (round(t[0] / 5), t[1]))
            merged: list[str] = []
            for y, _x, text in items:
                if merged and text[:1].islower():
                    merged[-1] = f"{merged[-1]} {text}"
                elif text not in merged:
                    merged.append(text)
            if merged:
                result[n] = merged
        return result

    @staticmethod
    def _tier_outcomes(page) -> tuple[dict[int, float], float | None]:
        words = page.get_text("words")
        tier_y: dict[int, float] = {}
        i = 0
        while i < len(words) - 1:
            w = words[i]
            if w[4] == "Tier" and i + 1 < len(words) and words[i + 1][4].isdigit():
                n = int(words[i + 1][4])
                tier_y[n] = w[1]
                i += 2
                continue
            i += 1
        pct_words = sorted(
            (w for w in words if re.fullmatch(r"\d+%", w[4])), key=lambda w: w[1]
        )
        pct_values = [float(w[4].rstrip("%")) for w in pct_words]
        tiers_sorted = sorted(tier_y, key=lambda n: tier_y[n])
        baseline = None
        if len(pct_values) == len(tiers_sorted) + 1:
            baseline, pct_values = pct_values[0], pct_values[1:]
        pairing = dict(zip(tiers_sorted, pct_values))
        return pairing, baseline

    # ── named strategy cards (guidance) ──────────────────────────────────

    _STRATEGY_CARDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Street shading strategy", ("N-S aligned streets", "aligned to the NE")),
        ("Promenade shading strategy", ("N-S aligned Promenade", "E-W aligned promenade")),
        ("Respite zone strategy", ("Respite Zone Strategies", "Pocket park", "Shade refuge", "Shade Refuge")),
        ("Massing moves for street-level wind", ("Step down building heights", "Wider canyons", "Seaside edge canyons")),
    )

    def _emit_strategy_cards(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        for title, needles in self._STRATEGY_CARDS:
            matched_pages = [
                i for i in range(doc.page_count)
                if any(needle.lower() in doc[i].get_text().lower() for needle in needles)
            ]
            matched_pages = [i for i in matched_pages if i + 1 in real_pages]
            if not matched_pages:
                ex.warnings.append(f"strategy card {title!r} not found on any real slide")
                continue
            body = "\n\n".join(_clean(doc[i].get_text()) for i in matched_pages)
            ex.items.append(Item(
                item_type="guidance",
                title=title,
                statement=body[:500],
                payload={"body_md": body, "figure_ids": [], "legend_tokens": [], "disclaimer": None},
                citations=[Citation(page_index=i + 1) for i in matched_pages],
            ))

    # ── analysis-output benchmarks ────────────────────────────────────────

    @staticmethod
    def _sorted_block_text(page) -> str:
        """Blocks in visual (top-to-bottom, left-to-right) order rather than
        the PDF's internal drawing order -- native get_text() interleaves
        columns badly on these multi-option comparison slides, which pairs a
        label with the wrong neighbouring figure."""
        blocks = page.get_text("blocks")
        blocks = sorted(blocks, key=lambda b: (round(b[1] / 5), b[0]))
        return "\n\n".join(_clean(b[4]).strip() for b in blocks if _clean(b[4]).strip())

    def _emit_benchmarks(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        self._solar_radiation_benchmarks(ex, doc, real_pages)
        self._daylight_factor_benchmarks(ex, doc, real_pages)
        self._illuminance_benchmarks(ex, doc, real_pages)
        self._surface_temperature_benchmarks(ex, doc, real_pages)

    # Corner/legend words that sit immediately before a figure in reading
    # order on the ungrouped 3x3 comparison grid, but are not themselves the
    # name of the massing option that figure belongs to.
    _SOLAR_LABEL_DENYLIST = {"best", "worst", "mwh"}

    def _solar_radiation_benchmarks(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        pair_re = re.compile(r"([A-Za-z][A-Za-z0-9 /\-]{2,30}?)\s*\n\s*([\d,]+)\s*MWh")
        for i in range(doc.page_count):
            n = i + 1
            if n not in real_pages:
                continue
            text = self._sorted_block_text(doc[i])
            if "MWh" not in text:
                continue
            heading = text.splitlines()[0] if text else "Solar radiation"
            all_values = re.findall(r"([\d,]+)\s*MWh", text)
            good_pairs = [
                (label, value) for label, value in pair_re.findall(text)
                if label.strip().lower() not in self._SOLAR_LABEL_DENYLIST
            ]
            labelled_values = {value for _label, value in good_pairs}
            for label, value in good_pairs:
                ex.items.append(Item(
                    item_type="benchmark",
                    title=f"{label.strip()} -- {heading}",
                    payload={
                        "metric_id": "solar_radiation_annual",
                        "value_numeric": float(value.replace(",", "")),
                        "value_text": f"{value} MWh",
                        "unit_id": "mwh",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": None,
                    },
                    citations=[Citation(page_index=n)],
                ))
            unlabelled = [v for v in all_values if v not in labelled_values]
            if unlabelled:
                ex.items.append(Item(
                    item_type="benchmark",
                    title=f"{heading} (unlabelled options)",
                    payload={
                        "metric_id": "solar_radiation_annual",
                        "value_numeric": None,
                        "value_text": ", ".join(f"{v} MWh" for v in unlabelled),
                        "unit_id": "mwh",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": "option labels are graphic-only on this slide; values could not be "
                                       "matched to a named massing option from the text layer",
                    },
                    citations=[Citation(page_index=n)],
                ))
                ex.warnings.append(f"slide {n}: {len(unlabelled)} MWh values found with no adjacent text label")

    def _daylight_factor_benchmarks(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        row_re = re.compile(r"(Ground|[1-5])\s*\n\s*(\d\.\d+)\s*\n\s*([\d.]+)")
        total_re = re.compile(r"Total average\s*([\d.]+)\s*%")
        for i in range(doc.page_count):
            n = i + 1
            if n not in real_pages:
                continue
            text = doc[i].get_text()
            if "Daylight factor" not in text:
                continue
            for floor, df, area in row_re.findall(text):
                ex.items.append(Item(
                    item_type="benchmark",
                    title=f"Daylight factor -- floor {floor}",
                    payload={
                        "metric_id": "daylight_factor",
                        "value_numeric": float(df),
                        "value_text": f"{df}%",
                        "unit_id": "pct",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": f"area above 2% DF: {area}% of floor plate; 80% glazing assumption",
                    },
                    citations=[Citation(page_index=n)],
                ))
            m = total_re.search(text)
            if m:
                ex.items.append(Item(
                    item_type="benchmark",
                    title="Daylight factor -- total average",
                    payload={
                        "metric_id": "daylight_factor",
                        "value_numeric": float(m.group(1)),
                        "value_text": f"{m.group(1)} %",
                        "unit_id": "pct",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": "80% glazing assumption",
                    },
                    citations=[Citation(page_index=n)],
                ))

    def _illuminance_benchmarks(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        lux_re = re.compile(r"([<>]?\d[\d,]*(?:-\d[\d,]*)?)\s*lux")
        for i in range(doc.page_count):
            n = i + 1
            if n not in real_pages:
                continue
            text = doc[i].get_text()
            for value in lux_re.findall(text):
                ex.items.append(Item(
                    item_type="benchmark",
                    title=f"Illuminance threshold -- slide {n}",
                    payload={
                        "metric_id": "illuminance",
                        "value_numeric": None,
                        "value_text": f"{value} lux",
                        "unit_id": "lux",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": None,
                    },
                    citations=[Citation(page_index=n)],
                ))

    def _surface_temperature_benchmarks(self, ex: Extraction, doc, real_pages: set[int]) -> None:
        temp_re = re.compile(r"(\d+(?:-\d+)?)\s*[o°]C\b")
        for i in range(doc.page_count):
            n = i + 1
            if n not in real_pages:
                continue
            text = doc[i].get_text()
            if "surface temperature" not in text.lower():
                continue
            for value in temp_re.findall(text):
                is_range = "-" in value
                lo, hi = (value.split("-") if is_range else (value, value))
                ex.items.append(Item(
                    item_type="benchmark",
                    title=f"Surface temperature reduction -- slide {n}",
                    payload={
                        "metric_id": "surface_temperature_reduction",
                        "value_numeric": None if is_range else float(value),
                        "value_min": float(lo) if is_range else None,
                        "value_max": float(hi) if is_range else None,
                        "value_text": f"{value}°C",
                        "unit_id": "celsius",
                        "comparator": "none",
                        "is_placeholder": False,
                        "caveat_text": None,
                    },
                    citations=[Citation(page_index=n)],
                ))


DECK = DeckExtractor()
register(DECK)
