"""Rules from CONTRACT.md that more than one extractor needs.

Extractors may import only `tools.pipeline`, so until this module existed each
of the seven carried its own copy of the contract's extraction rules -- and the
copies had drifted. `is_placeholder` (CONTRACT.md: 'set is_placeholder for X% /
Xkm / X no of') was implemented once and hardcoded to False at eight other
benchmark-emitting sites; the value parser was written twice, and the version
that found both bounds of a range threw them away instead of writing
value_min/value_max.

This module is *not* an extractor: it registers nothing and must stay
import-safe with zero side effects, because `pipeline.load_extractors()`
imports every module in this package on every pipeline run.

Deliberately not consolidated here:
  - `deck._clean` strips a copyright footer. It shares a name with the
    whitespace `clean()` below and nothing else.
  - the two lorem detectors (`tools/ingest_document.py` vs `generic.py`) are
    incompatible, and merging them changes ingest classification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NUM = r"-?\d+(?:\.\d+)?"

# free-text unit -> (unit_id, symbol). Anything unseen but symbol-shaped gets a
# slugified ad-hoc id; the writer autocreates the `unit` row either way.
# 'm'/'km'/'m2'/'m²' are spelled out rather than left to the slug fallback,
# which would turn 'm²' into 'm' and quietly conflate area with length.
UNIT_ALIASES: dict[str, tuple[str, str]] = {
    "l/p/day": ("lpd", "l/p/day"),
    "ppm": ("ppm", "ppm"),
    "ppb": ("ppb", "ppb"),
    "%": ("pct", "%"),
    "m": ("m", "m"),
    "km": ("km", "km"),
    "m2": ("m2", "m2"),
    "m²": ("m2", "m2"),
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

# Five of the aliases above contain a digit or a '.' ('kgCO2e/m2GIA',
# 'kgCO2e/kg', 'µg/m3', 'mg/m3', 'kWh/m2.year'), so a unit token made only of
# letters and symbols truncated them ('kgCO' ...) and lookup_unit's slug
# fallback minted a junk unit id for the stump. The token therefore has to
# admit digits and '.', and every pattern below places it directly after a
# number -- so the whole difficulty is admitting them without letting the unit
# start on, or reach into, a neighbouring number:
#   - the FIRST character is never a digit and never '.'. A unit is
#     'kgCO2e/m2GIA', never '2050' and never '.5', so '700 - 800 2050' still
#     parses as a bare range and '< 40 2030' cannot read the year as a unit.
#     It is also what stops a range's second bound being eaten: the token can
#     only begin where NUM has already stopped, and NUM is greedy.
#   - whitespace is outside both classes, so a token ends at the space:
#     '30 m 2050' yields 'm', not 'm 2050'. No \b needed -- the class is the
#     boundary.
#   - '.' is consumed only when a unit-initial character follows it, which
#     keeps the '.year' of 'kWh/m2.year' while leaving a sentence-final stop
#     ('40%.', '< 30 m.') and any decimal point outside the unit.
# '/' is admitted after the first character only: a leading '/' is not a unit,
# and letting it match would slug to 'x' and mint that as a unit id.
_UNIT_HEAD = r"[%a-zA-Zµ°²·]"
_UNIT_TAIL = r"[%a-zA-Z0-9µ°²·/]"
_UNIT_CORE = rf"{_UNIT_HEAD}(?:{_UNIT_TAIL}|\.(?={_UNIT_HEAD}))*"
# optional, because a comparator or range often states no unit at all ('≤ 35')
_UNIT_TOKEN = rf"(?:{_UNIT_CORE})?"
_RANGE_RE = re.compile(rf"({NUM})\s*-\s*({NUM})\s*({_UNIT_TOKEN})")
_LTE_RE = re.compile(rf"[<≤]\s*({NUM})\s*({_UNIT_TOKEN})")
_GTE_RE = re.compile(rf"[>≥]\s*({NUM})\s*({_UNIT_TOKEN})")
_PCT_RE = re.compile(rf"({NUM})\s*%")
# same token, but required: this one is fullmatched against the whole cell, so
# an empty unit here would just duplicate _BARE_NUM_RE.
_NUM_UNIT_RE = re.compile(rf"({NUM})\s*({_UNIT_CORE})")
_BARE_NUM_RE = re.compile(NUM)
_PARENTHESISED_UNIT_RE = re.compile(r"\((%|m|km|m2|m²|dB|dBA)\)")
_PLACEHOLDER_RE = re.compile(r"^X\s*(%|km|no)", re.I)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")

ASTERISK_CAVEAT = ("footnoted with an asterisk on the source page; footnote text not "
                   "positionally linked by this extractor")


def slugify(text: str) -> str:
    """Slug, or `'x'` when nothing survives. The id-shaped variant: callers use
    the result directly as a `building_use_id` or a `requirement_scope.code`,
    which are NOT NULL."""
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_") or "x"


def slugify_or_none(text: str | None) -> str | None:
    """Same slug, but empty stays empty. `spreadsheet.py` depends on the None:
    it feeds `slugify_or_none(label) or <sheet>_<cell>`, so an `'x'` here would
    win that `or` and every unlabelled parameter on a sheet would collide on
    one name."""
    if not text:
        return None
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_") or None


def clean(text: str | None) -> str:
    """Collapse all whitespace, including the newlines pymupdf leaves inside a
    table cell. `\\s+` already covers those, so no separate newline pass."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def is_placeholder_value(text: str | None) -> bool:
    """CONTRACT.md: 'X%' / 'Xkm' / 'X no of' are the corpus' way of writing a
    target the author had not filled in. They must be flagged, never ingested
    as a number -- a benchmark of 'X%' read as a fact is this system's worst
    failure mode."""
    return bool(text) and bool(_PLACEHOLDER_RE.match(text.strip()))


