"""The one place a pure `Extraction` becomes rows.

Runs inside the caller's transaction (never opens its own — see CONTRACT.md).
Idempotent per document: re-running clears this document's chunks, external
references and knowledge_item rows (subtables, citations cascade off
knowledge_item_id) before writing again. doc_node rows are *not* wiped; they
are upserted by `code`, so a document's structural spine — much of it likely
already laid down by tools/build_structure.py — is enriched rather than
duplicated on every extraction run.

Two families of forward reference get resolved here:
  - `Node.parent_ref` / `Item.node_ref`   -- caller-local refs into this
    Extraction's own `nodes`, resolved via a ref -> doc_node.id map.
  - `Item.ref` -- caller-local refs between items, used today by
    `pattern.parent_pattern_id` and `process_step.responsible_role_id`,
    both of which are uuid columns that point at another knowledge_item.
A ref that never resolves is a warning and a NULL. It never raises.
"""
from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from tools.chunk_pages import split as _split_page
from tools.pipeline import Extraction, Item, Node

# ── subtype tables, keyed by Item.item_type ──────────────────────────────

ITEM_TABLE = {
    "requirement": "requirement",
    "benchmark": "benchmark",
    "guidance": "guidance",
    "pattern": "pattern",
    "template": "template",
    "definition": "definition",
    "process_step": "process_step",
    "role": "role",
}

# payload keys that are really refs into the item ref-map, not literal uuids
SELF_REF_PAYLOAD_KEYS = {
    "pattern": ("parent_pattern_id",),
    "process_step": ("responsible_role_id",),
}

# uuid payload columns whose value may arrive as a caller-local lookup ref
# rather than an id -- resolved against the maps the lookup upserts return.
LOOKUP_REF_PAYLOAD_KEYS = {
    "requirement": {"criterion_id": "criterion", "rating_level_id": "rating_level"},
}


# a caller-local handle carried on lookup rows; never a column
REF_HANDLE = "__ref__"


class PayloadError(ValueError):
    """Item.payload (or a lookup dict) names a column that table doesn't have."""


# ── column cache ──────────────────────────────────────────────────────────

_COLUMN_CACHE: dict[str, set[str]] = {}


def _table_columns(conn: psycopg.Connection, table: str) -> set[str]:
    if table not in _COLUMN_CACHE:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        cols = {r["column_name"] if isinstance(r, dict) else r[0] for r in rows}
        if not cols:
            raise PayloadError(f"write_extraction: no such table {table!r}")
        _COLUMN_CACHE[table] = cols
    return _COLUMN_CACHE[table]


def _validate_payload(conn: psycopg.Connection, table: str, payload: dict[str, Any]) -> None:
    allowed = _table_columns(conn, table) - {"knowledge_item_id"}
    bad = sorted(k for k in payload if k not in allowed and k != REF_HANDLE)
    if bad:
        raise PayloadError(
            f"write_extraction: {bad!r} not a column of {table!r} "
            f"(item_type payload). Valid columns: {sorted(allowed)}"
        )


def _adapt(value: Any) -> Any:
    # jsonb columns (pattern.attributes, ...) get a dict; everything else
    # psycopg already knows how to adapt (lists -> arrays, etc.)
    return Json(value) if isinstance(value, dict) else value


def _insert_row(conn: psycopg.Connection, table: str, values: dict[str, Any]) -> None:
    cols = list(values)
    stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    conn.execute(stmt, [_adapt(values[c]) for c in cols])


# ── ltree label sanitising ────────────────────────────────────────────────

_LABEL_BAD = re.compile(r"[^A-Za-z0-9_]+")
_UUID_RE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


def _ltree_label(text: str | None, fallback: str) -> str:
    raw = (text or fallback) or "n"
    label = _LABEL_BAD.sub("_", raw).strip("_") or "n"
    if label[0].isdigit():
        label = f"n{label}"
    return label[:200]


# ── nodes: topological insert/upsert, keyed on code ───────────────────────


def _topo_order_nodes(nodes: list[Node]) -> list[Node]:
    by_ref = {n.ref for n in nodes if n.ref}
    resolved: set[str] = set()
    order: list[Node] = []
    remaining = list(nodes)
    changed = True
    while remaining and changed:
        changed = False
        still = []
        for n in remaining:
            ready = n.parent_ref is None or n.parent_ref in resolved or n.parent_ref not in by_ref
            if ready:
                order.append(n)
                if n.ref:
                    resolved.add(n.ref)
                changed = True
            else:
                still.append(n)
        remaining = still
    order.extend(remaining)  # only reachable via a cycle; resolved as dangling below
    return order


def _find_node_by_code(conn: psycopg.Connection, document_id: str, code: str) -> dict | None:
    return conn.execute(
        "SELECT id, path::text AS path FROM doc_node WHERE document_id = %s AND code = %s",
        (document_id, code),
    ).fetchone()


