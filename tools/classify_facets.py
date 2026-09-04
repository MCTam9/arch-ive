"""Rule-based facet classification: tags knowledge_item rows into item_term.

item_term is the single polymorphic join that makes every faceted query in
the web UI possible (see db/schema.sql). It starts out empty; this module
fills it, deterministically and idempotently, using two kinds of evidence:

  1. Structural signals already sitting in the typed subtype tables and the
     document/criterion hierarchy -- a benchmark's own metric_id, a crib
     sheet's own criterion code, a requirement's own rating_level ordinal,
     a pattern's own pattern_kind. These are `assigned_by='rule'` and get
     the higher confidence bands: this item did not need to be read to know
     it is about water, it came from the water crib sheet's water criterion.

  2. A keyword lexicon built from taxonomy_term.label + .synonyms (see
     db/seed_synonyms.sql, which was populated by reading the corpus's own
     vocabulary -- EUI, WWR, UTCI, BNG, "Building Level" and the rest).
     These are `assigned_by='model'` and score lower: a single keyword
     match in a long statement is a hint, not a fact.

Precision over recall throughout: an item gets no tag at all rather than a
tag resting on a weak signal. See workflows/classify_facets.md for how each
lookup table below was derived, and the rationale for every judgment call.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from tools import db

RULE = "rule"
MODEL = "model"

# Confidence bands. Never 1.0 -- even a "certain" structural rule reflects a
# human's read of the corpus, not ground truth, and a reviewer needs room to
# sort the merely-likely from the near-certain.
CONF_RULE_EXACT = 0.95   # this item's own typed column names the term
CONF_RULE_DOC = 0.75     # the whole document/framework implies the term
CONF_RULE_AMBIG = 0.55   # a structural rule that is genuinely a coin flip
CONF_MODEL_PHRASE = 0.55 # multi-word keyword/synonym match
CONF_MODEL_WORD = 0.40   # single-word keyword/synonym match

PLACEHOLDER_STATUSES = {"lorem", "template", "wip", "draft"}

# ---------------------------------------------------------------------------
# Structural lookup tables. Every key here was read off the live corpus
# (criterion codes, doc_node titles, benchmark.metric_id/building_use_id
# values, pattern_kind, rating_scale ladders) -- see workflows/classify_facets.md
# for the queries that produced each one. All keys are document slugs,
# criterion codes or column values, never a real name.
# ---------------------------------------------------------------------------

# crib sheet slug -> its single topic, the fallback once a more specific
# criterion-code override (below) has had a chance to fire.
CRIB_DOC_TOPIC: dict[str, str] = {
    "crib-water": "topic.water",
    "crib-embodied-carbon": "topic.embodied_carbon_circularity",
    "crib-operational-carbon": "topic.operational_carbon",
    "crib-climate-resilience": "topic.climate_resilience",
    "crib-biodiversity": "topic.biodiversity_pollution_land",
    "crib-health-wellbeing": "topic.health_wellbeing",
}

# document slug -> topic, for the one document that is entirely about one
# topic but is not a crib sheet (framework-vol-a10-smart-city: every doc_node
# under it is the smart city framework, confirmed by reading its outline).
WHOLE_DOC_TOPIC: dict[str, str] = {
    "framework-vol-a10-smart-city": "topic.smart_city_digital",
}

# criterion.code -> topic term(s), overriding the document default for the
# leaves that read differently from their sheet's headline topic (a "Indoor
# Air Quality" criterion inside the biodiversity crib sheet is health &
# wellbeing, not biodiversity). Looked up by progressively trimming the code
# ("crib-biodiversity-4.3" -> "crib-biodiversity-4" -> ...) so one entry
# covers a whole branch when the leaves don't need their own override.
# A tuple means the source is genuinely ambiguous between two topics.
CRITERION_TOPIC: dict[str, str | tuple[str, ...]] = {
    # masterplan framework: CC/NF/PC/RE (framework-vol-e1 doc_node titles)
    "CC1": "topic.urban_design_typology",
    "CC2": "topic.urban_design_typology",
    "CC2.3": "topic.smart_city_digital",       # "digital connections in public areas"
    "CC3": "topic.transport_mobility",
    "NF1": "topic.biodiversity_pollution_land.biodiversity",
    "NF2": "topic.biodiversity_pollution_land.biodiversity",
    "NF3": "topic.biodiversity_pollution_land.pollution",
    "PC1": "topic.health_wellbeing",
    "PC2": "topic.social_value",
    "PC2.3": "topic.climate_resilience",       # "design for resilience and adaptability"
    "PC3": "topic.social_value",
    "PC4": "topic.social_value",
    "RE1": "topic.water",
    "RE2": "topic.operational_carbon",
    "RE2.1": "topic.operational_carbon.passive_design",
    "RE2.3": "topic.operational_carbon.renewable_energy",
    "RE2.4": "topic.operational_carbon.active_systems",
    "RE3": "topic.embodied_carbon_circularity.materials_specification",
    "RE4": "topic.embodied_carbon_circularity.circular_design",
    "RE5": ("topic.operational_carbon", "topic.embodied_carbon_circularity"),  # "net zero carbon city" spans both

    # crib-biodiversity: the sheet is biodiversity_pollution_land throughout
    # except the leaves that name a different topic outright
    "crib-biodiversity-3.1": "topic.biodiversity_pollution_land.biodiversity",
    "crib-biodiversity-3.5": "topic.biodiversity_pollution_land.pollution",
    "crib-biodiversity-4.1": "topic.biodiversity_pollution_land.pollution",   # "Acoustic" (noise pollution)
    "crib-biodiversity-4.2": "topic.biodiversity_pollution_land.pollution",   # soil pollution
    "crib-biodiversity-4.3": "topic.health_wellbeing.air_quality",           # "Indoor Air Quality" -- not site pollution
    "crib-biodiversity-4.4": "topic.biodiversity_pollution_land.pollution",
    "crib-biodiversity-4.5": "topic.biodiversity_pollution_land.pollution",
    "crib-biodiversity-4.6": "topic.biodiversity_pollution_land.land_transformation",

    # crib-embodied-carbon: materials vs. circularity split
    "crib-embodied-carbon-3": "topic.embodied_carbon_circularity.materials_specification",
    "crib-embodied-carbon-3.1": "topic.embodied_carbon_circularity.materials_specification",
    "crib-embodied-carbon-3.2": "topic.embodied_carbon_circularity.circular_design",  # "Material reuse/recycle"
    "crib-embodied-carbon-3.3": "topic.embodied_carbon_circularity.materials_specification",  # EPD
    "crib-embodied-carbon-3.4": "topic.embodied_carbon_circularity.materials_specification",
    "crib-embodied-carbon-3.5": "topic.embodied_carbon_circularity.materials_specification",
    "crib-embodied-carbon-3.6": "topic.embodied_carbon_circularity.materials_specification",
    "crib-embodied-carbon-3.7": "topic.embodied_carbon_circularity.circular_design",
    "crib-embodied-carbon-4": "topic.embodied_carbon_circularity.circular_design",
    "crib-embodied-carbon-4.1": "topic.embodied_carbon_circularity.circular_design",
    "crib-embodied-carbon-4.2": "topic.embodied_carbon_circularity.circular_design",

    # crib-operational-carbon: passive design sub-branches
    "crib-operational-carbon-1.1": "topic.operational_carbon.passive_design",
    "crib-operational-carbon-1.3": "topic.operational_carbon.passive_design.building_fabric",
    "crib-operational-carbon-1.4": "topic.operational_carbon.passive_design.shading_glazing",
    "crib-operational-carbon-1.5": "topic.operational_carbon.passive_design",
    "crib-operational-carbon-1.6": "topic.operational_carbon.passive_design.building_fabric",
    "crib-operational-carbon-1.7": "topic.operational_carbon.passive_design.shading_glazing",  # "Glazing % (WWR)"
    "crib-operational-carbon-1.8": "topic.operational_carbon.passive_design.building_fabric",
    "crib-operational-carbon-2.1": "topic.operational_carbon.active_systems",
    "crib-operational-carbon-3.1": "topic.operational_carbon.renewable_energy",

    # crib-health-wellbeing: three topics share one sheet (module-6 in
    # private/documents.yaml is literally "Social Value and Transport")
    "crib-health-wellbeing-3.1": "topic.health_wellbeing.thermal_comfort",
    "crib-health-wellbeing-3.2": "topic.health_wellbeing.air_quality",
    "crib-health-wellbeing-3.3": "topic.health_wellbeing.acoustics",
    "crib-health-wellbeing-3.4": "topic.health_wellbeing.daylight_views",
    "crib-health-wellbeing-4": "topic.social_value",
    "crib-health-wellbeing-4.1": "topic.social_value",
    "crib-health-wellbeing-4.2": "topic.social_value",
    "crib-health-wellbeing-4.3": "topic.social_value",
    "crib-health-wellbeing-4.4": "topic.social_value",
    "crib-health-wellbeing-5": "topic.transport_mobility",
}

# benchmark.metric_id -> topic. Every benchmark row already carries a
# metric_id (write_extraction populated it in full: 128/128), so this alone
# resolves topic for every benchmark item with no text to read at all.
# None means the metric is a dimensional/spatial constant with no topic of
# its own (e.g. a minimum ceiling height belongs to no sustainability topic).
METRIC_TOPIC: dict[str, str | None] = {
    "balustrade_height": None,
    "biodiversity_net_gain": "topic.biodiversity_pollution_land.biodiversity",
    "ceiling_height_min": None,
    "co2_concentration": "topic.health_wellbeing.air_quality",
    "daylight_factor": "topic.health_wellbeing.daylight_views",
    "eui": "topic.operational_carbon",
    "far_by_storeys": "topic.urban_design_typology",
    "floor_to_floor_height_min": None,
    "form_factor": "topic.operational_carbon.passive_design.building_fabric",
    "illuminance": "topic.health_wellbeing.daylight_views",
    "page_goal_crib_biodiversity": "topic.biodiversity_pollution_land",
    "page_goal_crib_climate_resilience": "topic.climate_resilience",
    "page_goal_crib_embodied_carbon": "topic.embodied_carbon_circularity",
    "page_goal_crib_health_wellbeing": "topic.health_wellbeing",
    "page_goal_crib_operational_carbon": "topic.operational_carbon",
    "page_goal_crib_water": "topic.water",
    "plot_coverage_max": "topic.urban_design_typology",
    "pm25": "topic.health_wellbeing.air_quality",
    "potable_water_use": "topic.water.potable_water",
    "renewable_fraction": "topic.operational_carbon.renewable_energy",
    "reverberation_time": "topic.health_wellbeing.acoustics",
    "solar_radiation_annual": "topic.climate_resilience",
    "street_connectivity_index": "topic.transport_mobility",
    "surface_temperature_reduction": "topic.climate_resilience",
    "upfront_embodied_carbon": "topic.embodied_carbon_circularity",
    "urban_green_factor": "topic.biodiversity_pollution_land.biodiversity",
    "utci": "topic.climate_resilience",
    "voc": "topic.health_wellbeing.air_quality",
    "waste_diversion_rate": "topic.embodied_carbon_circularity.circular_design",
    "window_glazed_area_min": "topic.operational_carbon.passive_design.shading_glazing",
    "window_openable_min": "topic.health_wellbeing.thermal_comfort",
    "window_wall_ratio": "topic.operational_carbon.passive_design.shading_glazing",
}

# benchmark.building_use_id (a free-text token the extractor wrote, not a
# taxonomy id) -> the matching building_use term. One entry
# (f_b_retail_with_catering_without_catering) is deliberately absent: it is
# an extraction artefact that conflates two different uses under one column
# header, and guessing which one a given row means would be exactly the
# "hotel benchmark filed under Residential" mistake this task warns against.
BUILDING_USE_NORMALIZE: dict[str, str] = {
    "hotel": "building_use.hotel",
    "sport_leisure": "building_use.sport_leisure",
    "flats": "building_use.residential.flats",
    "science_tech": "building_use.science_tech",
    "culture_entertainment": "building_use.culture_entertainment",
    "general_offices": "building_use.office",
    "office_shell_core": "building_use.office.shell_core",
    "office_office_shell_core": "building_use.office.shell_core",
    "trading_floors": "building_use.trading_floors",
    "retail_dept_store": "building_use.retail.dept_store",
    "single_family_homes": "building_use.residential.single_family",
    "residential_single_family_homes": "building_use.residential.single_family",
    "retail": "building_use.retail",
    "without_catering": "building_use.f_and_b.without_catering",
}

# pattern.pattern_kind -> scale. A unit_type pattern (e.g. a 2-bed layout) is
# unit-scale by construction; a typology (e.g. a stacked-townhouse building
# type) is building-scale; an urban_cluster is neighbourhood-scale. persona
# and comfort_tier are not a design scale at all.
PATTERN_KIND_SCALE: dict[str, str] = {
    "unit_type": "scale.unit",
    "typology": "scale.building",
    "urban_cluster": "scale.neighbourhood_block",
}

# rating_scale.slug -> {rating_level.ordinal: level term}. The crib sheets'
# own four-rung ladder (L1..L4, the top rung literally labelled "EXEMPLAR
# PERFORMANCE") maps cleanly 1:1. The two masterplan-framework ladders have
# three graded rungs plus a "None" rung that means "not aligned" rather than
# "baseline ambition" -- that rung is omitted, not squeezed into baseline.
RATING_LEVEL_ORDINAL: dict[str, dict[int, str]] = {
    "crib-levels": {1: "level.baseline", 2: "level.enhanced", 3: "level.best_practice", 4: "level.exemplar"},
    "framework-targets": {1: "level.baseline", 2: "level.enhanced", 3: "level.exemplar"},
    "smart-city-alignment-ladder": {1: "level.baseline", 2: "level.enhanced", 3: "level.exemplar"},
    "smart-city-contribution": {2: "level.baseline", 3: "level.enhanced", 4: "level.exemplar"},
}

# source_document.doc_kind -> authority. The practice's own working documents
# (its crib sheets, its early-stage-design deck, its fee/cost calculators)
# are internal_practice; the commissioned, client-specific deliverables
# (the masterplan framework volumes, the typology catalogue) are
# client_framework. No document in this corpus cites an external_standard
# as its own authority -- BREEAM/LEED/RIBA appear only as things a handful
# of items reference in passing (see workflows/classify_facets.md).
DOC_KIND_AUTHORITY: dict[str, str] = {
    "crib_sheet": "authority.internal_practice",
    "deck": "authority.internal_practice",
    "calculator": "authority.internal_practice",
    "framework": "authority.client_framework",
    "implementation_plan": "authority.client_framework",
    "solutions_framework": "authority.client_framework",
    "guideline_report": "authority.client_framework",
}

# The three masterplan-framework volumes share one client project; CONTRACT.md
# already discloses its region as the Gulf (no more specific detail belongs
# in code). typology-multifamily is a different, unrelated commission with no
# disclosed region, so it is deliberately absent here.
GULF_DOC_SLUGS = {"framework-vol-e1", "framework-vol-e2", "framework-vol-a10-smart-city"}

# The whole multifamily typology catalogue is residential (its own title says
# so); it is not exclusively "flats" (it also covers stacked townhouses), so
# the safe default is the parent term, refined per-item by the keyword pass
# below when an item's own text says "single-family" or "flats".
TYPOLOGY_DOC_BUILDING_USE = {"typology-multifamily": "building_use.residential"}


def _trim_code(code: str) -> str | None:
    """One step toward the document root: 'crib-water-3.1' -> 'crib-water-3'
    -> None. Mirrors how the corpus's own codes nest (crib sheets) or how
    CC/NF/PC/RE decompose (masterplan framework)."""
    if "." in code:
        return code.rsplit(".", 1)[0]
    m = re.match(r"^(.*)-\d+$", code)
    return m.group(1) if m else None


def _topic_for_criterion(code: str | None) -> tuple[str, ...] | None:
    candidate = code
    while candidate:
        hit = CRITERION_TOPIC.get(candidate)
        if hit is not None:
            return (hit,) if isinstance(hit, str) else hit
        candidate = _trim_code(candidate)
    return None


# ---------------------------------------------------------------------------
# Keyword lexicon: taxonomy_term.label + .synonyms, loaded from the DB so the
# lexicon has one source of truth (db/seed_synonyms.sql) instead of drifting
# between SQL and Python. Fallback only -- consulted after every structural
# rule above has had its chance, and only for facets/items a structural rule
# did not already resolve.
# ---------------------------------------------------------------------------

_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _phrase_pattern(phrase: str) -> re.Pattern:
    pat = _PATTERN_CACHE.get(phrase)
    if pat is None:
        # words separated by literal whitespace in the phrase must match
        # across whatever whitespace run appears in the source text
        body = r"\s+".join(re.escape(w) for w in phrase.split())
        pat = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
        _PATTERN_CACHE[phrase] = pat
    return pat


@dataclass(frozen=True)
class _Term:
    id: str
    taxonomy_id: str
    phrases: tuple[str, ...]


def _load_terms(conn) -> list[_Term]:
    rows = db.all_rows(conn, "SELECT id, taxonomy_id, label, synonyms FROM taxonomy_term")
    terms = []
    for r in rows:
        phrases = tuple(p for p in [r["label"], *(r["synonyms"] or [])] if p)
        terms.append(_Term(r["id"], r["taxonomy_id"], phrases))
    return terms


def _keyword_hits(text: str, terms: list[_Term], taxonomy_id: str) -> list[tuple[str, float]]:
    """(term_id, confidence) for every term of `taxonomy_id` whose label or a
    synonym appears in `text` as a whole word/phrase. Confidence rewards a
    longer, more specific phrase over a single short word."""
    if not text:
        return []
    hits: list[tuple[str, float]] = []
    for term in terms:
        if term.taxonomy_id != taxonomy_id:
            continue
        best: float | None = None
        for phrase in term.phrases:
            if len(phrase) < 2:
                continue
            if _phrase_pattern(phrase).search(text):
                conf = CONF_MODEL_PHRASE if " " in phrase or "/" in phrase else CONF_MODEL_WORD
                best = conf if best is None else max(best, conf)
        if best is not None:
            hits.append((term.id, best))
    return hits


# ---------------------------------------------------------------------------
# Per-item classification
# ---------------------------------------------------------------------------


@dataclass
class _Tag:
    term_id: str
    confidence: float
    assigned_by: str


def _add(tags: dict[str, _Tag], term_id: str | None, confidence: float, assigned_by: str) -> None:
    if not term_id:
        return
    existing = tags.get(term_id)
    if existing is None or confidence > existing.confidence:
        tags[term_id] = _Tag(term_id, confidence, assigned_by)


def _classify_one(item: dict, terms: list[_Term]) -> list[_Tag]:
    tags: dict[str, _Tag] = {}
    text = " ".join(
        str(v) for v in (
            item["title"], item["statement"],
            item["criterion_title"], item["criterion_title_alt"],
            item["node_title"], item["node_title_alt"],
        ) if v
    )

    # ---- topic --------------------------------------------------------
    topic_hits = _topic_for_criterion(item["criterion_code"])
    if topic_hits is None and item["metric_id"]:
        mapped = METRIC_TOPIC.get(item["metric_id"])
        if mapped:
            topic_hits = (mapped,)
    structural_topic = topic_hits is not None
    if topic_hits is None:
        doc_topic = CRIB_DOC_TOPIC.get(item["doc_slug"]) or WHOLE_DOC_TOPIC.get(item["doc_slug"])
        if doc_topic:
            topic_hits = (doc_topic,)
            structural_topic = True
    if topic_hits:
        conf = CONF_RULE_AMBIG if len(topic_hits) > 1 else (
            CONF_RULE_EXACT if item["criterion_code"] or item["metric_id"] else CONF_RULE_DOC
        )
        for t in topic_hits:
            _add(tags, t, conf, RULE)
    # keyword topic hits only fill in when nothing structural fired, or add
    # a same-branch refinement (parent/child of what structural already gave)
    for term_id, conf in _keyword_hits(text, terms, "topic"):
        if not structural_topic or any(
            term_id.startswith(t.term_id) or t.term_id.startswith(term_id) for t in tags.values()
            if t.term_id.startswith("topic.")
        ):
            _add(tags, term_id, conf, MODEL)

    # ---- scale ----------------------------------------------------------
    scale_hit = PATTERN_KIND_SCALE.get(item["pattern_kind"]) if item["pattern_kind"] else None
    if scale_hit:
        _add(tags, scale_hit, CONF_RULE_EXACT, RULE)
    else:
        for term_id, conf in _keyword_hits(text, terms, "scale"):
            _add(tags, term_id, conf, MODEL)

    # ---- building_use -----------------------------------------------------
    bu_hit = BUILDING_USE_NORMALIZE.get(item["building_use_id"]) if item["building_use_id"] else None
    if bu_hit:
        _add(tags, bu_hit, CONF_RULE_EXACT, RULE)
    else:
        doc_bu = TYPOLOGY_DOC_BUILDING_USE.get(item["doc_slug"])
        if doc_bu:
            _add(tags, doc_bu, CONF_RULE_DOC, RULE)
        for term_id, conf in _keyword_hits(text, terms, "building_use"):
            _add(tags, term_id, conf, MODEL)

    # ---- level ------------------------------------------------------------
    level_hit = None
    if item["scale_slug"] and item["level_ordinal"] is not None:
        level_hit = RATING_LEVEL_ORDINAL.get(item["scale_slug"], {}).get(item["level_ordinal"])
    if level_hit:
        _add(tags, level_hit, CONF_RULE_EXACT, RULE)
    else:
        for term_id, conf in _keyword_hits(text, terms, "level"):
            _add(tags, term_id, conf, MODEL)

    # ---- discipline, region, project_type, stage: keyword only ------------
    for taxonomy_id in ("discipline", "region", "project_type", "stage"):
        for term_id, conf in _keyword_hits(text, terms, taxonomy_id):
            _add(tags, term_id, conf, MODEL)
    if item["doc_slug"] in GULF_DOC_SLUGS:
        _add(tags, "region.geography.gulf", CONF_RULE_DOC, RULE)

    # ---- authority ----------------------------------------------------
    auth_hit = DOC_KIND_AUTHORITY.get(item["doc_kind"])
    if auth_hit:
        _add(tags, auth_hit, CONF_RULE_DOC, RULE)

    return list(tags.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_ITEM_QUERY = """
    SELECT
        ki.id, ki.item_type, ki.title, ki.statement, ki.content_status,
        d.slug AS doc_slug, d.doc_kind::text AS doc_kind,
        n.title AS node_title, n.title_alt AS node_title_alt,
        c.code AS criterion_code, c.title_primary AS criterion_title,
        c.title_alt AS criterion_title_alt,
        rl.ordinal AS level_ordinal, rs.slug AS scale_slug,
        b.metric_id, b.building_use_id,
        p.pattern_kind
    FROM knowledge_item ki
    JOIN source_document d ON d.id = ki.document_id
    LEFT JOIN doc_node n ON n.id = ki.node_id
    LEFT JOIN requirement r ON r.knowledge_item_id = ki.id
    LEFT JOIN criterion c ON c.id = r.criterion_id
    LEFT JOIN rating_level rl ON rl.id = r.rating_level_id
    LEFT JOIN rating_scale rs ON rs.id = rl.scale_id
    LEFT JOIN benchmark b ON b.knowledge_item_id = ki.id
    LEFT JOIN pattern p ON p.knowledge_item_id = ki.id
    {where}
    ORDER BY ki.id