def page_is_real(page_row: dict | None) -> bool:
    """`content_status` is set by ingest before any extractor runs; extractors
    only obey it. A missing row is not evidence of anything, so it is not
    real."""
    return bool(page_row) and page_row.get("content_status", "real") == "real"


def normalise_unit(raw: str) -> str | None:
    key = raw.strip().lower().replace(" ", "")
    return key or None


def lookup_unit(raw: str) -> tuple[str, str] | None:
    """(unit_id, symbol) for a free-text unit token, or None if it does not look
    like a unit at all."""
    key = normalise_unit(raw)
    if not key:
        return None
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    if re.fullmatch(r"[a-z0-9/.%µ°²·-]+", key):
        return (slugify(key), raw.strip())
    return None


@dataclass(frozen=True)
class ParsedValue:
    """What a verbatim target string yields. The caller always keeps the
    verbatim string too -- `value_text` and `target_text` are NOT NULL."""
    numeric: float | None
    minimum: float | None
    maximum: float | None
    comparator: str          # one of db/schema.sql's `comparator` enum values
    unit_id: str | None
    unit_symbol: str | None
    caveat_text: str | None
    parsed_ok: bool


def parse_value(text: str, *, context: str | None = None) -> ParsedValue:
    """Best-effort numeric parse of a verbatim target, per CONTRACT.md's
    extraction rules: a comparator from `<`/`≤`/`>`/`≥`, both bounds of a range
    into min/max, a trailing asterisk into `caveat_text`.

    `context` is the surrounding requirement sentence, for the compliance
    tables where the value cell holds a bare number and the unit ('... (%)')
    and comparator ('Minimum ...') are stated in the prose beside it. Anything
    found in `text` itself wins over the context.
    """
    t = text.strip()
    caveat = None
    if t.endswith("*"):
        caveat = ASTERISK_CAVEAT
        t = t[:-1].strip()

    parsed = _parse_number(t)
    if parsed is None:
        # still worth reporting the caveat and any context unit: 'N/A' against
        # a '... (%)' requirement is a real, if unparseable, answer.
        return ParsedValue(None, None, None, "none", *_context_unit(context), caveat, False)

    numeric, minimum, maximum, comparator, unit = parsed
    if unit is None:
        unit = _context_unit_pair(context)
    if comparator == "none" and context:
        comparator = comparator_from_words(context)
    return ParsedValue(numeric, minimum, maximum, comparator,
                        unit[0] if unit else None, unit[1] if unit else None,
                        caveat, True)


def comparator_from_words(text: str) -> str:
    """'Minimum 15% of ...' states its comparator in words, not symbols."""
    low = text.lower()
    if low.startswith("minimum") or " minimum " in low:
        return "gte"
    if low.startswith("maximum") or " maximum " in low:
        return "lte"
    return "none"


def _context_unit_pair(context: str | None) -> tuple[str, str] | None:
    if not context:
        return None
    m = _PARENTHESISED_UNIT_RE.search(context)
    return lookup_unit(m.group(1)) if m else None


def _context_unit(context: str | None) -> tuple[str | None, str | None]:
    unit = _context_unit_pair(context)
    return (unit[0], unit[1]) if unit else (None, None)


