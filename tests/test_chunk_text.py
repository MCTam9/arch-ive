"""The composer that decides what a typed chunk says.

`compose` is a pure function over one database row, which is why it is worth
testing directly: it is the only thing standing between the typed tables and
what search can reach. The bug it exists to fix returned *nothing* for
"embodied carbon 2030" while the corpus held nine benchmarks answering it,
because the chunk text was the title alone.
"""
from __future__ import annotations

from tools.refresh_chunk_text import compose


def _benchmark(**over):
    row = {
        "item_type": "benchmark",
        "title": "Flats 2030 target",
        "statement": None,
        "summary": None,
        "metric_id": "upfront_embodied_carbon",
        "metric_name": "Upfront Embodied Carbon",
        "unit_symbol": "kgCO2e/m2GIA",
        "comparator": "none",
        "value_text": "380",
        "is_placeholder": False,
        "building_use_id": "flats",
        "target_year": 2030,
        "region_id": None,
        "standard_id": None,
        "standard_name": None,
        "caveat_text": None,
    }
    return {**row, **over}


def _requirement(**over):
    row = {
        "item_type": "requirement",
        "title": None,
        "statement": "BNG or equivalent target increased by 15%",
        "summary": None,
        "metric_id": None,
        "metric_name": None,
        "unit_symbol": "%",
        "comparator": "none",
        "target_text": "BNG or equivalent target increased by 15%",
        "deliverable_name": None,
        "criterion_code": "crib-biodiversity-3.1",
        "criterion_title": "Biodiversity Design Strategies",
        "level_code": "L4",
        "level_name": "EXEMPLAR PERFORMANCE",
    }
    return {**row, **over}


def test_benchmark_carries_metric_value_and_unit():
    text = compose(_benchmark())
    assert "Flats 2030 target" in text
    assert "Upfront Embodied Carbon" in text
    assert "380" in text
    assert "kgCO2e/m2GIA" in text


def test_facts_already_in_the_title_are_not_repeated():
    """Term frequency is a ranking signal. Restating 'flats' and '2030' under a
    label would push a row up the results for saying the same thing twice."""
    text = compose(_benchmark())
    assert text.lower().count("flats") == 1
    assert text.count("2030") == 1


def test_facts_absent_from_the_title_are_added():
    text = compose(_benchmark(title="Shell and core 2030 target",
                              building_use_id="office_shell_core"))
    assert "Building use: office shell core" in text


def test_slug_ids_are_split_into_words():
    """A slug is one token to the text-search parser, so 'office_shell_core'
    is unreachable by a search for 'office' until it is split."""
    text = compose(_benchmark(title="2030 target", building_use_id="office_shell_core"))
    assert "office shell core" in text
    assert "office_shell_core" not in text


def test_placeholder_value_is_not_indexed():
    """'X%' is the source sheet's blank, not a number. Naming the metric is
    useful; indexing the placeholder is noise."""
    text = compose(_benchmark(title="Target", value_text="X%", is_placeholder=True))
    assert "Upfront Embodied Carbon" in text
    assert "X%" not in text


def test_comparator_becomes_a_word():
    """'>=' does not survive to_tsvector and does not embed. 'at least' does."""
    text = compose(_benchmark(title="Target", comparator="gte", value_text="15"))
    assert "at least 15" in text


def test_requirement_gains_its_criterion_and_level():
    text = compose(_requirement())
    assert "Biodiversity Design Strategies" in text
    assert "EXEMPLAR PERFORMANCE" in text


def test_requirement_target_matching_the_statement_is_not_duplicated():
    text = compose(_requirement())
    assert text.count("BNG or equivalent target increased by 15%") == 1


def test_requirement_target_differing_from_the_statement_is_kept():
    text = compose(_requirement(statement="Meet the biodiversity target",
                                target_text="10% net gain", comparator="gte"))
    assert "at least 10% net gain" in text


def test_never_returns_empty():
    """chunk.text is NOT NULL, and an item can carry no title, no statement and
    no typed facts at all -- 48 of them did."""
    bare = {"item_type": "benchmark", "title": None, "statement": None, "summary": None}
    assert compose(bare) == "[benchmark]"
    assert compose({**bare, "summary": "a summary"}) == "a summary"
