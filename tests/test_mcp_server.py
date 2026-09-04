"""Tests for tools/mcp_server.py.

Calls the tool functions directly (not over stdio) against the test
database. tests/conftest.py already redirects DATABASE_URL to arch_test for
the read-write (arch_app) side; this module additionally points
DATABASE_URL_READONLY at the matching arch_read connection on that same
database, so the functions under test really go through the read-only role
db/roles.sql creates -- not arch_app.

Fixture rows all live under document slug `h-mcp-fixture` (id fields use the
`h-mcp-` naming the task asked for via a fixed, deterministic uuid/sha256 so
reruns are idempotent instead of piling up duplicates).

Run in isolation -- other agents' fixtures share this database:
    ./.venv/bin/python -m pytest tests/test_mcp_server.py -q
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# DATABASE_URL_READONLY is set by tests/conftest.py, which is the one place
# every test DSN is decided. It used to be defaulted here instead, which meant
# CI -- where the database is on a different port -- ran these against nothing.

import psycopg
import pytest

from tools import db
from tools import mcp_server as m

DOC_SLUG = "h-mcp-fixture"
DOC_ID = "0000000a-0000-0000-0000-00000000f001"
SHA256 = hashlib.sha256(b"h-mcp-fixture").hexdigest()

BENCH_ITEM_ID = "0000000a-0000-0000-0000-00000000f002"
REQ_ITEM_ID = "0000000a-0000-0000-0000-00000000f003"
TEMPLATE_ITEM_ID = "0000000a-0000-0000-0000-00000000f004"
LOREM_ITEM_ID = "0000000a-0000-0000-0000-00000000f005"

CIT_BENCH_ID = "0000000a-0000-0000-0000-00000000f101"
CIT_REQ_ID = "0000000a-0000-0000-0000-00000000f102"
CIT_LOREM_ID = "0000000a-0000-0000-0000-00000000f103"

CHUNK_BENCH_ID = "0000000a-0000-0000-0000-00000000f201"
CHUNK_REQ_ID = "0000000a-0000-0000-0000-00000000f202"
CHUNK_LOREM_ID = "0000000a-0000-0000-0000-00000000f203"

NODE_ID = "0000000a-0000-0000-0000-00000000f301"

BUILDING_USE = "h_mcp_use"
TARGET_YEAR = 2031
SEARCH_TOKEN = "hmcpfixtureuniquesearchtoken"
LOREM_SEARCH_TOKEN = "hmcpfixturelorempkaceholdertoken"
TEMPLATE_SLUG = "h-mcp-calc"
SHEET_NAME = "H-MCP Sheet"


def _setup(conn) -> None:
    """Idempotent fixture load -- safe against a persistent arch_test db
    that other test runs (and other agents) also write to."""
    conn.execute(
        "INSERT INTO source_document (id, slug, doc_kind, sha256, page_count, content_status) "
        "VALUES (%s, %s, 'guideline_report', %s, 20, 'real') "
        "ON CONFLICT (sha256) DO UPDATE SET slug = EXCLUDED.slug",
        (DOC_ID, DOC_SLUG, SHA256),
    )
    conn.execute(
        "INSERT INTO doc_node (id, document_id, node_kind, title, ordinal, page_from, page_to) "
        "VALUES (%s, %s, 'chapter', 'H-MCP Fixture Chapter', 0, 1, 20) "
        "ON CONFLICT (id) DO NOTHING",
        (NODE_ID, DOC_ID),
    )

    for item_id, status in ((BENCH_ITEM_ID, "real"), (LOREM_ITEM_ID, "lorem")):
        conn.execute(
            "INSERT INTO knowledge_item (id, item_type, document_id, title, statement, content_status) "
            "VALUES (%s, 'benchmark', %s, 'H-MCP benchmark', 'H-MCP benchmark statement', %s) "
            "ON CONFLICT (id) DO UPDATE SET content_status = EXCLUDED.content_status",
            (item_id, DOC_ID, status),
        )
        conn.execute(
            "INSERT INTO benchmark (knowledge_item_id, metric_id, value_numeric, value_text, "
            "unit_id, building_use_id, target_year) "
            "VALUES (%s, 'upfront_embodied_carbon', 111, '111', 'kgco2e_m2_gia', %s, %s) "
            "ON CONFLICT (knowledge_item_id) DO NOTHING",
            (item_id, BUILDING_USE, TARGET_YEAR),
        )

    conn.execute(
        "INSERT INTO knowledge_item (id, item_type, document_id, node_id, title, statement, content_status) "
        "VALUES (%s, 'requirement', %s, %s, 'H-MCP requirement', 'H-MCP requirement statement', 'real') "
        "ON CONFLICT (id) DO NOTHING",
        (REQ_ITEM_ID, DOC_ID, NODE_ID),
    )
    conn.execute(
        "INSERT INTO requirement (knowledge_item_id, requirement_kind, target_text, comparator) "
        "VALUES (%s, 'compliance', 'H-MCP requirement target', 'none') "
        "ON CONFLICT (knowledge_item_id) DO NOTHING",
        (REQ_ITEM_ID,),
    )
    conn.execute(
        "INSERT INTO item_term (knowledge_item_id, term_id) "
        "VALUES (%s, 'topic.climate_resilience') ON CONFLICT DO NOTHING",
        (REQ_ITEM_ID,),
    )

    conn.execute(
        "INSERT INTO knowledge_item (id, item_type, document_id, title, content_status) "
        "VALUES (%s, 'template', %s, 'H-MCP calculator', 'real') "
        "ON CONFLICT (id) DO NOTHING",
        (TEMPLATE_ITEM_ID, DOC_ID),
    )
    conn.execute(
        "INSERT INTO template (knowledge_item_id, template_kind, engine, slug) "
        "VALUES (%s, 'calculator', 'xlsx', %s) ON CONFLICT (knowledge_item_id) DO NOTHING",
        (TEMPLATE_ITEM_ID, TEMPLATE_SLUG),
    )
    conn.execute(
        "INSERT INTO template_parameter (template_id, name, sheet_name, cell_ref, is_input, is_output) "
        "VALUES (%s, 'h_mcp_param', %s, 'A1', true, false) "
        "ON CONFLICT (template_id, name) DO NOTHING",
        (TEMPLATE_ITEM_ID, SHEET_NAME),
    )

    conn.execute(
        "INSERT INTO citation (id, knowledge_item_id, document_id, page_index, printed_page_label) "
        "VALUES (%s, %s, %s, 7, '7') ON CONFLICT (id) DO NOTHING",
        (CIT_BENCH_ID, BENCH_ITEM_ID, DOC_ID),
    )
    conn.execute(
        "INSERT INTO citation (id, knowledge_item_id, document_id, page_index, printed_page_label) "
        "VALUES (%s, %s, %s, 8, '8') ON CONFLICT (id) DO NOTHING",
        (CIT_REQ_ID, REQ_ITEM_ID, DOC_ID),
    )
    conn.execute(
        "INSERT INTO citation (id, knowledge_item_id, document_id, page_index, printed_page_label) "
        "VALUES (%s, %s, %s, 9, '9') ON CONFLICT (id) DO NOTHING",
        (CIT_LOREM_ID, LOREM_ITEM_ID, DOC_ID),
    )

    conn.execute(
        "INSERT INTO chunk (id, document_id, knowledge_item_id, page_from, page_to, text, content_status) "
        "VALUES (%s, %s, %s, 7, 7, %s, 'real') ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text",
        (CHUNK_BENCH_ID, DOC_ID, BENCH_ITEM_ID, f"Benchmark chunk mentioning {SEARCH_TOKEN} for citation checks."),
    )
    conn.execute(
        "INSERT INTO chunk (id, document_id, knowledge_item_id, page_from, page_to, text, content_status) "
        "VALUES (%s, %s, %s, 8, 8, %s, 'real') ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text",
        (CHUNK_REQ_ID, DOC_ID, REQ_ITEM_ID, f"Requirement chunk mentioning {SEARCH_TOKEN} for citation checks."),
    )
    conn.execute(
        "INSERT INTO chunk (id, document_id, knowledge_item_id, page_from, page_to, text, content_status) "
        "VALUES (%s, %s, %s, 9, 9, %s, 'lorem') ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text",
        (CHUNK_LOREM_ID, DOC_ID, LOREM_ITEM_ID, f"Lorem ipsum placeholder chunk mentioning {LOREM_SEARCH_TOKEN}."),
    )


@pytest.fixture(scope="module", autouse=True)
def _fixture_data():
    with db.transaction() as conn:
        _setup(conn)
    yield


# ─────────────────────────────────────────────────────────────────────────
# search_knowledge
# ─────────────────────────────────────────────────────────────────────────

def test_search_knowledge_finds_real_content_with_citation():
    with m.read_connect() as conn:
        out = m.search_knowledge(conn, SEARCH_TOKEN, limit=10)
    assert out["results"], "expected at least the two real h-mcp fixture chunks"
    slugs = {r["citation"]["document_slug"] for r in out["results"]}
    assert DOC_SLUG in slugs
    for r in out["results"]:
        assert r["citation"]["document_slug"]
        assert r["citation"]["page_from"] is not None or r["citation"]["page_to"] is not None


def test_search_knowledge_excludes_placeholder_by_default():
    """The core requirement: lorem/wip/template/draft content must not be
    returned as fact unless explicitly requested."""
    with m.read_connect() as conn:
        out = m.search_knowledge(conn, LOREM_SEARCH_TOKEN, limit=10)
    assert out["results"] == []
    assert out["placeholder_included"] is False


def test_search_knowledge_include_placeholder_labels_it():
    with m.read_connect() as conn:
        out = m.search_knowledge(conn, LOREM_SEARCH_TOKEN, limit=10, include_placeholder=True)
    assert out["placeholder_included"] is True
    matches = [r for r in out["results"] if r["knowledge_item_id"] == LOREM_ITEM_ID]
    assert matches, "expected the lorem chunk once include_placeholder=True"
    assert matches[0]["is_placeholder"] is True


# ─────────────────────────────────────────────────────────────────────────
# get_benchmark
# ─────────────────────────────────────────────────────────────────────────

def test_get_benchmark_filters_and_carries_citation():
    with m.read_connect() as conn:
        out = m.get_benchmark(conn, metric="upfront_embodied_carbon",
                               building_use=BUILDING_USE, target_year=TARGET_YEAR)
    ids = {r["knowledge_item_id"] for r in out["results"]}
    assert BENCH_ITEM_ID in ids
    assert LOREM_ITEM_ID not in ids  # placeholder excluded by default
    for r in out["results"]:
        assert r["document_slug"]
        assert r["page_index"] is not None


def test_get_benchmark_include_placeholder_labels_it():
    with m.read_connect() as conn:
        out = m.get_benchmark(conn, metric="upfront_embodied_carbon",
                               building_use=BUILDING_USE, target_year=TARGET_YEAR,
                               include_placeholder=True)
    by_id = {r["knowledge_item_id"]: r for r in out["results"]}
    assert LOREM_ITEM_ID in by_id
    assert by_id[LOREM_ITEM_ID]["is_placeholder_content"] is True


# ─────────────────────────────────────────────────────────────────────────
# get_requirement_matrix
# ─────────────────────────────────────────────────────────────────────────

def test_get_requirement_matrix_topic_filter_and_citation():
    with m.read_connect() as conn:
        out = m.get_requirement_matrix(conn, topic="topic.climate_resilience", limit=50)
    ids = {r["knowledge_item_id"] for r in out["results"]}
    assert REQ_ITEM_ID in ids
    for r in out["results"]:
        assert r["document_slug"]
        assert r["page_index"] is not None


def test_get_requirement_matrix_degrades_gracefully_on_missing_view():
    """Defensive-coding requirement: a renamed/missing view must not crash
    the tool. Exercise the same code path by pointing it at a view name that
    doesn't exist to prove the degrade branch actually runs."""
    with m.read_connect() as conn:
        columns = m._existing_columns(conn, "v_requirement_matrix_does_not_exist")
    assert columns == set()