def _parse_number(t: str) -> tuple[float | None, float | None, float | None,
                                    str, tuple[str, str] | None] | None:
    """(numeric, min, max, comparator, unit) or None if nothing parsed."""
    m = _RANGE_RE.search(t)
    # a hyphen inside a code ('NF1.2-3') or a word is not a range; only a
    # match that does not continue an alphanumeric run is one.
    if m and (m.start() == 0 or not t[m.start() - 1].isalnum()):
        return (None, float(m.group(1)), float(m.group(2)), "range", _unit_or_none(m.group(3)))

    for pattern, comparator in ((_LTE_RE, "lte"), (_GTE_RE, "gte")):
        m = pattern.search(t)
        if m:
            return (float(m.group(1)), None, None, comparator, _unit_or_none(m.group(2)))

    m = _PCT_RE.search(t)
    if m:
        return (float(m.group(1)), None, None, "none", UNIT_ALIASES["%"])

    m = _NUM_UNIT_RE.fullmatch(t)
    if m:
        return (float(m.group(1)), None, None, "none", _unit_or_none(m.group(2)))

    if _BARE_NUM_RE.fullmatch(t):
        return (float(t), None, None, "none", None)

    return None


def _unit_or_none(raw: str) -> tuple[str, str] | None:
    return lookup_unit(raw) if raw else None


# ── placeholder pages ────────────────────────────────────────────────────

# The vocabulary shared by the classic 69-word Lorem Ipsum paragraph and the
# longer "De Finibus" source text that generators (including InDesign's
# built-in filler) draw random sentences from. A page is flagged on the
# *fraction* of its words drawn from this set, not on any single phrase --
# this corpus's filler text never contains "lorem" or "ipsum" itself.
_LATIN_STOPWORDS = frozenset("""
a ab ad adipisci adipiscing alias aliquam aliquid aliquip amet animi
aperiam architecto asperiores aspernatur assumenda at atque aut autem
beatae blanditiis cillum commodi commodo consectetur consequatur consequat
consequuntur corporis corrupti culpa cum cumque cupiditate cupidatat
debitis delectus deleniti deserunt dicta dignissimos distinctio
do dolor dolore dolorem doloremque dolores doloribus dolorum duis
ducimus ea eaque earum eius eiusmod eligendi elit enim eos
error esse est et eu eum eveniet ex excepteur excepturi exercitation
exercitationem expedita explicabo facere facilis fuga fugiat fugit harum
hic id illo illum impedit in incididunt incidunt inventore ipsa ipsam
ipsum irure iste itaque iure iusto labore laboris laboriosam laborum
laudantium libero lorem magna magnam magni maiores maxime minim minima
minus modi molestiae molestias mollit mollitia natus necessitatibus nemo
neque nesciunt nihil nisi nobis non nostrud nostrum nulla numquam
occaecat occaecati odio odit officia officiis omnis optio pariatur
perferendis perspiciatis placeat porro possimus praesentium proident
provident quae quaerat quam quas quasi qui quia quibusdam quidem
quis quisquam quo quod quos ratione recusandae reiciendis rem repellat
repellendus reprehenderit repudiandae rerum saepe sapiente sed sequi
similique sint sit soluta sunt suscipit tempor tempora tempore
temporibus tenetur totam ullam ullamco unde ut vel velit veniam
veritatis vero vitae voluptas voluptate voluptatem voluptates voluptatibus
voluptatum
""".split())

_LOREM_MIN_WORDS = 150
_LOREM_FRACTION = 0.12

_TEMPLATE_ONLY_RE = re.compile(r"template\s*\n?\s*only", re.IGNORECASE)
_WIP_RE = re.compile(r"(?<!\w)WIP(?!\w)")
# the literal phrase, for the generators (unlike this corpus's own filler)
# that do spell it out -- a page can fall under the 150-word floor the
# fraction rule needs and still be unambiguously lorem ipsum.
_LOREM_STAMP_RE = re.compile(r"lorem\s+ipsum", re.I)
_WORD_RE = re.compile(r"[a-z]+")


def page_status_from_text(text: str) -> str:
    """The `content_status` a page's own text argues for.

    wip > template > lorem > real -- an explicit stamp beats a fuzzy match.

    Two callers, which is why it lives here rather than in either of them.
    `tools/ingest_document.py` runs it once per page at ingest and stores the
    answer on `source_page`. `extractors/generic.py` runs it again over the
    same text, because a page carrying `content_status = 'real'` that says WIP
    is a document that ingest did not see, and that extractor is the one place
    a bad document cannot be allowed to look fine -- serving placeholder text
    as guidance is, per CONTRACT.md, this system's worst failure mode.

    The second look used to be a *different*, weaker rule living in
    `generic.py`: three of six stock Latin tokens, and no word-fraction test.
    Two rules for one question is how a corpus ends up with two answers. This
    is that rule, once, and both callers reach it.
    """
    if _WIP_RE.search(text):
        return "wip"
    if _TEMPLATE_ONLY_RE.search(text):
        return "template"
    if _LOREM_STAMP_RE.search(text):
        return "lorem"
    words = _WORD_RE.findall(text.lower())
    if len(words) >= _LOREM_MIN_WORDS:
        hits = sum(1 for w in words if w in _LATIN_STOPWORDS)
        if hits / len(words) > _LOREM_FRACTION:
            return "lorem"
    return "real"