def _find_node_uncoded(conn: psycopg.Connection, document_id: str, node: Node,
                       parent_id: str | None) -> dict | None:
    """Match a node that carries no code, so re-extraction updates it in place.

    Codes are the natural key where a document supplies them, but volume roots,
    slide groups and most detected headings have none -- and matching only on
    code meant every re-run inserted the whole uncoded tree again. Re-running an
    improved extractor is a designed workflow here, so that silently duplicated
    structure each time. Position plus kind plus title is the stable key for
    these: an extractor that emits the same tree twice emits it identically.
    """
    return conn.execute(
        """SELECT id, path::text AS path FROM doc_node
            WHERE document_id = %s
              AND code IS NULL
              AND parent_id IS NOT DISTINCT FROM %s
              AND node_kind = %s
              AND title IS NOT DISTINCT FROM %s
              AND ordinal = %s
            LIMIT 1""",
        (document_id, parent_id, node.node_kind, node.title, node.ordinal),
    ).fetchone()


def _write_nodes(
    conn: psycopg.Connection, document_id: str, nodes: list[Node], warnings: list[str]
) -> tuple[dict[str, str], dict]:
    """Returns (ref -> doc_node.id map, counts)."""
    ref_map: dict[str, str] = {}
    path_by_id: dict[str, str] = {}
    inserted = updated = 0

    for node in _topo_order_nodes(nodes):
        parent_id = None
        parent_path = None
        if node.parent_ref:
            parent_id = ref_map.get(node.parent_ref)
            if parent_id is None:
                warnings.append(
                    f"dangling node parent_ref {node.parent_ref!r} "
                    f"(node ref={node.ref!r} code={node.code!r})"
                )
            else:
                parent_path = path_by_id.get(parent_id)

        existing = (_find_node_by_code(conn, document_id, node.code) if node.code
                    else _find_node_uncoded(conn, document_id, node, parent_id))

        if existing:
            node_id = existing["id"]
            conn.execute(
                """UPDATE doc_node SET
                     title      = COALESCE(%s, title),
                     title_alt  = COALESCE(%s, title_alt),
                     text       = COALESCE(%s, text),
                     page_from  = COALESCE(%s, page_from),
                     page_to    = COALESCE(%s, page_to)
                   WHERE id = %s""",
                (node.title, node.title_alt, node.text, node.page_from, node.page_to, node_id),
            )
            node_path = existing["path"]
            updated += 1
        else:
            label = _ltree_label(node.code, f"{node.node_kind}_{node.ordinal}")
            node_path = f"{parent_path}.{label}" if parent_path else label
            row = conn.execute(
                """INSERT INTO doc_node
                     (document_id, parent_id, node_kind, code, title, title_alt,
                      ordinal, page_from, page_to, path, text)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (document_id, parent_id, node.node_kind, node.code, node.title,
                 node.title_alt, node.ordinal, node.page_from, node.page_to,
                 node_path, node.text),
            ).fetchone()
            node_id = row["id"]
            inserted += 1

        path_by_id[node_id] = node_path
        if node.ref:
            ref_map[node.ref] = node_id

    return ref_map, {"nodes_inserted": inserted, "nodes_updated": updated}


# ── lookup upserts ─────────────────────────────────────────────────────────


# Lookup list -> the natural key a bare `ref` aliases, or None where the table
# has no such column and `ref` is only a caller-local handle to be discarded.
_LOOKUP_LISTS = {
    "units": None,                   # keyed on id
    "metrics": None,                 # keyed on id
    "design_variables": None,        # keyed on id
    "design_variable_values": None,  # keyed on id
    "rating_scales": "slug",
    "rating_levels": None,           # keyed on (scale, ordinal)
    "frameworks": "slug",
    "criteria": None,                # keyed on (framework, code)
    "requirement_scopes": None,      # keyed on id (caller-minted, like units/metrics)
}


def _normalise_lookup_refs(extraction) -> None:
    """Accept `ref`/`*_ref` on lookup rows as well as `*_slug`.

    `ref` is how records link to each other everywhere else in the pipeline
    (Node.ref, Item.node_ref, parent_pattern_ref), so extractors reasonably use
    it for lookup rows too. Rather than make every extractor learn a second
    convention for these eight lists, translate here: a bare `ref` duplicates
    the natural key and is dropped, and `<thing>_ref` becomes `<thing>_slug`.
    """
    # criteria link to their parent by ref, but the upsert walks parent_code;
    # translate through the rows' own ref -> code mapping before anything else.
    code_by_ref = {r["ref"]: r.get("code")
                   for r in (extraction.criteria or []) if isinstance(r, dict) and r.get("ref")}

    for name, natural_key in _LOOKUP_LISTS.items():
        for row in getattr(extraction, name, []) or []:
            if not isinstance(row, dict):
                continue
            if name == "criteria" and row.get("parent_ref"):
                row.setdefault("parent_code", code_by_ref.get(row["parent_ref"]))
                row.pop("parent_ref", None)
            for key in [k for k in row if k == "ref" or k.endswith("_ref")]:
                value = row.pop(key)
                if key == "ref":
                    # keep the handle: items point at these rows by ref
                    row[REF_HANDLE] = value
                    if natural_key and value is not None:
                        row.setdefault(natural_key, value)
                    continue
                target = f"{key[:-4]}_slug"
                if row.get(target) is None and value is not None:
                    row[target] = value


def _upsert_units(conn: psycopg.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        _validate_payload(conn, "unit", r)
        conn.execute(
            """INSERT INTO unit (id, symbol, dimension, si_factor)
               VALUES (%(id)s, %(symbol)s, %(dimension)s, %(si_factor)s)
               ON CONFLICT (id) DO UPDATE SET
                 symbol    = COALESCE(EXCLUDED.symbol, unit.symbol),
                 dimension = COALESCE(EXCLUDED.dimension, unit.dimension),
                 si_factor = COALESCE(EXCLUDED.si_factor, unit.si_factor)""",
            {"id": r["id"], "symbol": r.get("symbol"), "dimension": r.get("dimension"),
             "si_factor": r.get("si_factor")},
        )
        n += 1
    return n


def _upsert_metrics(conn: psycopg.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        _validate_payload(conn, "metric", r)
        conn.execute(
            """INSERT INTO metric (id, name, definition, default_unit_id, formula, higher_is_better)
               VALUES (%(id)s, %(name)s, %(definition)s, %(default_unit_id)s, %(formula)s, %(higher_is_better)s)
               ON CONFLICT (id) DO UPDATE SET
                 name             = COALESCE(EXCLUDED.name, metric.name),
                 definition       = COALESCE(EXCLUDED.definition, metric.definition),
                 default_unit_id  = COALESCE(EXCLUDED.default_unit_id, metric.default_unit_id),
                 formula          = COALESCE(EXCLUDED.formula, metric.formula),
                 higher_is_better = COALESCE(EXCLUDED.higher_is_better, metric.higher_is_better)""",
            {"id": r["id"], "name": r.get("name"), "definition": r.get("definition"),
             "default_unit_id": r.get("default_unit_id"), "formula": r.get("formula"),
             "higher_is_better": r.get("higher_is_better")},
        )
        n += 1
    return n


def _upsert_design_variables(conn: psycopg.Connection, rows: list[dict], document_id: str) -> int:
    n = 0
    for r in rows:
        _validate_payload(conn, "design_variable", r)
        conn.execute(
            """INSERT INTO design_variable (id, name, document_id, ordinal)
               VALUES (%(id)s, %(name)s, %(document_id)s, %(ordinal)s)
               ON CONFLICT (id) DO UPDATE SET
                 name    = COALESCE(EXCLUDED.name, design_variable.name),
                 ordinal = COALESCE(EXCLUDED.ordinal, design_variable.ordinal)""",
            {"id": r["id"], "name": r.get("name"), "document_id": r.get("document_id", document_id),
             "ordinal": r.get("ordinal", 0)},
        )
        n += 1
    return n


def _upsert_design_variable_values(conn: psycopg.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        _validate_payload(conn, "design_variable_value", r)
        conn.execute(
            """INSERT INTO design_variable_value (id, variable_id, label, ordinal)
               VALUES (%(id)s, %(variable_id)s, %(label)s, %(ordinal)s)
               ON CONFLICT (id) DO UPDATE SET
                 label   = COALESCE(EXCLUDED.label, design_variable_value.label),
                 ordinal = COALESCE(EXCLUDED.ordinal, design_variable_value.ordinal)""",
            {"id": r["id"], "variable_id": r["variable_id"], "label": r.get("label"),
             "ordinal": r.get("ordinal", 0)},
        )
        n += 1
    return n


def _upsert_frameworks(conn: psycopg.Connection, rows: list[dict], document_id: str) -> tuple[int, dict[str, str]]:
    """Keyed on slug (framework.id is server-generated). Returns slug -> id map."""
    slug_map: dict[str, str] = {}
    n = 0
    for r in rows:
        row_for_validation = {k: v for k, v in r.items() if k != "rating_scale_slug"}
        _validate_payload(conn, "framework", row_for_validation)
        rating_scale_id = None
        if r.get("rating_scale_slug"):
            found = conn.execute(
                "SELECT id FROM rating_scale WHERE slug = %s", (r["rating_scale_slug"],)
            ).fetchone()
            rating_scale_id = found["id"] if found else None
        row = conn.execute(
            """INSERT INTO framework (slug, name, owner_org_id, version, rating_scale_id, document_id)
               VALUES (%(slug)s, %(name)s, %(owner_org_id)s, %(version)s, %(rating_scale_id)s, %(document_id)s)
               ON CONFLICT (slug) DO UPDATE SET
                 name            = COALESCE(EXCLUDED.name, framework.name),
                 owner_org_id    = COALESCE(EXCLUDED.owner_org_id, framework.owner_org_id),
                 version         = COALESCE(EXCLUDED.version, framework.version),
                 rating_scale_id = COALESCE(EXCLUDED.rating_scale_id, framework.rating_scale_id)
               RETURNING id""",
            {"slug": r["slug"], "name": r.get("name"), "owner_org_id": r.get("owner_org_id"),
             "version": r.get("version"), "rating_scale_id": rating_scale_id,
             "document_id": r.get("document_id", document_id)},
        ).fetchone()
        slug_map[r["slug"]] = row["id"]
        n += 1
    return n, slug_map


def _upsert_rating_scales(conn: psycopg.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        _validate_payload(conn, "rating_scale", r)
        conn.execute(
            """INSERT INTO rating_scale (slug, name) VALUES (%(slug)s, %(name)s)
               ON CONFLICT (slug) DO UPDATE SET name = COALESCE(EXCLUDED.name, rating_scale.name)""",
            {"slug": r["slug"], "name": r.get("name")},
        )
        n += 1
    return n


def _upsert_rating_levels(conn: psycopg.Connection, rows: list[dict]) -> tuple[int, dict[str, str]]:
    """Returns (count, ref -> rating_level.id) so requirements can link by ref."""
    n = 0
    ref_map: dict[str, str] = {}
    for r in rows:
        row_for_validation = {k: v for k, v in r.items() if k != "scale_slug"}
        _validate_payload(conn, "rating_level", row_for_validation)
        scale = conn.execute("SELECT id FROM rating_scale WHERE slug = %s", (r["scale_slug"],)).fetchone()
        if not scale:
            raise PayloadError(f"write_extraction: rating_level references unknown scale_slug {r['scale_slug']!r}")
        conn.execute(
            """INSERT INTO rating_level (scale_id, ordinal, code, name, description, colour)
               VALUES (%(scale_id)s, %(ordinal)s, %(code)s, %(name)s, %(description)s, %(colour)s)
               ON CONFLICT (scale_id, ordinal) DO UPDATE SET
                 code        = COALESCE(EXCLUDED.code, rating_level.code),
                 name        = COALESCE(EXCLUDED.name, rating_level.name),
                 description = COALESCE(EXCLUDED.description, rating_level.description),
                 colour      = COALESCE(EXCLUDED.colour, rating_level.colour)""",
            {"scale_id": scale["id"], "ordinal": r["ordinal"], "code": r.get("code"),
             "name": r.get("name"), "description": r.get("description"), "colour": r.get("colour")},
        )
        if r.get(REF_HANDLE):
            found = conn.execute(
                "SELECT id FROM rating_level WHERE scale_id = %s AND ordinal = %s",
                (scale["id"], r["ordinal"]),
            ).fetchone()
            if found:
                ref_map[r[REF_HANDLE]] = found["id"]
        n += 1
    return n, ref_map


def _upsert_criteria(conn: psycopg.Connection, rows: list[dict],
                     framework_slug_map: dict[str, str]) -> tuple[int, dict[str, str]]:
    """No unique constraint backs (framework_id, code) in schema.sql, so this
    upserts by hand rather than via ON CONFLICT. Parent linkage is by
    `parent_code` within the same framework, resolved topologically."""
    resolved_ids: dict[tuple[str, str], str] = {}
    path_by_key: dict[tuple[str, str], str] = {}
    criterion_ref_map: dict[str, str] = {}

    def framework_id_for(fslug: str) -> str:
        fid = framework_slug_map.get(fslug)
        if not fid:
            found = conn.execute("SELECT id FROM framework WHERE slug = %s", (fslug,)).fetchone()
            fid = found["id"] if found else None
        if not fid:
            raise PayloadError(f"write_extraction: criterion references unknown framework_slug {fslug!r}")
        return fid

    def ready(r: dict) -> bool:
        parent_code = r.get("parent_code")
        if not parent_code:
            return True
        return (r["framework_slug"], parent_code) in resolved_ids

    remaining = list(rows)
    n = 0
    changed = True
    while remaining and changed:
        changed = False
        still = []
        for r in remaining:
            if not ready(r):
                still.append(r)
                continue
            changed = True
            row_for_validation = {k: v for k, v in r.items() if k not in ("framework_slug", "parent_code")}
            _validate_payload(conn, "criterion", row_for_validation)
            fid = framework_id_for(r["framework_slug"])
            parent_code = r.get("parent_code")
            parent_id = resolved_ids.get((r["framework_slug"], parent_code)) if parent_code else None
            parent_path = path_by_key.get((r["framework_slug"], parent_code)) if parent_code else None
            label = _ltree_label(r.get("code"), f"c{n}")
            cpath = f"{parent_path}.{label}" if parent_path else label

            existing = None
            if r.get("code"):
                existing = conn.execute(
                    "SELECT id FROM criterion WHERE framework_id = %s AND code = %s",
                    (fid, r["code"]),
                ).fetchone()
            if existing:
                cid = existing["id"]
                conn.execute(
                    """UPDATE criterion SET
                         title_primary = COALESCE(%s, title_primary),
                         title_alt     = COALESCE(%s, title_alt),
                         ordinal       = COALESCE(%s, ordinal),
                         parent_id     = COALESCE(%s, parent_id),
                         path          = COALESCE(%s, path)
                       WHERE id = %s""",
                    (r.get("title_primary"), r.get("title_alt"), r.get("ordinal"),
                     parent_id, cpath, cid),
                )
            else:
                row = conn.execute(
                    """INSERT INTO criterion (framework_id, parent_id, code, title_primary, title_alt, ordinal, path)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (fid, parent_id, r.get("code"), r.get("title_primary") or r.get("code") or "",
                     r.get("title_alt"), r.get("ordinal", 0), cpath),
                )
                cid = row.fetchone()["id"]
            if r.get("code"):
                resolved_ids[(r["framework_slug"], r["code"])] = cid
                path_by_key[(r["framework_slug"], r["code"])] = cpath
            if r.get(REF_HANDLE):
                criterion_ref_map[r[REF_HANDLE]] = cid
            n += 1
        remaining = still
    # leftover rows form a cycle on parent_code; insert them rootless rather than dropping data
    for r in remaining:
        row_for_validation = {k: v for k, v in r.items() if k not in ("framework_slug", "parent_code")}
        _validate_payload(conn, "criterion", row_for_validation)
        fid = framework_id_for(r["framework_slug"])
        conn.execute(
            """INSERT INTO criterion (framework_id, code, title_primary, title_alt, ordinal)
               VALUES (%s,%s,%s,%s,%s)""",
            (fid, r.get("code"), r.get("title_primary") or r.get("code") or "",
             r.get("title_alt"), r.get("ordinal", 0)),
        )
        n += 1
    return n, criterion_ref_map


