"""Tests for extractors/support.py -- the extraction rules CONTRACT.md states
once and the extractors used to state seven times.

Pure functions only: no database, no fixture PDF, no corpus. Run directly:
    ./.venv/bin/python -m pytest tests/test_extractor_support.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from extractors import support


def test_module_registers_nothing():
    # pipeline.load_extractors() imports every module in extractors/ on every
    # run, so an import side effect here would fire on every ingest.
    from tools import pipeline
    before = dict(pipeline._REGISTRY)
    import importlib
    importlib.reload(support)
    assert dict(pipeline._REGISTRY) == before
    assert not hasattr(support, "doc_kinds")


# ── the two slugify behaviours ───────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Residential", "residential"),
    ("  Mixed Use / Office  ", "mixed_use_office"),
    ("kgCO2e/m2GIA", "kgco2e_m2gia"),
    ("", "x"),
    ("///", "x"),
])
def test_slugify_never_returns_empty(text, expected):
    assert support.slugify(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Residential", "residential"),
    ("", None),
    ("   ", None),
    ("///", None),
    (None, None),
])
def test_slugify_or_none_keeps_empty_empty(text, expected):
    assert support.slugify_or_none(text) == expected


def test_the_two_slugifies_differ_only_on_empty():
    # spreadsheet.py feeds `slugify_or_none(label) or <sheet>_<cell>`; an 'x'
    # here would win that `or` and collide every unlabelled parameter.
    assert support.slugify("") == "x"
    assert support.slugify_or_none("") is None


def test_clean_collapses_newlines_and_runs():
    assert support.clean("  a\n\n b\t c ") == "a b c"
    assert support.clean(None) == ""
    assert support.clean("") == ""


# ── the placeholder rule (CONTRACT.md: 'X%' / 'Xkm' / 'X no of') ──────────

@pytest.mark.parametrize("text", [
    "X%", "X %", "Xkm", "X km of cycle lane", "X no of trees", "x no. of units",
    "X% of 2020 baseline",
])
def test_placeholder_values_are_flagged(text):
    assert support.is_placeholder_value(text) is True


@pytest.mark.parametrize("text", [
    "10%", "700-800ppm", "", None, "Xylem pump count", "no of units",
])
def test_real_values_are_not_flagged(text):
    assert support.is_placeholder_value(text) is False


def test_a_placeholder_parses_no_number():
    # 'X% of 2020 baseline' contains a number that is not its value.
    assert support.parse_value("X% of 2020 baseline").parsed_ok is False


# ── parse_value ──────────────────────────────────────────────────────────

def test_a_range_keeps_both_bounds():
    # the whole point of the shared parser: crib_sheet found both bounds and
    # returned neither, so benchmark.value_min / value_max were never written.
    p = support.parse_value("700-800ppm")
    assert (p.minimum, p.maximum) == (700.0, 800.0)
    assert p.numeric is None          # no single number is the value
    assert p.comparator == "range"
    assert p.unit_id == "ppm"
    assert p.parsed_ok is True


def test_a_range_needs_a_boundary_before_it():
    # a hyphen inside a strategy code is not a range
    p = support.parse_value("NF1.2-3 applies")
    assert p.comparator != "range"


@pytest.mark.parametrize("text,numeric,comparator,unit", [
    ("<900 ppm", 900.0, "lte", "ppm"),
    ("≤ 35", 35.0, "lte", None),
    (">15%", 15.0, "gte", "pct"),
    ("≥ 10 years", 10.0, "gte", "yr"),
    ("10%", 10.0, "none", "pct"),
    ("125 ppm", 125.0, "none", "ppm"),
    ("800", 800.0, "none", None),
    ("-2.5", -2.5, "none", None),
])
def test_scalar_forms(text, numeric, comparator, unit):
    p = support.parse_value(text)
    assert p.parsed_ok is True
    assert p.numeric == numeric
    assert p.comparator == comparator
    assert p.unit_id == unit
    assert (p.minimum, p.maximum) == (None, None)


@pytest.mark.parametrize("text", [
    "N/A", "Y", "", "Best practice", "800 (primary) 400 (secondary)",
])
def test_unparseable_values_report_so(text):
    p = support.parse_value(text)
    assert p.parsed_ok is False
    assert p.numeric is None


def test_an_asterisk_becomes_a_caveat_not_a_lost_digit():
    p = support.parse_value("0.4*")
    assert p.numeric == 0.4
    assert p.caveat_text and "asterisk" in p.caveat_text


def test_context_supplies_unit_and_comparator_the_cell_does_not():
    # the compliance appendices put the number in one column and its unit and
    # comparator in the requirement sentence beside it.
    p = support.parse_value("15", context="Minimum green cover (%) of site area")
    assert (p.numeric, p.unit_id, p.comparator) == (15.0, "pct", "gte")
    assert support.parse_value("15", context="Maximum overshadowing (%)").comparator == "lte"


def test_the_cell_wins_over_the_context():
    # the cell says 'at most'; the sentence beside it says 'Minimum'. What the
    # value itself states is the one that is about this value.
    p = support.parse_value("<15 ppm", context="Minimum something (m)")
    assert p.comparator == "lte"
    assert p.unit_id == "ppm"


def test_context_unit_survives_an_unparseable_value():
    # 'N/A' against a '(%)' requirement is still an answer about a percentage
    p = support.parse_value("N/A", context="Cycle parking provision (%)")
    assert p.parsed_ok is False
    assert p.unit_id == "pct"


def test_square_metres_do_not_collapse_onto_metres():
    assert support.lookup_unit("m²") == ("m2", "m2")
    assert support.lookup_unit("m") == ("m", "m")
    assert support.lookup_unit("dBA") == ("db", "dBA")
    assert support.lookup_unit("") is None


# ── page_is_real ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    ({"content_status": "real"}, True),
    ({"page_index": 7}, True),                    # the column defaults to 'real'
    ({"content_status": "lorem"}, False),
    ({"content_status": "wip"}, False),
    ({"content_status": "template"}, False),
    (None, False),                                # a page we have no row for
    ({}, False),                                  # nor is an empty dict one
])
def test_page_is_real(row, expected):
    assert support.page_is_real(row) is expected