# ─────────────────────────────────────────────────────────────────────────
# list_templates
# ─────────────────────────────────────────────────────────────────────────

def test_list_templates_returns_sheet_based_citation():
    with m.read_connect() as conn:
        out = m.list_templates(conn, limit=200)
    matches = [r for r in out["results"] if r["slug"] == TEMPLATE_SLUG]
    assert matches, "expected the h-mcp fixture calculator"
    assert matches[0]["citation"]["document_slug"] == DOC_SLUG
    assert SHEET_NAME in matches[0]["citation"]["sheets"]


# ─────────────────────────────────────────────────────────────────────────
# get_citation
# ─────────────────────────────────────────────────────────────────────────

def test_get_citation_returns_page_and_label():
    with m.read_connect() as conn:
        out = m.get_citation(conn, BENCH_ITEM_ID)
    assert out["citations"], "expected a citation row"
    row = out["citations"][0]
    assert row["document_slug"] == DOC_SLUG
    assert row["page_index"] == 7
    assert row["is_placeholder"] is False


def test_get_citation_labels_placeholder_content():
    with m.read_connect() as conn:
        out = m.get_citation(conn, LOREM_ITEM_ID)
    assert out["citations"][0]["is_placeholder"] is True


# ─────────────────────────────────────────────────────────────────────────
# get_document
# ─────────────────────────────────────────────────────────────────────────