def _upsert_requirement_scopes(conn: psycopg.Connection, rows: list[dict],
                               framework_slug_map: dict[str, str]) -> int:
    """requirement_scope.id is caller-minted (like unit/metric ids), so this
    is a plain upsert -- no ref/id round trip needed. framework_slug is
    resolved the same way _upsert_criteria resolves it, because
    requirement_scope.framework_id is not nullable."""
    n = 0
    for r in rows:
        row_for_validation = {k: v for k, v in r.items() if k != "framework_slug"}
        _validate_payload(conn, "requirement_scope", row_for_validation)
        fid = framework_slug_map.get(r["framework_slug"])
        if not fid:
            found = conn.execute(
                "SELECT id FROM framework WHERE slug = %s", (r["framework_slug"],)
            ).fetchone()
            fid = found["id"] if found else None
        if not fid:
            raise PayloadError(
                f"write_extraction: requirement_scope references unknown "
                f"framework_slug {r['framework_slug']!r}"
            )
        conn.execute(
            """INSERT INTO requirement_scope (id, framework_id, code, title, ordinal)
               VALUES (%(id)s, %(fid)s, %(code)s, %(title)s, %(ordinal)s)
               ON CONFLICT (id) DO UPDATE SET
                 framework_id = EXCLUDED.framework_id,
                 code         = COALESCE(EXCLUDED.code, requirement_scope.code),
                 title        = COALESCE(EXCLUDED.title, requirement_scope.title),
                 ordinal      = COALESCE(EXCLUDED.ordinal, requirement_scope.ordinal)""",
            {"id": r["id"], "fid": fid, "code": r.get("code"), "title": r.get("title"),
             "ordinal": r.get("ordinal", 0)},
        )
        n += 1
    return n