"""


def classify_items(conn, document_id: str | None = None) -> dict:
    """Tag knowledge items into item_term. Returns counts. Idempotent: for
    every item in scope, replaces its current tag set with the freshly
    computed one (deletes what a prior run wrote, then reinserts) rather than
    accumulating -- safe because item_term has no other writer."""
    where = "WHERE ki.document_id = %s" if document_id else ""
    params = (document_id,) if document_id else ()
    items = db.all_rows(conn, _ITEM_QUERY.format(where=where), params)
    terms = _load_terms(conn)

    by_taxonomy: dict[str, int] = {}
    tagged_items = 0
    total_tags = 0
    skipped_placeholder = 0
    item_ids = [i["id"] for i in items]

    plan: list[tuple[str, str, float, str]] = []  # (item_id, term_id, confidence, assigned_by)
    for item in items:
        if item["content_status"] in PLACEHOLDER_STATUSES:
            # Placeholder statements are not something to file a fact under a
            # real facet -- see workflow doc for why this branch is currently
            # a no-op against the live corpus (every knowledge_item here is
            # 'real'; the flag exists for whatever ingests next).
            skipped_placeholder += 1
            continue
        item_tags = _classify_one(item, terms)
        if item_tags:
            tagged_items += 1
        for tag in item_tags:
            plan.append((item["id"], tag.term_id, tag.confidence, tag.assigned_by))
            total_tags += 1
            taxonomy_id = tag.term_id.split(".", 1)[0]
            by_taxonomy[taxonomy_id] = by_taxonomy.get(taxonomy_id, 0) + 1

    if item_ids:
        conn.execute(
            "DELETE FROM item_term WHERE knowledge_item_id = ANY(%s)",
            (item_ids,),
        )
    for item_id, term_id, confidence, assigned_by in plan:
        conn.execute(
            "INSERT INTO item_term (knowledge_item_id, term_id, confidence, assigned_by) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (knowledge_item_id, term_id) DO UPDATE "
            "SET confidence = EXCLUDED.confidence, assigned_by = EXCLUDED.assigned_by",
            (item_id, term_id, confidence, assigned_by),
        )

    return {
        "items_considered": len(items),
        "items_skipped_placeholder": skipped_placeholder,
        "items_tagged": tagged_items,
        "items_untagged": len(items) - skipped_placeholder - tagged_items,
        "tags_written": total_tags,
        "by_taxonomy": by_taxonomy,
    }


if __name__ == "__main__":
    doc_id = sys.argv[1] if len(sys.argv) > 1 else None
    with db.transaction() as _conn:
        result = classify_items(_conn, doc_id)
    print(f"classify_facets: {result['items_tagged']}/{result['items_considered']} items tagged "
          f"({result['tags_written']} tags written, {result['items_untagged']} untagged, "
          f"{result['items_skipped_placeholder']} placeholder skipped)")
    for taxonomy_id, count in sorted(result["by_taxonomy"].items()):
        print(f"  {taxonomy_id:<14} {count}")