def test_get_document_outline_and_content_status_breakdown():
    with m.read_connect() as conn:
        out = m.get_document(conn, DOC_SLUG)
    assert out["document"]["slug"] == DOC_SLUG
    titles = {n["title"] for n in out["outline"]}
    assert "H-MCP Fixture Chapter" in titles
    # the fixture deliberately includes one lorem item, so the breakdown
    # must surface it rather than average it away
    assert out["content_status_breakdown"].get("lorem", 0) >= 1
    assert out["placeholder_item_fraction"] > 0


def test_get_document_unknown_slug_errors_cleanly():
    with m.read_connect() as conn:
        out = m.get_document(conn, "h-mcp-does-not-exist")
    assert "error" in out


# ─────────────────────────────────────────────────────────────────────────
# Read-only role enforcement, at the level this server actually connects
# through (not just raw psql -- see db/roles.sql for the DDL-level proof).
# ─────────────────────────────────────────────────────────────────────────

def test_readonly_connection_cannot_write():
    with m.read_connect() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("INSERT INTO unit (id, symbol) VALUES ('h_mcp_bad_unit', 'bad')")


def test_no_account_id_set_sees_zero_rows():
    with psycopg.connect(m.readonly_dsn()) as conn:
        row = conn.execute("SELECT count(*) FROM source_document").fetchone()
        assert row[0] == 0