# stub column for a lookup table whose row an extractor referenced but never declared
_STUB_COLUMN = {"metric": "name", "unit": "symbol"}


def _ensure_lookup(conn: psycopg.Connection, table: str, key: Any,
                   warnings: list[str]) -> bool:
    """Create a stub row for a metric or unit referenced but never declared.

    The alternative is a foreign-key violation that loses a whole document's
    extraction over one missing lookup row. The verbatim value_text is kept
    either way, so a stub plus a warning beats dropping the data -- the review
    queue can name it properly later.
    """
    if not key or not isinstance(key, str):
        return False
    if conn.execute(sql.SQL("SELECT 1 FROM {} WHERE id = %s").format(
            sql.Identifier(table)), (key,)).fetchone():
        return False
    conn.execute(
        sql.SQL("INSERT INTO {} (id, {}) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING").format(
            sql.Identifier(table), sql.Identifier(_STUB_COLUMN[table])),
        (key, key.replace("_", " ")),
    )
    warnings.append(f"{table} {key!r} was referenced but not declared; stub created")
    return True


def _write_template_parameters(conn: psycopg.Connection, template_id: str,
                                parameters: list[dict]) -> int:
    n = 0
    for i, param in enumerate(parameters):
        if not isinstance(param, dict) or not param.get("name"):
            continue
        conn.execute(
            """INSERT INTO template_parameter
                 (template_id, name, label, sheet_name, cell_ref, data_type,
                  unit_id, default_value, is_input, is_output, ordinal)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (template_id, name) DO UPDATE SET
                 label = EXCLUDED.label, cell_ref = EXCLUDED.cell_ref,
                 default_value = EXCLUDED.default_value,
                 is_input = EXCLUDED.is_input, is_output = EXCLUDED.is_output""",
            (template_id, param["name"], param.get("label"), param.get("sheet_name"),
             param.get("cell_ref"), param.get("data_type", "number"),
             param.get("unit_id"), param.get("default_value"),
             bool(param.get("is_input", True)), bool(param.get("is_output", False)),
             param.get("ordinal", i)),
        )
        n += 1
    return n


# ── items ───────────────────────────────────────────────────────────────


def _topo_order_items(items: list[Item]) -> list[Item]:
    refs_present = {it.ref for it in items if it.ref}
    resolved: set[str] = set()
    order: list[Item] = []
    remaining = list(items)
    changed = True
    while remaining and changed:
        changed = False
        still = []
        for it in remaining:
            deps = [
                it.payload.get(f)
                for f in SELF_REF_PAYLOAD_KEYS.get(it.item_type, ())
                if isinstance(it.payload.get(f), str) and it.payload.get(f) in refs_present
            ]
            if all(d in resolved for d in deps):
                order.append(it)
                if it.ref:
                    resolved.add(it.ref)
                changed = True
            else:
                still.append(it)
        remaining = still
    order.extend(remaining)
    return order


def _write_items(
    conn: psycopg.Connection,
    document_id: str,
    items: list[Item],
    node_ref_map: dict[str, str],
    extraction_run_id: str,
    warnings: list[str],
    lookup_ref_maps: dict[str, dict[str, str]] | None = None,
) -> dict:
    lookup_ref_maps = lookup_ref_maps or {}
    item_ref_map: dict[str, str] = {}
    refs_present = {it.ref for it in items if it.ref}
    page_index_map = {
        r["page_index"]: r["id"]
        for r in conn.execute(
            "SELECT id, page_index FROM source_page WHERE document_id = %s", (document_id,)
        ).fetchall()
    }

    counts = {
        "items": 0, "citations": 0, "chunks_item": 0,
        "item_terms": 0, "item_terms_skipped": 0, "requirement_scope_applicability": 0,
    }
    covered_pages: set[int] = set()

    for item in _topo_order_items(items):
        if item.item_type not in ITEM_TABLE:
            raise PayloadError(f"write_extraction: unknown item_type {item.item_type!r}")
        table = ITEM_TABLE[item.item_type]

        node_id = None
        if item.node_ref:
            node_id = node_ref_map.get(item.node_ref)
            if node_id is None:
                warnings.append(f"dangling item node_ref {item.node_ref!r} (item ref={item.ref!r})")

        row = conn.execute(
            """INSERT INTO knowledge_item
                 (item_type, document_id, node_id, title, statement, summary,
                  content_status, extraction_confidence, extraction_run_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (item.item_type, document_id, node_id, item.title, item.statement, item.summary,
             item.content_status, item.confidence, extraction_run_id),
        ).fetchone()
        item_id = row["id"]
        counts["items"] += 1
        if item.ref:
            item_ref_map[item.ref] = item_id

        # resolve self-refs (pattern.parent_pattern_id, process_step.responsible_role_id)
        payload = dict(item.payload)
        for field in SELF_REF_PAYLOAD_KEYS.get(item.item_type, ()):
            v = payload.get(field)
            if isinstance(v, str) and v in refs_present:
                resolved = item_ref_map.get(v)
                if resolved is None:
                    warnings.append(f"dangling item ref {v!r} in {table}.{field} (item ref={item.ref!r})")
                payload[field] = resolved

        # resolve lookup refs (requirement.criterion_id / .rating_level_id).
        # Extractors emit these as the ref they gave the criterion or level,
        # because at extraction time no database id exists yet.
        for field, kind in LOOKUP_REF_PAYLOAD_KEYS.get(item.item_type, {}).items():
            v = payload.get(field)
            if not isinstance(v, str) or _UUID_RE.match(v):
                continue
            resolved = lookup_ref_maps.get(kind, {}).get(v)
            if resolved is None:
                warnings.append(f"unresolved {kind} ref {v!r} in {table}.{field}")
            payload[field] = resolved

        parameters = payload.pop("parameters", None) if item.item_type == "template" else None
        if item.item_type in ("benchmark", "requirement"):
            counts["metrics_autocreated"] = counts.get("metrics_autocreated", 0) + int(
                _ensure_lookup(conn, "metric", payload.get("metric_id"), warnings))
            counts["units_autocreated"] = counts.get("units_autocreated", 0) + int(
                _ensure_lookup(conn, "unit", payload.get("unit_id"), warnings))

        _validate_payload(conn, table, payload)
        _insert_row(conn, table, {"knowledge_item_id": item_id, **payload})

        # per-scope applicability (compliance_table.py's role/checklist
        # reprints of the same requirement). Duck-typed on the Item -- see
        # that module's docstring for why it isn't a declared dataclass
        # field. requirement_scope rows are upserted earlier in
        # write_extraction(), so scope_id is already valid here.
        if item.item_type == "requirement":
            for row in getattr(item, "scope_applicability", None) or []:
                _validate_payload(conn, "requirement_scope_applicability", row)
                _insert_row(conn, "requirement_scope_applicability",
                            {"knowledge_item_id": item_id, **row})
                counts["requirement_scope_applicability"] += 1

        if parameters:
            counts["template_parameters"] = counts.get("template_parameters", 0) + \
                _write_template_parameters(conn, item_id, parameters)

        for cit in item.citations:
            page_id = page_index_map.get(cit.page_index)
            conn.execute(
                """INSERT INTO citation
                     (knowledge_item_id, document_id, page_id, page_index, printed_page_label, bbox)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (item_id, document_id, page_id, cit.page_index, cit.printed_page_label, cit.bbox),
            )
            counts["citations"] += 1
            covered_pages.add(cit.page_index)

        text = "\n\n".join(p for p in (item.title, item.statement) if p) \
            or item.summary or f"[{item.item_type}]"
        pages = [c.page_index for c in item.citations]
        conn.execute(
            """INSERT INTO chunk (document_id, knowledge_item_id, node_id, page_from, page_to, text, content_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (document_id, item_id, node_id, min(pages) if pages else None,
             max(pages) if pages else None, text, item.content_status),
        )
        counts["chunks_item"] += 1

        if item.terms:
            existing_terms = {
                r["id"] for r in conn.execute(
                    "SELECT id FROM taxonomy_term WHERE id = ANY(%s)", (item.terms,)
                ).fetchall()
            }
            for term_id in item.terms:
                if term_id not in existing_terms:
                    counts["item_terms_skipped"] += 1
                    continue
                conn.execute(
                    """INSERT INTO item_term (knowledge_item_id, term_id)
                       VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                    (item_id, term_id),
                )
                counts["item_terms"] += 1

    return counts, covered_pages


# ── page-level chunks for pages no item already covers ────────────────────


def _write_page_chunks(conn: psycopg.Connection, document_id: str, covered_pages: set[int]) -> int:
    """A chunk for every page no item was extracted from, so its text is still
    findable -- in windows, not whole.

    This used to insert the entire page as one chunk. The embedding model has a
    512-token window and a third of the pages exceeded it, so their vectors
    described only the top of the page while the row looked indexed. The
    windowing rule lives in tools/chunk_pages.py, which is also the tool that
    re-windows an existing corpus; keeping one implementation means the two
    cannot drift into disagreeing about what a page chunk is.
    """
    pages = conn.execute(
        "SELECT page_index, text FROM source_page "
        "WHERE document_id = %s AND content_status = 'real'",
        (document_id,),
    ).fetchall()
    n = 0
    for p in pages:
        if p["page_index"] in covered_pages:
            continue
        for ordinal, window in enumerate(_split_page(p["text"])):
            conn.execute(
                """INSERT INTO chunk (document_id, page_from, page_to, ordinal, text, content_status)
                   VALUES (%s,%s,%s,%s,%s,'real')""",
                (document_id, p["page_index"], p["page_index"], ordinal, window),
            )
            n += 1
    return n


# ── external references ────────────────────────────────────────────────


def _write_references(
    conn: psycopg.Connection, document_id: str, refs, node_ref_map: dict[str, str], warnings: list[str]
) -> int:
    n = 0
    for ref in refs:
        from_node_id = None
        if ref.from_node_ref:
            from_node_id = node_ref_map.get(ref.from_node_ref)
            if from_node_id is None:
                warnings.append(f"dangling reference from_node_ref {ref.from_node_ref!r}")
        conn.execute(
            """INSERT INTO external_reference (from_node_id, from_document_id, raw_text, ref_kind, status)
               VALUES (%s,%s,%s,%s,'unresolved')""",
            (from_node_id, document_id, ref.raw_text, ref.ref_kind),
        )
        n += 1
    return n


# ── entry point ────────────────────────────────────────────────────────


def write_extraction(conn: psycopg.Connection, document_id: str, extraction: Extraction) -> dict:
    """Persist an Extraction. Runs inside the caller's transaction.

    Idempotent per document: clears this document's chunks, external
    references and knowledge_items (+cascaded subtables/citations) first.
    """
    warnings: list[str] = list(extraction.warnings)

    run = conn.execute(
        "INSERT INTO extraction_run (pipeline_version, stats) VALUES (%s, %s) RETURNING id",
        ("write_extraction/1", "{}"),
    ).fetchone()
    extraction_run_id = run["id"]

    _normalise_lookup_refs(extraction)

    unit_count = _upsert_units(conn, extraction.units)
    metric_count = _upsert_metrics(conn, extraction.metrics)
    dv_count = _upsert_design_variables(conn, extraction.design_variables, document_id)
    dvv_count = _upsert_design_variable_values(conn, extraction.design_variable_values)
    rs_count = _upsert_rating_scales(conn, extraction.rating_scales)
    rl_count, level_ref_map = _upsert_rating_levels(conn, extraction.rating_levels)
    fw_count, framework_slug_map = _upsert_frameworks(conn, extraction.frameworks, document_id)
    crit_count, criterion_ref_map = _upsert_criteria(
        conn, extraction.criteria, framework_slug_map)
    reqscope_count = _upsert_requirement_scopes(
        conn, getattr(extraction, "requirement_scopes", []) or [], framework_slug_map)

    # idempotency: clear this document's prior extraction output.
    #
    # `asset_id IS NULL` keeps figure chunks, which this stage did not write and
    # has no business deleting: they come from a model reading a cropped figure,
    # cost real money or real time to produce, and are keyed to source_asset
    # rather than to any knowledge item. Without the guard, re-running an
    # improved extractor -- which the workflow actively encourages -- throws
    # them away as a side effect.
    conn.execute("DELETE FROM chunk WHERE document_id = %s AND asset_id IS NULL", (document_id,))
    conn.execute("DELETE FROM external_reference WHERE from_document_id = %s", (document_id,))
    conn.execute("DELETE FROM knowledge_item WHERE document_id = %s", (document_id,))

    node_ref_map, node_counts = _write_nodes(conn, document_id, extraction.nodes, warnings)
    lookup_ref_maps = {"criterion": criterion_ref_map, "rating_level": level_ref_map}
    item_counts, covered_pages = _write_items(
        conn, document_id, extraction.items, node_ref_map, extraction_run_id,
        warnings, lookup_ref_maps)
    page_chunk_count = _write_page_chunks(conn, document_id, covered_pages)
    ref_count = _write_references(conn, document_id, extraction.references, node_ref_map, warnings)

    # A benchmark's chunk is its title alone until the typed row exists, so the
    # metric, value and unit that make it findable are composed in afterwards --
    # by the same function tools/refresh_chunk_text.py runs over an existing
    # corpus, so there is one definition of what a typed chunk says. The
    # embeddings it clears are NULL already at this point in a fresh ingest;
    # the EMBEDDED stage runs after this one and picks them up.
    from tools.refresh_chunk_text import refresh as _refresh_chunk_text

    typed_chunk_count = _refresh_chunk_text(conn, document_id=document_id)

    counts = {
        **node_counts,
        **item_counts,
        "chunks_page": page_chunk_count,
        "chunks_typed": typed_chunk_count,
        "external_references": ref_count,
        "units": unit_count,
        "metrics": metric_count,
        "frameworks": fw_count,
        "criteria": crit_count,
        "rating_scales": rs_count,
        "rating_levels": rl_count,
        "design_variables": dv_count,
        "design_variable_values": dvv_count,
        "requirement_scopes": reqscope_count,
        "warnings": warnings,
    }

    # Warnings are the review queue's raw material -- an extractor saying
    # "these two copies disagree" or "I skipped 32 WIP pages" is the most
    # valuable thing it produces. Persist them rather than dropping them on
    # the floor; truncate only so one pathological run cannot bloat the row.
    import json
    persisted = {k: v for k, v in counts.items() if k != "warnings"}
    persisted["warning_count"] = len(warnings)
    persisted["warnings"] = warnings[:200]
    conn.execute(
        "UPDATE extraction_run SET finished_at = now(), stats = %s WHERE id = %s",
        (json.dumps(persisted), extraction_run_id),
    )

    return counts
