"""The six reads.

Plain functions over a Catalog, each returning JSON-ready dicts -- so they can
be tested, and read, without MCP anywhere near them. server.py is the wiring.

Everything here answers from the metadata database. The questions worth asking
of an Oracle Forms application are mostly joins -- which forms write this
table, where is this procedure called, what fires on that button -- and the
loader has already done the hard part.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from .db import Catalog, CatalogError, clip, value

# A trigger body or a program unit can be thousands of lines. These are the
# caps on what one answer carries; a caller that wants more asks again with a
# bigger one, or reads the unit itself.
CODE_LIMIT = 20000
SNIPPET = 160
MAX_ROWS = 200


# ----------------------------------------------------------- subclassing
#
# An object subclassed from an object library carries a pointer and only the
# properties it overrides; the loader followed the pointer and recorded what
# the object takes from there. Reading the object's own row alone therefore
# answers what the file says, which is not what the form does -- an alert
# whose title, style and buttons all come from a library looks like an alert
# with nothing set at all.

LINKS = "form_subclass_links"
INHERITED = "form_inherited_properties"


def _links(cat: Catalog, file_pk) -> Dict[int, Dict[str, Any]]:
    """Every subclassed object in one module, by its id."""
    if not cat.has(LINKS):
        return {}
    return {r["owner_pk"]: dict(r) for r in cat.rows(
        "SELECT * FROM %s WHERE form_file_fk = ?" % LINKS, (file_pk,))}


def _inherited(cat: Catalog, owner_pk) -> Dict[str, Dict[str, Any]]:
    """What one object takes from its parent, by property name."""
    if not cat.has(INHERITED):
        return {}
    return {r["property_name"]: {"value": r["property_value"],
                                 "from_module": value(r, "from_module"),
                                 "from_object": value(r, "from_object")}
            for r in cat.rows(
                "SELECT * FROM %s WHERE owner_pk = ? ORDER BY property_name"
                % INHERITED, (owner_pk,))}


def _parent_file(cat: Catalog, link: Optional[Dict[str, Any]]) -> Optional[str]:
    """The file an object's parent was found in, or the file its pointer names.

    The file loaded is the answer when the loader found the parent; the
    pointer's own file name next; the module name the pointer gives is all
    some pointers carry, and is only said when there is nothing better.
    """
    if not link:
        return None
    if link.get("parent_file_fk"):
        got = cat.rows("SELECT file_name FROM form_files WHERE form_file_pk = ?",
                       (link["parent_file_fk"],))
        if got:
            return got[0]["file_name"]
    return link.get("parent_file") or None


def _through_group(source: Optional[Dict[str, Any]], row: Dict[str, Any],
                   link: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Say so when an object came in through a subclassed object group.

    Its own pointer names the group (ParentType 23); the loader follows that
    to the object of the same kind and name in the group's module, and the
    link records that object. The row keeps what Forms wrote.
    """
    if not source or not link:
        return source
    pointer = (row.get("parent_name") or "").strip()
    if pointer and pointer.upper() != (link.get("parent_object") or "").upper() \
            and str(row.get("parent_type") or "").strip() in ("23", "ObjectGroup", "Object Group"):
        source = dict(source, through_object_group=pointer)
    return source


def _source(cat: Catalog, link: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Where an object comes from, for an answer that mentions it."""
    if not link:
        return None
    out = {"file": _parent_file(cat, link),
           "object": link.get("parent_object"),
           "resolved": (link.get("resolved") or "N") == "Y"}
    if not out["file"]:
        # The pointer names a module and no file: all Forms recorded.
        out["module_name"] = link.get("parent_module")
    if not out["resolved"]:
        out["note"] = ("%s is not in this database, so what it gives this object "
                       "is unknown" % (out["file"] or link.get("parent_module")
                                       or "the parent"))
    return out


# ---------------------------------------------------------------- modules

def _counts(cat: Catalog, table: str, key: str) -> Dict[Any, int]:
    """How many rows of ``table`` each file has, in one pass over the table.

    A subquery per file reads the whole table once per file -- the database
    is opened read-only and has no index on the link column -- which took
    seconds on an application of a few hundred forms.
    """
    if not _have(cat, table, key):
        return {}
    return {r["k"]: r["n"] for r in cat.rows(
        "SELECT %s AS k, count(*) AS n FROM %s GROUP BY %s" % (key, table, key))}


def _form_modules(cat: Catalog) -> List[Dict[str, Any]]:
    rows = cat.rows(
        "SELECT form_file_pk AS pk, module_name, file_name, file_kind, module_kind,"
        "       module_version FROM form_files ORDER BY file_name")
    n = {name: _counts(cat, "form_" + name, "form_file_fk")
         for name in ("blocks", "items", "triggers", "program_units")}
    modules = [{
        "file": r["file_name"],
        "kind": value(r, "module_kind") or value(r, "file_kind"),
        # The name inside the file: whatever its developer typed, not unique,
        # often not the file's. The file is what identifies a module.
        "module_name": r["module_name"],
        "version": value(r, "module_version"),
        "blocks": n["blocks"].get(r["pk"], 0), "items": n["items"].get(r["pk"], 0),
        "triggers": n["triggers"].get(r["pk"], 0),
        "program_units": n["program_units"].get(r["pk"], 0),
    } for r in rows]
    return modules


def _report_modules(cat: Catalog) -> List[Dict[str, Any]]:
    rows = cat.rows("SELECT report_file_pk AS pk, file_name, module_name, module_version"
                    "  FROM report_files ORDER BY file_name")
    n = {name: _counts(cat, "report_" + name, "report_file_fk")
         for name in ("queries", "groups", "formulas", "placeholders", "program_units",
                      "parameters")}
    return [{"file": r["file_name"], "kind": "report", "module_name": r["module_name"],
             "version": value(r, "module_version"),
             **{name: n[name].get(r["pk"], 0) for name in n}} for r in rows]


def list_modules(cat: Catalog) -> Dict[str, Any]:
    """Every module in the application, forms and reports, by its file."""
    modules = []
    if "form" in cat.families:
        modules += _form_modules(cat)
    if "report" in cat.families:
        modules += _report_modules(cat)
    modules.sort(key=lambda m: (m["file"] or "").upper())
    out = {"database": cat.path, "modules": modules, "count": len(modules)}
    if cat.reports_path:
        out["reports_database"] = cat.reports_path
    return out


def _have(cat: Catalog, table: str, *columns: str) -> bool:
    """Whether a database has these columns to be asked about.

    A database is written by a loader and read later, possibly by a newer
    tool: a question about a column that is not there should come back as a
    quieter answer, not as "no such column".
    """
    if not cat.has(table):
        return False
    known = set(cat.columns(table))
    return all(c in known for c in columns)


def _navigation_summary(cat: Catalog, out: Dict[str, Any], file_name: str) -> None:
    """The files one module leads to and the files that lead to it, when the
    database recorded calls; calls() and called_by() give each call."""
    from . import navigation
    if not navigation._recorded(cat):
        return
    edges = navigation.graph(cat)["edges"]
    calls = [e for e in edges if not e.get("named")]
    named = [e for e in edges if e.get("named")]
    leads = sorted({e["to"] for e in calls if e["from"] == file_name and e["to"]}, key=str.upper)
    by = sorted({e["from"] for e in calls if e["to"] == file_name}, key=str.upper)
    open_ends = sum(1 for e in calls if e["from"] == file_name and not e["to"])
    summary = {"leads_to": leads, "called_by": by, "calls_not_followed": open_ends}
    names = sorted({e["to"] for e in named if e["from"] == file_name}, key=str.upper)
    named_by = sorted({e["from"] for e in named if e["to"] == file_name}, key=str.upper)
    if names:
        summary["names"] = names
    if named_by:
        summary["named_by"] = named_by
    out["navigation"] = summary


def describe_form(cat: Catalog, name: str) -> Dict[str, Any]:
    """One module's shape: its blocks and items, what fires where, what it
    runs, and which tables it touches. Given a report, the report's shape."""
    if cat.find(name)["family"] == "report":
        return describe_report(cat, name)
    module = cat.module(name)
    pk = module["form_file_pk"]

    optional = [c for c in ("database_block", "query_data_source_name")
                if _have(cat, "form_blocks", c)]
    counts = []
    if _have(cat, "form_items", "form_block_fk"):
        counts.append("(SELECT count(*) FROM form_items i"
                      " WHERE i.form_block_fk = b.form_block_pk) AS items")
    if _have(cat, "form_triggers", "parent_kind", "parent_pk"):
        counts.append("(SELECT count(*) FROM form_triggers t"
                      " WHERE t.parent_kind = 'Block'"
                      "   AND t.parent_pk = b.form_block_pk) AS triggers")
    blocks = [{
        "block": r["form_block_name"],
        "items": value(r, "items"),
        "database_block": value(r, "database_block"),
        "data_source": value(r, "query_data_source_name"),
        "triggers": value(r, "triggers"),
    } for r in cat.rows(
        "SELECT b.form_block_pk, b.form_block_name%s%s"
        "  FROM form_blocks b WHERE b.form_file_fk = ?"
        " ORDER BY b.form_block_name"
        % ("".join(", b." + c for c in optional),
           "".join(", " + c for c in counts)), (pk,))]

    triggers = []
    if cat.has("form_triggers"):
        triggers = [_trigger_where(r) for r in cat.rows(
            _TRIGGER_SQL + " WHERE t.form_file_fk = ?"
            " ORDER BY scope_order, blk, item, name", (pk,))]

    units = []
    if _have(cat, "form_program_units", "form_program_unit_name",
             "program_unit_type", "program_unit_text"):
        units = [{
            "unit": r["form_program_unit_name"],
            "kind": value(r, "program_unit_type"),
            "lines": len((value(r, "program_unit_text") or "").splitlines()),
        } for r in cat.rows(
            "SELECT form_program_unit_name, program_unit_type, program_unit_text"
            "  FROM form_program_units WHERE form_file_fk = ?"
            " ORDER BY form_program_unit_name", (pk,))]

    tables = []
    if cat.has("form_table_usage"):
        tables = [{
            "table": r["table_name"],
            "operations": "".join(op for op, col in (("C", "creates"), ("R", "reads"),
                                                     ("U", "updates"), ("D", "deletes"))
                                  if (value(r, col) or "").upper() == "Y"),
        } for r in cat.rows(
            "SELECT * FROM form_table_usage WHERE form_file_fk = ?"
            " ORDER BY table_name", (pk,))]

    menu_code = [c for c in _code(cat, file_pk=pk, family="form")
                 if c["kind"] in ("menu item", "startup code")]
    out = {
        "file": module["file_name"],
        "kind": value(module, "module_kind") or value(module, "file_kind"),
        "module_name": module["module_name"],
        "title": value(module, "title"),
        "first_block": value(module, "first_navigation_block_name"),
        "menu": value(module, "menu_module"),
        "blocks": blocks,
        "triggers": triggers,
        "program_units": units,
        "tables": tables,
        "counts": {"blocks": len(blocks), "triggers": len(triggers),
                   "program_units": len(units), "tables": len(tables)},
    }
    runs = [r for r in _report_runs(cat) if r["form"] == module["file_name"]]
    if runs:
        out["runs_reports"] = [{"report_object": r["report_object"], "filename": r["filename"],
                                "report_file": r["report_file"],
                                **({"candidates": r["candidates"]} if r["candidates"] else {})}
                               for r in runs]
        missing = [r["filename"] for r in runs
                   if r["filename"] and not r["report_file"] and not r["candidates"]]
        unnamed = [r["report_object"] for r in runs if not r["filename"]]
        # Oracle's Forms Migration Assistant turns each RUN_PRODUCT(REPORTS, ...)
        # into rp2rro.rp2rro_run_product(...) and adds one report object, RP2RRO,
        # with no file: the library sets the file on each call.
        generic = [n for n in unnamed if (n or "").upper() == "RP2RRO"]
        others = [n for n in unnamed if (n or "").upper() != "RP2RRO"]
        notes = []
        if generic:
            notes.append("RP2RRO is the generic report object Oracle's Forms Migration "
                         "Assistant adds: rp2rro.rp2rro_run_product sets its file on each "
                         "call, so the reports this form runs are named in those calls "
                         "(search for rp2rro_run_product).")
        if others:
            notes.append("%s name%s no .rdf file: the form sets one at run time with "
                         "SET_REPORT_OBJECT_PROPERTY(..., REPORT_FILENAME, ...)."
                         % (", ".join(others), "s" if len(others) == 1 else ""))
        if notes:
            out["runs_reports_unnamed_note"] = " ".join(notes)
        if missing:
            out["runs_reports_note"] = (
                "Not loaded: %s. %s" % (", ".join(missing),
                "Open the reports database with --reports to match them."
                if "report" not in cat.families else
                "No report file of that name is in the reports database."))
    _navigation_summary(cat, out, module["file_name"])
    if menu_code:
        # A menu's PL/SQL is in its items' commands and its startup code;
        # read_code gives the source of any of them.
        out["menu_code"] = [{"kind": c["kind"], "on": c["on"],
                             "lines": len((c["body"] or "").splitlines())}
                            for c in menu_code]
        out["counts"]["menu_code"] = len(menu_code)
    links = _links(cat, pk)
    if links:
        # One pointer may name the library's file and another only its module;
        # the same library, so the file a sibling pointer gives stands for both.
        file_of = {}
        for l in links.values():
            got = _parent_file(cat, l)
            if got and l.get("parent_module"):
                file_of.setdefault(l["parent_module"].upper(), got)

        def parent(l):
            return (_parent_file(cat, l)
                    or file_of.get((l.get("parent_module") or "").upper())
                    or l.get("parent_module"))
        unresolved = sorted({parent(l) for l in links.values()
                             if (l.get("resolved") or "N") != "Y"} - {None})
        out["subclassing"] = {
            "objects": len(links),
            "resolved": sum(1 for l in links.values() if l.get("resolved") == "Y"),
            "from_files": sorted({parent(l) for l in links.values()} - {None}),
            "objects_listed": sorted(
                ("%s (%s)" % (l.get("owner_name") or "?", l.get("parent_object") or "?"))
                for l in links.values())[:40],
            "note": "Properties these take from their parent are in "
                    "form_inherited_properties; effective() puts the two together.",
        }
        if unresolved:
            out["subclassing"]["unresolved_parents"] = unresolved
    return out


# ------------------------------------------------------------------- code

# One row per trigger, with the object it fires on resolved. A trigger's owner
# is a parent_pk and a parent_kind; the name of that owner lives in whichever
# table the kind points at, which is why every question about triggers starts
# with this join rather than with form_triggers alone.
_TRIGGER_SQL = """
SELECT t.form_trigger_pk          AS pk,
       t.form_trigger_name        AS name,
       t.parent_kind              AS kind,
       t.trigger_text             AS body,
       f.file_name                AS file_name,
       f.module_kind              AS module_kind,
       coalesce(b.form_block_name, bi.form_block_name) AS blk,
       i.form_item_name           AS item,
       CASE t.parent_kind WHEN 'FormModule' THEN 0 WHEN 'Block' THEN 1 ELSE 2 END AS scope_order
  FROM form_triggers t
  JOIN form_files  f  ON f.form_file_pk  = t.form_file_fk
  LEFT JOIN form_blocks b  ON t.parent_kind = 'Block' AND b.form_block_pk = t.parent_pk
  LEFT JOIN form_items  i  ON t.parent_kind = 'Item'  AND i.form_item_pk  = t.parent_pk
  LEFT JOIN form_blocks bi ON bi.form_block_pk = i.form_block_fk
"""


def _trigger_where(row) -> Dict[str, Any]:
    """Where a trigger lives, said the way a developer says it."""
    kind = value(row, "kind")
    if kind == "Item" and value(row, "blk") and value(row, "item"):
        on = "%s.%s" % (row["blk"], row["item"])
    elif kind == "Block" and value(row, "blk"):
        on = row["blk"]
    else:
        on = "form"
    out = {"trigger": row["name"], "on": on, "scope": kind or "FormModule"}
    body = value(row, "body")
    if body is not None:
        out["lines"] = len(body.splitlines())
    return out


def read_code(cat: Catalog, name: str, unit: str,
              on: Optional[str] = None) -> Dict[str, Any]:
    """The source of one trigger, program unit, menu item or menu startup code.

    A menu item is named as ITEM or MENU.ITEM; a menu module's startup code
    as STARTUP_CODE.

    ``unit`` is a trigger name (WHEN-BUTTON-PRESSED) or a program unit name
    (CHECK_PACKAGE_FAILURE). A trigger name repeats across a module -- every
    button has a WHEN-BUTTON-PRESSED -- so ``on`` narrows it to one object
    (TOOLBAR.PB_SAVE, or the block).
    """
    wanted = (unit or "").strip()
    if not wanted:
        raise CatalogError("name a trigger or program unit")
    found = cat.find(name)
    if found["family"] == "report":
        return _read_report_code(cat, found, wanted)
    module = found
    pk = module["form_file_pk"]

    units = cat.rows(
        "SELECT form_program_unit_name AS name, program_unit_type AS kind,"
        "       program_unit_text AS body"
        "  FROM form_program_units"
        " WHERE form_file_fk = ? AND upper(form_program_unit_name) = upper(?)",
        (pk, wanted))
    if units:
        r = units[0]
        out = {"file": module["file_name"], "unit": r["name"],
               "kind": value(r, "kind") or "Program Unit"}
        out.update(clip(value(r, "body"), CODE_LIMIT))
        return out


    # A menu's code: an item's command (ORDERS, or MAIN_MENU.ORDERS), or the
    # menu module's startup code.
    menu = [c for c in _code(cat, file_pk=pk, family="form")
            if c["kind"] in ("menu item", "startup code")
            and (wanted.upper() in (c["name"].upper(), c["on"].upper())
                 or (c["kind"] == "startup code"
                     and wanted.upper().replace(" ", "_") == "STARTUP_CODE"))]
    if on and len(menu) > 1:
        menu = [c for c in menu if c["on"].upper().startswith(on.strip().upper() + ".")] or menu
    if len(menu) > 1:
        return {"file": module["file_name"], "menu_item": wanted,
                "several": [{"on": c["on"]} for c in menu],
                "note": "%d menu items are called %s; pass on= the menu, or name it "
                        "MENU.ITEM." % (len(menu), wanted)}
    if menu:
        c = menu[0]
        out = {"file": module["file_name"], "kind": c["kind"], "name": c["name"],
               "on": c["on"]}
        out.update(clip(c["body"], CODE_LIMIT))
        return out

    rows = cat.rows(_TRIGGER_SQL + " WHERE t.form_file_fk = ? AND upper(t.form_trigger_name) = upper(?)"
                    " ORDER BY scope_order, blk, item", (pk, wanted))
    if not rows:
        raise CatalogError(
            "%s has no trigger, program unit or menu item called %s"
            % (module["file_name"], wanted))
    if on:
        want = on.strip().upper()
        rows = [r for r in rows if _trigger_where(r)["on"].upper() == want] or rows
    if len(rows) > 1 and not on:
        return {
            "file": module["file_name"], "trigger": wanted,
            "several": [_trigger_where(r) for r in rows],
            "note": "%s fires on %d objects here; pass on= to pick one."
                    % (wanted, len(rows)),
        }
    r = rows[0]
    out = {"file": module["file_name"]}
    out.update(_trigger_where(r))
    body = value(r, "body")
    if not (body or "").strip():
        # A trigger subclassed from a library has no body of its own: what
        # it runs is the parent's, which the loader recorded rather than
        # leaving this to read as an empty trigger.
        taken = _inherited(cat, r["pk"]).get("trigger_text")
        if taken:
            link = _links(cat, pk).get(r["pk"])
            out["inherited_from"] = {"file": _parent_file(cat, link),
                                     "object": taken["from_object"]}
            if not out["inherited_from"]["file"]:
                out["inherited_from"]["module_name"] = taken["from_module"]
            out.update(clip(taken["value"], CODE_LIMIT))
            out["lines"] = len((taken["value"] or "").splitlines())
            return out
    out.update(clip(body, CODE_LIMIT))
    return out


# ----------------------------------------------------------------- search

#: What a piece of code is, in the order an answer lists them within a file.
CODE_KINDS = ("trigger", "program unit", "menu item", "startup code", "query")


def _code(cat: Catalog, file_pk=None, like: Optional[str] = None,
          family: Optional[str] = None):
    """Every piece of PL/SQL in the application, one dict per piece.

    Triggers and program units, and a menu's code too: each menu item's
    command and the menu module's startup code, which is where most of an
    application's CALL_FORM and OPEN_FORM calls live. ``kind`` is one of
    CODE_KINDS; ``on`` is where it sits, said the way a developer says it
    (ORDERS.CUSTOMERID, MAIN_MENU.ORDERS); ``unit_type`` is a program unit's
    own kind (Procedure, Package Spec). One function, so search, read_code
    and list_code cannot disagree about what code there is.

    A report's code is its program units -- every formula, format trigger and
    report trigger is one -- and its queries' SQL. ``family`` says which kind
    of file ``file_pk`` is, since a form's id and a report's can be the same
    number when they come from two databases.
    """
    forms = "form" in cat.families and family in (None, "form")
    reports = "report" in cat.families and family in (None, "report")
    where_file = " AND f.form_file_pk = ?" if file_pk is not None else ""
    def args(*extra):
        return tuple(a for a in (like,) + ((file_pk,) if file_pk is not None else ()) + extra
                     if a is not None)

    if forms and cat.has("form_triggers"):
        sql = _TRIGGER_SQL + " WHERE 1 = 1"
        if like is not None:
            sql += " AND t.trigger_text LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_file.replace("f.form_file_pk", "t.form_file_fk")
                          + " ORDER BY file_name, scope_order, blk, item, name", args()):
            where = _trigger_where(r)
            yield {"pk": r["pk"], "file": r["file_name"], "module_kind": r["module_kind"],
                   "kind": "trigger", "name": where["trigger"], "on": where["on"],
                   "scope": where["scope"], "body": value(r, "body")}

    if forms and _have(cat, "form_program_units", "form_program_unit_name", "program_unit_text"):
        sql = ("SELECT f.file_name AS file_name, f.module_kind AS module_kind,"
               "       p.form_program_unit_name AS name, p.program_unit_type AS unit_type,"
               "       p.program_unit_text AS body"
               "  FROM form_program_units p JOIN form_files f ON f.form_file_pk = p.form_file_fk"
               " WHERE 1 = 1")
        if like is not None:
            sql += " AND p.program_unit_text LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_file + " ORDER BY f.file_name, p.form_program_unit_name",
                          args()):
            yield {"file": r["file_name"], "module_kind": r["module_kind"],
                   "kind": "program unit", "name": r["name"], "on": r["name"],
                   "unit_type": value(r, "unit_type"), "body": value(r, "body")}

    if forms and _have(cat, "form_menu_items", "menu_item_code"):
        sql = ("SELECT f.file_name AS file_name, f.module_kind AS module_kind,"
               "       m.form_menu_name AS menu, m.form_menu_item_name AS name,"
               "       m.menu_item_code AS body"
               "  FROM form_menu_items m JOIN form_files f ON f.form_file_pk = m.form_file_fk"
               " WHERE m.menu_item_code IS NOT NULL")
        if like is not None:
            sql += " AND m.menu_item_code LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_file + " ORDER BY f.file_name, m.form_menu_name,"
                          " m.form_menu_item_pk", args()):
            yield {"file": r["file_name"], "module_kind": r["module_kind"],
                   "kind": "menu item", "name": r["name"],
                   "on": "%s.%s" % (r["menu"], r["name"]) if r["menu"] else r["name"],
                   "body": r["body"]}

    # A menu module's startup code has no column of its own; the loader keeps
    # it with the other properties that have none.
    if forms and _have(cat, "form_properties", "property_name", "property_value"):
        sql = ("SELECT f.file_name AS file_name, f.module_kind AS module_kind,"
               "       p.property_value AS body"
               "  FROM form_properties p JOIN form_files f ON f.form_file_pk = p.form_file_fk"
               " WHERE p.property_name = 'StartupCode'")
        if like is not None:
            sql += " AND p.property_value LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_file + " ORDER BY f.file_name", args()):
            yield {"file": r["file_name"], "module_kind": r["module_kind"],
                   "kind": "startup code", "name": "STARTUP_CODE", "on": "menu module",
                   "body": r["body"]}

    if not reports:
        return
    where_rep = " AND f.report_file_pk = ?" if file_pk is not None else ""
    if _have(cat, "report_program_units", "report_program_unit_name", "source_text"):
        sql = ("SELECT f.file_name AS file_name, p.report_program_unit_name AS name,"
               "       p.source_text AS body"
               "  FROM report_program_units p"
               "  JOIN report_files f ON f.report_file_pk = p.report_file_fk WHERE 1 = 1")
        if like is not None:
            sql += " AND p.source_text LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_rep + " ORDER BY f.file_name, p.report_program_unit_pk",
                          args()):
            yield {"file": r["file_name"], "module_kind": "report",
                   "kind": "program unit", "name": r["name"], "on": r["name"],
                   "body": _dedent(r["body"])}
    if _have(cat, "report_queries", "report_query_name", "select_text"):
        sql = ("SELECT f.file_name AS file_name, q.report_query_name AS name,"
               "       q.select_text AS body"
               "  FROM report_queries q"
               "  JOIN report_files f ON f.report_file_pk = q.report_file_fk WHERE 1 = 1")
        if like is not None:
            sql += " AND q.select_text LIKE ? ESCAPE '\\'"
        for r in cat.rows(sql + where_rep + " ORDER BY f.file_name, q.report_query_pk", args()):
            yield {"file": r["file_name"], "module_kind": "report",
                   "kind": "query", "name": r["name"], "on": r["name"], "body": r["body"]}


def _dedent(text: Optional[str]) -> Optional[str]:
    """A report program unit as Reports Builder shows it.

    The XML indents a function's source under its element and opens it on an
    empty line; that indentation is the file's layout, not the code's, and
    line numbers that count the empty first line are one off from Builder's.
    """
    if text is None:
        return None
    import textwrap
    return textwrap.dedent(text).strip("\n")


def _like(text: str) -> str:
    """A LIKE pattern that finds ``text`` as written: % and _ are not wildcards."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def search(cat: Catalog, text: str, limit: int = 50) -> Dict[str, Any]:
    """Every line of PL/SQL in the application that mentions something:
    triggers, program units, menu item commands and menu startup code.

    Plain text, case-insensitive. This is the tool that replaces opening
    forms one at a time to find where something is done.
    """
    needle = (text or "").strip()
    if not needle:
        raise CatalogError("give me something to search for")
    limit = max(1, min(int(limit or 50), MAX_ROWS))

    pieces = sorted(_code(cat, like=_like(needle)),
                    key=lambda c: (c["file"], CODE_KINDS.index(c["kind"])))
    hits: List[Dict[str, Any]] = []
    for c in pieces:
        for line_no, line in _matching_lines(c["body"], needle):
            hits.append({"file": c["file"], "object": c["on"],
                         "kind": (c.get("unit_type") or c["kind"]).lower(),
                         "name": c["name"], "line": line_no, "text": line})

    found = len(hits)
    return {"searched_for": needle, "hits": hits[:limit], "found": found,
            "shown": min(found, limit),
            "note": None if found <= limit else
                    "%d lines match; %d shown. Narrow the text or raise limit."
                    % (found, limit)}


def list_code(cat: Catalog, file: Optional[str] = None, kind: Optional[str] = None,
              name: Optional[str] = None, module_kind: Optional[str] = None,
              limit: int = MAX_ROWS) -> Dict[str, Any]:
    """Every trigger, program unit, menu item command, menu startup code and
    report query, across the application or in one file -- without the source.

    ``kind`` narrows to one of CODE_KINDS; ``name`` to one name (ON-ERROR,
    CHECK_PACKAGE_FAILURE), with % as a wildcard; ``module_kind`` to the files
    of one kind (form, menu, library, object library). read_code gives the
    source of any one of them.
    """
    found = cat.find(file) if file else None
    file_pk, family = (found["pk"], found["family"]) if found else (None, None)
    if kind:
        kind = kind.strip().lower().replace("_", " ").rstrip("s")
        kind = {"menu": "menu item", "startup": "startup code", "sql": "query",
                "unit": "program unit", "pu": "program unit"}.get(kind, kind)
        if kind not in CODE_KINDS:
            raise CatalogError("kind is one of: %s" % ", ".join(CODE_KINDS))
    pattern = None
    if name:
        pattern = re.compile("^%s$" % re.escape(name.strip()).replace("%", ".*"), re.I)
    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS * 5))

    rows = []
    for c in _code(cat, file_pk=file_pk, family=family):
        if kind and c["kind"] != kind:
            continue
        if module_kind and (c["module_kind"] or "").lower() != module_kind.strip().lower():
            continue
        if pattern and not pattern.match(c["name"] or ""):
            continue
        row = {"file": c["file"], "kind": c["kind"], "name": c["name"], "on": c["on"],
               "lines": len((c["body"] or "").splitlines())}
        if c.get("unit_type"):
            row["unit_type"] = c["unit_type"]
        if c["kind"] == "trigger":
            row["scope"] = c["scope"]
        rows.append(row)
    rows.sort(key=lambda r: (r["file"], CODE_KINDS.index(r["kind"])))
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"code": rows[:limit], "found": len(rows), "counts": counts,
            "note": None if len(rows) <= limit else
                    "%d pieces of code match; %d shown. Narrow by file, kind or name."
                    % (len(rows), limit)}


def _matching_lines(body: Optional[str], needle: str):
    low = needle.lower()
    for i, line in enumerate((body or "").splitlines(), start=1):
        if low in line.lower():
            stripped = line.strip()
            yield i, (stripped[:SNIPPET] + "…" if len(stripped) > SNIPPET else stripped)


def _table_key(name: Optional[str]) -> str:
    """A table name as a comparison key: no quotes, no case.

    Forms keeps a data source the way the developer typed it, and a table
    whose name needs quoting -- Northwind's "Order Details" -- is stored with
    its double quotes on, while the usage the loader works out has them off.
    Compared as written, the two never met, and where_used("Order Details")
    found the modules but none of the blocks.
    """
    return (name or "").replace('"', "").strip().upper()


def _names_table(stored: Optional[str], wanted: str) -> bool:
    """Whether a data source names this table, with or without its schema:
    HR.EMPLOYEES and "HR"."EMPLOYEES" both name EMPLOYEES."""
    got = _table_key(stored)
    return bool(got) and (got == wanted or got.endswith("." + wanted))


_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")


def _oracle_name(raw: Optional[str]) -> str:
    """A table name the way Oracle holds it, for showing back.

    Written with quotes, it stays as written. Without them, an ordinary
    identifier folds to upper case, and one that could not have been written
    unquoted -- a space, a hyphen -- must have been quoted, so it gets its
    quotes back: form_table_usage keeps "Order Details" as Order Details. A
    mixed-case ordinary name there (Orders) may have been quoted or not; the
    usage table has lost which, so it is taken as unquoted.
    """
    raw = (raw or "").strip()
    if not raw or '"' in raw:
        return raw
    return ".".join(p.upper() if _PLAIN.fullmatch(p) else '"%s"' % p
                    for p in raw.split("."))


def _table_of(shown: str) -> str:
    """The table part of a shown name, schema dropped: one per real table."""
    last = re.findall(r'"[^"]*"|[^.]+', shown)[-1:] or [shown]
    return last[0]


#: What each verb does to a table, in the C/R/U/D letters where_used reports.
_VERB_OPS = {"query": "R", "insert": "C", "update": "U", "delete": "D", "merge": "CU"}


def _code_table_refs(cat: Catalog, family: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every table a piece of code names, as the loader recorded it.

    form_code_tables and report_code_tables are filled by sqling's
    CodeAnalysis when the database is built; a database built before they
    existed has neither, and this returns nothing for it.
    """
    refs = []
    for fam, table, files, pk, fk in (
            ("form", "form_code_tables", "form_files", "form_file_pk", "form_file_fk"),
            ("report", "report_code_tables", "report_files", "report_file_pk", "report_file_fk")):
        if fam not in cat.families or (family and fam != family) or not cat.has(table):
            continue
        for r in cat.rows("SELECT f.file_name, t.owner_type, t.owner_name, t.table_name, t.verb"
                          "  FROM %s t JOIN %s f ON f.%s = t.%s ORDER BY f.file_name"
                          % (table, files, pk, fk)):
            refs.append({"file": r["file_name"], "kind": fam,
                         "in": _words(r["owner_type"] or "code"), "name": r["owner_name"],
                         "table": r["table_name"], "verb": r["verb"],
                         "operations": _VERB_OPS.get(r["verb"], "")})
    return refs


def _code_tables_missing(cat: Catalog) -> List[str]:
    """The families loaded whose database predates code table usage."""
    return [fam for fam, table in (("form", "form_code_tables"), ("report", "report_code_tables"))
            if fam in cat.families and not cat.has(table)]


#: Tables that are real but deliberately left out of table usage, and why.
_UNCOUNTED = {
    "DUAL": "is Oracle's one-row dummy table: selecting from it reads no "
            "application data (SELECT SYSDATE INTO v FROM DUAL).",
}


def where_used(cat: Catalog, table: str) -> Dict[str, Any]:
    """Which modules read, insert, update or delete a table -- and where the
    name turns up in code, which is the half a block's data source does not
    say (a cursor, a lookup, dynamic SQL).

    Names match without regard to quotes or case, because whoever asks should
    not need the exact spelling. The answer names the table the way the
    application does, and says so when two spellings that Oracle would keep
    apart -- "Orders" and ORDERS -- both matched.
    """
    asked = (table or "").strip()
    if not asked.replace('"', "").strip():
        raise CatalogError("name a table")
    wanted = _table_key(asked)

    forms = "form" in cat.families
    usage_rows = cat.rows(
        "SELECT f.file_name AS file_name, u.*"
        "  FROM form_table_usage u JOIN form_files f ON f.form_file_pk = u.form_file_fk"
        " ORDER BY f.file_name") if forms and cat.has("form_table_usage") else []
    usage = [{
        "file": r["file_name"], "kind": "form", "table": _oracle_name(r["table_name"]),
        "operations": "".join(op for op, col in (("C", "creates"), ("R", "reads"),
                                                 ("U", "updates"), ("D", "deletes"))
                              if (value(r, col) or "").upper() == "Y"),
    } for r in usage_rows if _names_table(r["table_name"], wanted)]

    # A block can query one thing and write another -- a view to read, the
    # table under it to write -- so both of its names count.
    block_rows = []
    if forms:
        target = "b.dml_data_target_name" if "dml_data_target_name" in cat.columns(
            "form_blocks") else "NULL"
        block_rows = cat.rows(
            "SELECT f.file_name AS file_name, b.form_block_name,"
            "       b.query_data_source_name, %s AS dml_target"
            "  FROM form_blocks b JOIN form_files f ON f.form_file_pk = b.form_file_fk"
            " ORDER BY f.file_name, b.form_block_name" % target)
    blocks = [{"file": r["file_name"], "block": r["form_block_name"],
               "data_source": r["query_data_source_name"],
               "dml_target": r["dml_target"]}
              for r in block_rows
              if _names_table(r["query_data_source_name"], wanted)
              or _names_table(r["dml_target"], wanted)]

    # The code's half: every trigger, program unit, record group, FROM-clause
    # block and report query that names the table, as the loader recorded it.
    # A form's code uses join its blocks' -- a form that only updates a table
    # from a trigger updates it all the same.
    code_refs = _code_table_refs(cat)
    in_code = [ref for ref in code_refs if _names_table(ref["table"], wanted)]
    merged: Dict[Tuple[str, str, str], set] = {}
    for u in usage:
        merged.setdefault((u["file"], u["kind"], u["table"]), set()).update(u["operations"])
    for ref in in_code:
        merged.setdefault((ref["file"], ref["kind"], _oracle_name(ref["table"])), set()).update(
            ref["operations"])
    usage = [{"file": f, "kind": k, "table": t,
              "operations": "".join(op for op in "CRUD" if op in ops)}
             for (f, k, t), ops in sorted(merged.items(), key=lambda x: (x[0][0].upper(), x[0][2]))]
    code_uses = [{"file": ref["file"], "kind": ref["kind"], "in": ref["in"], "name": ref["name"],
                  "table": _oracle_name(ref["table"]), "verb": ref["verb"]}
                 for ref in in_code]

    # The spellings that matched, blocks first: a data source is kept as it
    # was typed, quotes and all, where the usage table has them taken off.
    # The usage table's copy of a name a block already spells is the same
    # table with its quotes lost, not a second one.
    # A schema is part of the name: FA.VENDORS_V and FIS.VENDORS_V are two
    # tables, and so is a bare VENDORS_V, which is whichever the connecting
    # user sees. Each is kept, and named on the entries that use it.
    spellings: List[str] = []
    typed = set()
    for b in blocks:
        for raw in (b["data_source"], b["dml_target"]):
            if _names_table(raw, wanted):
                spellings.append(_oracle_name(raw))
                typed.add(raw.replace('"', "").strip())
    for r in usage_rows:
        if _names_table(r["table_name"], wanted) \
                and r["table_name"].strip() not in typed:
            spellings.append(_oracle_name(r["table_name"]))
    for ref in in_code:
        spellings.append(_oracle_name(ref["table"]))
    names = []
    for shown in spellings:
        if shown not in names:
            names.append(shown)
    names.sort()
    # The usage copy of a block's quoted mixed-case name (Orders, from "Orders")
    # reads back as ORDERS; the entry should say what the block says.
    for u in usage:
        if u["kind"] == "form" and u["table"] not in names:
            u["table"] = next((n for n in names if n.replace('"', "") ==
                               u["table"].replace('"', "") or
                               n.replace('"', "").upper() == u["table"].upper()), u["table"])

    code = search(cat, asked.replace('"', "").strip(), limit=MAX_ROWS)
    out: Dict[str, Any] = {
        "table": names[0] if len(names) == 1 else _oracle_name(asked.replace('"', "")
                                                               if '"' not in asked else asked),
        "found": bool(names),
        "modules": usage, "blocks": blocks, "code_uses": code_uses,
        "mentions_in_code": code["hits"],
        "counts": {"modules": len(usage), "blocks": len(blocks),
                   "code_uses": len(code_uses), "code_lines": code["found"]},
    }
    missing = _code_tables_missing(cat)
    if missing:
        said = {"form": "for forms only blocks' data sources are counted, not code",
                "report": "for reports nothing is counted"}
        out["code_uses_note"] = (
            "This database was built before the tables code names were recorded, so %s. "
            "Build it again on the Batch Oracle Forms or Reports page to count the code too."
            % " and ".join(said[f] for f in missing))
    # DUAL, under any schema (SYS.DUAL), is left out of table usage on purpose,
    # and the answer says so before anything else -- not "two tables, DUAL and
    # SYS.DUAL", which is what counting the code's mentions of it made it say.
    bare = wanted.rsplit(".", 1)[-1]
    if bare in _UNCOUNTED:
        on_dual = [b for b in blocks]
        out.update({"table": bare, "found": False, "modules": [], "code_uses": [],
                    "blocks": on_dual})
        out["counts"].update({"modules": 0, "code_uses": 0, "blocks": len(on_dual)})
        out.pop("spellings", None)
        out["note"] = ("%s %s It is not counted as table usage.%s%s" % (
            bare, _UNCOUNTED[bare],
            " %d block(s) are based on it (a control block, usually)." % len(on_dual)
            if on_dual else "",
            " The %d line(s) of code that mention it are under mentions_in_code."
            % code["found"] if code["found"] else ""))
        out.pop("code_uses_note", None)
        return out
    if len(names) > 1:
        out["spellings"] = names
        plain = [n.replace('"', "").upper() for n in names]
        schemas = {p.rsplit(".", 1)[0] if "." in p else "" for p in plain}
        differ = []
        if len(schemas) > 1:
            differ.append("schema" + (" (a name with none is whichever table the "
                                      "connecting user sees)" if "" in schemas else ""))
        if len(set(plain)) < len(names):
            differ.append("case (Oracle keeps a quoted name's case)")
        out["note"] = ("%d different tables answer to that name: %s. They differ in %s. "
                       "Each file, block and report use above says which it uses."
                       % (len(names), ", ".join(names), " and ".join(differ) or "spelling"))
    elif wanted in _UNCOUNTED:
        out["note"] = ("%s %s It is not counted as table usage.%s" % (
            wanted, _UNCOUNTED[wanted],
            " The %d line(s) of code that mention it are under mentions_in_code."
            % code["found"] if code["found"] else ""))
    elif not names:
        known = sorted({_oracle_name(r["table_name"]) for r in usage_rows if r["table_name"]}
                       | {_oracle_name(r[c]) for r in block_rows
                          for c in ("query_data_source_name", "dml_target") if r[c]}
                       | {_oracle_name(ref["table"]) for ref in code_refs})
        keys = {_table_key(n): n for n in known}
        close = difflib.get_close_matches(wanted, list(keys), n=3, cutoff=0.75)
        if close:
            out["did_you_mean"] = [keys[k] for k in close]
        out["note"] = ("No table called %s in this application%s%s" % (
            asked,
            "; did you mean %s?" % " or ".join(keys[k] for k in close) if close else ".",
            " The text appears in %d line(s) of code, listed under mentions_in_code "
            "-- a text match, not a table." % code["found"] if code["found"] else ""))
    return out


# ---------------------------------------------------------------- reports

#: How each column that names a program unit reads, said of the object that
#: holds it. Anything else that turns out to name one is said in its own words.
_POINTER_WORDS = {
    "before_report_trigger": "before report trigger",
    "after_report_trigger": "after report trigger",
    "before_parameter_form_trigger": "before parameter form trigger",
    "after_parameter_form_trigger": "after parameter form trigger",
    "between_pages_trigger": "between pages trigger",
    "adv_format_trigger": "format trigger",
    "source": "formula",
}


def _words(name: str) -> str:
    """validationTrigger -> validation trigger; before_report_trigger likewise."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name or "").replace("_", " ").lower()


def _report_objects(cat: Catalog, file_pk) -> Dict[Any, str]:
    """Every object of one report by its id, said as "parameter P_ID"."""
    out: Dict[Any, str] = {}
    for table in cat.tables():
        if not table.startswith("report_") or table in ("report_properties",):
            continue
        columns = cat.columns(table)
        if table == "report_files":
            out[file_pk] = "the report"
            continue
        own = table[len("report_"):]
        singular = re.sub(r"ies$", "y", own)
        singular = re.sub(r"(?<!s)s$", "", singular) if singular == own else singular
        pk = "report_%s_pk" % singular
        name = "report_%s_name" % singular
        if "report_file_fk" not in columns or pk not in columns:
            continue
        label = singular.replace("_", " ")
        for r in cat.rows("SELECT %s AS pk, %s AS name FROM %s WHERE report_file_fk = ?"
                          % (pk, name if name in columns else "NULL", table), (file_pk,)):
            out[r["pk"]] = "%s %s" % (label, r["name"] or "?")
    return out


#: What to call a report object when saying its code is somewhere else.
_HOLDER_WORDS = {"formula": "formula column", "placeholder": "placeholder column",
                 "summary": "summary column", "repeating_frame": "repeating frame"}


def _report_pointers(cat: Catalog, file_pk) -> List[Dict[str, Any]]:
    """Every place a report's object names one of its program units.

    A report's PL/SQL is all program units; an object points at the one it
    uses by name -- the report's beforeReportTrigger, a formula's source, a
    field's format trigger, a parameter's validation trigger. Rather than a
    list of the pointers known today, every column of every report table is
    read for a value that names one of this report's units, and so is
    report_properties, where a pointer the schema has no column for is kept.

    One dict per pointer: the unit (lower-cased), what it is used as ("formula",
    "format trigger"), and its holder's kind and name ("field", "F_TOTAL");
    the holder is None for the report itself.
    """
    units = {r["n"].lower() for r in cat.rows(
        "SELECT report_program_unit_name AS n FROM report_program_units"
        " WHERE report_file_fk = ?", (file_pk,)) if r["n"]}
    found: List[Dict[str, Any]] = []
    if not units:
        return found
    holders = _report_objects(cat, file_pk)

    def add(unit, word, holder):
        kind, _, name = (holder or "").partition(" ")
        found.append({"unit": unit.lower(), "as": word,
                      "holder_kind": kind if holder and holder != "the report" else None,
                      "holder": name if holder and holder != "the report" else None})

    for table in cat.tables():
        if not table.startswith("report_") or table in ("report_program_units",
                                                         "report_properties"):
            continue
        columns = cat.columns(table)
        key = "report_file_pk" if table == "report_files" else "report_file_fk"
        pk_col = next((c for c in columns if c.endswith("_pk")), None)
        if key not in columns:
            continue
        name_col = next((c for c in columns if c.startswith("report_") and c.endswith("_name")),
                        None)
        text_cols = [c for c in columns
                     if not c.endswith(("_pk", "_fk")) and c != name_col
                     and c not in ("parent_kind", "file_name", "file_kind", "module_name",
                                   "module_kind", "module_version", "loaded_at")]
        for row in cat.rows("SELECT * FROM %s WHERE %s = ?" % (table, key), (file_pk,)):
            holder = "the report" if table == "report_files" else holders.get(row[pk_col])
            for col in text_cols:
                got = row[col]
                if isinstance(got, str) and got.lower() in units:
                    add(got, _POINTER_WORDS.get(col, _words(col)), holder)
    if _have(cat, "report_properties", "owner_pk", "property_name", "property_value"):
        for r in cat.rows("SELECT owner_pk, property_name, property_value FROM report_properties"
                          " WHERE report_file_fk = ?", (file_pk,)):
            got = r["property_value"]
            if isinstance(got, str) and got.lower() in units:
                add(got, _words(r["property_name"]), holders.get(r["owner_pk"]))
    return found


def _report_roles(cat: Catalog, file_pk) -> Dict[str, List[str]]:
    """What each of a report's program units is for, by lower-cased name:
    "formula of formula CF_TAX", "before report trigger"."""
    roles: Dict[str, List[str]] = {}
    for p in _report_pointers(cat, file_pk):
        roles.setdefault(p["unit"], []).append(
            p["as"] if not p["holder"] else "%s of %s %s" % (p["as"], p["holder_kind"].replace("_", " "), p["holder"]))
    return roles


def _report_key(path: Optional[str]) -> str:
    """What a report is called for matching: its file's name, without folder or
    extension. A form names the .rdf it runs (/apps/reports/MONTHLY.rdf, or
    just MONTHLY); the loaded file is reports/MONTHLY.rdf."""
    leaf = (path or "").replace("\\", "/").strip().rsplit("/", 1)[-1]
    if leaf.lower().endswith(".xml"):
        leaf = leaf[:-4]
    return re.sub(r"\.(rdf|rep|rex|jsp)$", "", leaf, flags=re.I).upper()


def _report_runs(cat: Catalog) -> List[Dict[str, Any]]:
    """Every report object in every form, with the loaded report it runs.

    A form's report object names its .rdf by Filename; matched on the file's
    name, never a module name. A Filename that names no loaded report is kept,
    with report_file None, so "runs a report that is not here" is said.
    """
    if not ("form" in cat.families and _have(cat, "form_reports", "filename")):
        return []
    by_key: Dict[str, List[str]] = {}
    if "report" in cat.families:
        for r in cat.rows("SELECT file_name FROM report_files"):
            by_key.setdefault(_report_key(r["file_name"]), []).append(r["file_name"])
    out = []
    for r in cat.rows("SELECT f.file_name, x.form_report_name, x.filename"
                      "  FROM form_reports x JOIN form_files f ON f.form_file_pk = x.form_file_fk"
                      " ORDER BY f.file_name, x.form_report_name"):
        # A report object need not name a file: the form can set it at run time
        # (SET_REPORT_OBJECT_PROPERTY), or the object is unused.
        matches = by_key.get(_report_key(r["filename"]), []) if r["filename"] else []
        out.append({"form": r["file_name"], "report_object": r["form_report_name"],
                    "filename": r["filename"],
                    "report_file": matches[0] if len(matches) == 1 else None,
                    "candidates": matches if len(matches) > 1 else None})
    return out


def describe_report(cat: Catalog, name: str) -> Dict[str, Any]:
    """One report's shape: parameters, queries with their SQL, groups, formulas,
    placeholders, program units and what each is for, and the forms that run it."""
    rep = cat.report(name)
    pk = rep["report_file_pk"]

    def rows(table, cols, order):
        if not cat.has(table):
            return []
        have = set(cat.columns(table))
        pick = [c for c in cols if c in have]
        return cat.rows("SELECT %s FROM %s WHERE report_file_fk = ? ORDER BY %s"
                        % (", ".join(pick), table, order), (pk,))

    validates: Dict[Any, str] = {}
    if _have(cat, "report_properties", "owner_pk", "property_name", "property_value"):
        validates = {r["owner_pk"]: r["property_value"] for r in cat.rows(
            "SELECT owner_pk, property_value FROM report_properties"
            " WHERE report_file_fk = ? AND property_name = 'validationTrigger'", (pk,))}
    params = []
    for r in rows("report_parameters", ["report_parameter_name", "datatype",
                  "initial_value", "label", "report_parameter_pk", "validation_trigger"],
                  "report_parameter_pk"):
        one = {"parameter": r["report_parameter_name"], "datatype": value(r, "datatype"),
               "initial_value": value(r, "initial_value"), "label": value(r, "label")}
        trigger = value(r, "validation_trigger") or validates.get(r["report_parameter_pk"])
        if trigger:
            one["validation_trigger"] = trigger
        params.append(one)
    queries = rows("report_queries", ["report_query_pk", "report_query_name", "select_text"],
                   "report_query_pk")
    groups = rows("report_groups", ["report_group_pk", "report_group_name", "parent_kind",
                                    "parent_pk"], "report_group_pk")
    query_of = {q["report_query_pk"]: q["report_query_name"] for q in queries}
    out_queries = [{"query": q["report_query_name"],
                    "groups": [g["report_group_name"] for g in groups
                               if value(g, "parent_pk") == q["report_query_pk"]],
                    **clip(value(q, "select_text"), CODE_LIMIT)} for q in queries]
    formulas = [{"formula": r["report_formula_name"], "datatype": value(r, "datatype"),
                 "unit": value(r, "source")}
                for r in rows("report_formulas", ["report_formula_name", "datatype", "source",
                              "report_formula_pk"], "report_formula_pk")]
    placeholders = [{"placeholder": r["report_placeholder_name"],
                     "datatype": value(r, "datatype"), "unit": value(r, "source")}
                    for r in rows("report_placeholders", ["report_placeholder_name", "datatype",
                                  "source", "report_placeholder_pk"], "report_placeholder_pk")]
    group_of = {g["report_group_pk"]: g["report_group_name"] for g in groups}
    summaries = [{"summary": r["report_summary_name"], "function": value(r, "function"),
                  "of": value(r, "source"),
                  "per": group_of.get(value(r, "parent_pk")) or "report",
                  "datatype": value(r, "datatype"), "label": value(r, "default_label")}
                 for r in rows("report_summaries", ["report_summary_name", "function", "source",
                               "parent_pk", "datatype", "default_label", "report_summary_pk"],
                               "report_summary_pk")]
    # A data link joins a child query to a parent group's column, the way a
    # master-detail relation does in a form.
    links = [{"parent_group": value(r, "parent_group"), "parent_column": value(r, "parent_column"),
              "child_query": value(r, "child_query"), "child_column": value(r, "child_column"),
              "condition": value(r, "condition"), "clause": value(r, "sql_clause")}
             for r in rows("report_links", ["parent_group", "parent_column", "child_query",
                           "child_column", "condition", "sql_clause", "report_link_pk"],
                           "report_link_pk")]
    roles = _report_roles(cat, pk)
    units = [{"unit": c["name"], "lines": len((c["body"] or "").splitlines()),
              "used_as": roles.get((c["name"] or "").lower(), [])}
             for c in _code(cat, file_pk=pk, family="report") if c["kind"] == "program unit"]
    triggers = [{"trigger": word, "unit": rep[col]}
                for col, word in _POINTER_WORDS.items()
                if col.endswith("_trigger") and col != "adv_format_trigger"
                and value(rep, col)]
    run_by = [{"form": r["form"], "report_object": r["report_object"]}
              for r in _report_runs(cat) if r["report_file"] == rep["file_name"]]
    used: Dict[str, Dict[str, Any]] = {}
    for ref in _code_table_refs(cat, family="report"):
        if ref["file"] != rep["file_name"]:
            continue
        one = used.setdefault(_table_key(ref["table"]), {
            "table": _oracle_name(ref["table"]), "operations": set(), "in": []})
        one["operations"].update(ref["operations"])
        one["in"].append("%s %s" % (ref["in"], ref["name"]))
    tables = [{"table": t["table"],
               "operations": "".join(op for op in "CRUD" if op in t["operations"]),
               "in": sorted(set(t["in"]))} for t in sorted(used.values(), key=lambda t: t["table"])]
    out = {
        "file": rep["file_name"], "kind": "report", "module_name": rep["module_name"],
        "version": value(rep, "module_version"),
        "parameters": params, "queries": out_queries,
        "groups": [{"group": g["report_group_name"],
                    "query": query_of.get(value(g, "parent_pk"))} for g in groups],
        "formulas": formulas, "placeholders": placeholders, "summaries": summaries,
        "links": links, "report_triggers": triggers, "program_units": units,
        "tables": tables, "run_by": run_by,
        "counts": {"parameters": len(params), "queries": len(out_queries),
                   "groups": len(groups), "formulas": len(formulas),
                   "placeholders": len(placeholders), "summaries": len(summaries),
                   "links": len(links), "program_units": len(units),
                   "tables": len(tables)},
    }
    _navigation_summary(cat, out, rep["file_name"])
    unused = [u["unit"] for u in units if not u["used_as"]]
    if unused:
        out["note"] = ("No object of this report points at %s; it may be called from "
                       "other PL/SQL -- search() will say." % ", ".join(unused))
    if "form" not in cat.families:
        out["run_by_note"] = ("No forms are loaded, so which forms run this report is "
                              "not known.")
    return out


def _read_report_code(cat: Catalog, rep: Dict[str, Any], wanted: str) -> Dict[str, Any]:
    """A report's program unit or query, by its own name -- or by the name of
    the object that uses it.

    A formula column is CF_TAX in the report and its PL/SQL is the function
    cf_taxformula; a field's format trigger, a parameter's validation trigger
    are the same arrangement. Asking for the object gives its unit, and says
    which unit that was.
    """
    code = {(c["name"] or "").upper(): c for c in _code(cat, file_pk=rep["pk"], family="report")}
    roles = _report_roles(cat, rep["pk"])

    def answer(c, via=None):
        out = {"file": rep["file_name"], "kind": c["kind"], "name": c["name"]}
        if c["kind"] == "program unit":
            out["used_as"] = roles.get(c["name"].lower(), [])
        if via:
            out["asked_for"] = via["holder"]
            out["note"] = "%s is a %s; its %s is the program unit %s." % (
                via["holder"], _HOLDER_WORDS.get(via["holder_kind"],
                                                 via["holder_kind"].replace("_", " ")),
                via["as"], c["name"])
        out.update(clip(c["body"], CODE_LIMIT))
        return out

    if wanted.upper() in code:
        return answer(code[wanted.upper()])
    held = [p for p in _report_pointers(cat, rep["pk"])
            if (p["holder"] or "").upper() == wanted.upper()]
    units = sorted({p["unit"] for p in held})
    if len(units) == 1 and units[0].upper() in code:
        return answer(code[units[0].upper()], via=held[0])
    if len(units) > 1:
        return {"file": rep["file_name"], "asked_for": wanted,
                "several": [{"unit": u, "used_as": roles.get(u, [])} for u in units],
                "note": "%s uses %d program units; name one." % (wanted, len(units))}
    raise CatalogError("%s has no program unit, query or object with code called %s"
                       % (rep["file_name"], wanted))


# -------------------------------------------------------------- effective

#: Columns that say where a row sits rather than what the object is like.
#: Left out of an object's properties, because they are the loader's, not
#: the form's.
PLUMBING = ("_pk", "_fk")
PLUMBING_NAMES = {"parent_pk", "parent_kind", "parent_filename",
                  "parent_module", "parent_module_type", "parent_name",
                  "parent_type", "file_name", "file_kind", "loaded_at",
                  "module_name", "module_kind", "module_version"}


#: Rows that point at an object rather than being one: an object group's
#: children name the alerts, blocks and canvases the group carries. A lookup
#: by name that counted them found STOP_ALERT twice, the alert and the group's
#: pointer to it.
_REFERENCES = {"form_object_group_children"}


def _object_tables(cat: Catalog) -> List[Tuple[str, str, str]]:
    """(table, key column, name column) for every table that holds objects."""
    out = []
    for table in cat.tables():
        if not table.startswith("form_") or table in (LINKS, INHERITED) \
                or table in _REFERENCES:
            continue
        columns = cat.columns(table)
        pk = next((c for c in columns if c.endswith("_pk")
                   and c[:-3] == table[:-1].rstrip("s") or c == table[:-1] + "_pk"), None)
        pk = pk or next((c for c in columns if c.endswith("_pk")), None)
        name = next((c for c in columns if c.endswith("_name")
                     and pk and c[:-5] == pk[:-3]), None)
        if pk and name:
            out.append((table, pk, name))
    return out


def _locate(cat: Catalog, file_pk, path: str) -> List[Dict[str, Any]]:
    """Every object in one module that answers to a name.

    ``path`` is what a developer says: BLOCK, BLOCK.ITEM, or the name of a
    top-level object such as an alert. Several kinds can share a name --
    a block and a window often do -- so this returns all of them and the
    caller says which it meant.
    """
    parts = [p for p in (path or "").split(".") if p]
    if not parts:
        raise CatalogError("name an object")
    leaf, owner = parts[-1], (parts[-2] if len(parts) > 1 else None)
    found = []
    for table, pk, name in _object_tables(cat):
        columns = cat.columns(table)
        sql = ("SELECT * FROM %s WHERE form_file_fk = ? AND upper(%s) = upper(?)"
               % (table, name))
        params: List[Any] = [file_pk, leaf]
        if owner:
            # Every child table names what holds it in a form_<container>_name
            # column: an item its block, an LOV column its LOV, a radio button
            # its item, a tab page its canvas. BLOCK.ITEM means the container
            # is BLOCK, so a table whose container is called something else --
            # or that has none, like an alert -- does not answer. Only items
            # used to be checked, and an LOV column and a record group column
            # that happened to be called CUSTOMERID came back as ORDERS.CUSTOMERID.
            holders = [c for c in columns
                       if c.startswith("form_") and c.endswith("_name") and c != name]
            if not holders:
                continue
            sql += " AND (%s)" % " OR ".join("upper(%s) = upper(?)" % c for c in holders)
            params.extend([owner] * len(holders))
        for row in cat.rows(sql, params):
            found.append({"table": table, "pk": row[pk], "name": row[name],
                          "name_column": name,
                          # The table's own words, minus the family prefix:
                          # chopping a final "s" turned object_group_children
                          # into "object group childre".
                          "kind": table[len("form_"):].replace("_", " "),
                          "row": dict(row)})
    return found


def _from(cat: Catalog, link: Optional[Dict[str, Any]],
          taken: Dict[str, Any]) -> Dict[str, Any]:
    """One inherited property, with where it came from said by file.

    The loader records the module a value came from, by the name its pointer
    gives. When that is the object's own parent, its file is known; a value
    further up a chain -- a property class's own library -- keeps the name the
    pointer gave, since nothing here says which file holds it.
    """
    out = {"value": taken["value"], "from_object": taken["from_object"]}
    if link and taken.get("from_module") == link.get("parent_module"):
        out["from_file"] = _parent_file(cat, link)
    if not out.get("from_file"):
        out["from_module_name"] = taken.get("from_module")
    return out


def effective(cat: Catalog, module: str, obj: str,
              table: Optional[str] = None) -> Dict[str, Any]:
    """One object as the form runs it: what it sets, and what it inherits.

    The question subclassing makes necessary. An item whose font, colour and
    format mask come from a property class sets none of them itself, so its
    own row reads as an item with almost nothing on it -- which is not what
    the user sees.
    """
    found = cat.module(module)
    pk = found["form_file_pk"]
    objects = _locate(cat, pk, obj)
    if table:
        objects = [o for o in objects if o["table"] == table] or objects
    if not objects:
        raise CatalogError("%s has no object called %s"
                           % (found["file_name"], obj))
    if len(objects) > 1:
        return {"file": found["file_name"], "object": obj,
                "several": [{"kind": o["kind"], "table": o["table"]}
                            for o in objects],
                "note": "%d objects answer to that name; pass table= to pick one."
                        % len(objects)}

    one = objects[0]
    # The object's own name, and the name of whatever holds it, are identity
    # rather than properties: they are already in the answer's "object" and
    # the path that was asked for.
    identity = {one["name_column"], "form_block_name", "form_canvas_name"}
    own = {}
    for column, got in one["row"].items():
        if got is None or column in PLUMBING_NAMES or column.endswith(PLUMBING):
            continue
        if column in identity:
            continue
        own[column] = got
    taken = _inherited(cat, one["pk"])
    link = _links(cat, pk).get(one["pk"])

    merged = dict((k, v["value"]) for k, v in taken.items())
    merged.update(own)
    out = {
        "file": found["file_name"],
        "object": one["name"], "kind": one["kind"], "table": one["table"],
        "subclassed_from": _through_group(_source(cat, link), one["row"], link),
        "own": own,
        "inherited": {k: _from(cat, link, v) for k, v in taken.items()},
        "effective": merged,
        "counts": {"own": len(own), "inherited": len(taken)},
    }
    # An object can hold its own copy of what its parent sets: Forms writes a
    # subclassed trigger's code into the form as well. Read alone, that copy is
    # "set by the object", and nothing reads as inherited. Compared with the
    # parent, a value that matches is the parent's, unchanged; one that differs
    # is an override.
    parent = _parent_row(cat, link, one["table"])
    if parent is not None:
        same = sorted(k for k, v in own.items() if parent.get(k) is not None
                      and _text(parent.get(k)) == _text(v))
        differ = {k: _difference(v, parent[k]) for k, v in own.items()
                  if parent.get(k) is not None and _text(parent[k]) != _text(v)}
        if same:
            out["matches_parent"] = same
        if differ:
            out["overrides"] = differ
        out["counts"].update({"matches_parent": len(same), "overrides": len(differ)})
        if same and not taken and not differ:
            out["note"] = ("Everything %s sets is also its parent's, unchanged: Forms keeps a "
                           "copy in the module. It overrides nothing." % one["name"])
    if one["table"] == "form_object_groups":
        out["note"] = ("An object group has no properties of its own to inherit: what it brings "
                       "is its children, each of which is subclassed in its own right -- ask "
                       "effective() about one of them.")
    return out


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _difference(own: Any, parent: Any) -> Dict[str, Any]:
    """An override, said briefly: a long text -- a trigger's code -- by its
    start and its length, which is enough to see it differs; read_code gives
    the whole of it."""
    if len(_text(own)) <= 200 and len(_text(parent)) <= 200:
        return {"own": own, "parent": parent}
    def brief(v):
        t = _text(v)
        return {"starts": t[:160] + ("..." if len(t) > 160 else ""), "lines": len(t.splitlines())}
    return {"own": brief(own), "parent": brief(parent)}


def _parent_row(cat: Catalog, link: Optional[Dict[str, Any]], table: str) -> Optional[Dict[str, Any]]:
    """The parent object's own row, when it is the same kind of object -- a
    property class gives to any kind, and its columns are not the child's."""
    if not link or not link.get("parent_pk") or (link.get("resolved") or "N") != "Y":
        return None
    pk = next((k for t, k, _n in _object_tables(cat) if t == table), None)
    if not pk:
        return None
    rows = cat.rows("SELECT * FROM %s WHERE %s = ?" % (table, pk), (link["parent_pk"],))
    return dict(rows[0]) if rows else None


# ------------------------------------------------------------------ query

def query(cat: Catalog, sql: str, limit: int = 100) -> Dict[str, Any]:
    """A caller's own SELECT over the metadata tables.

    The escape hatch: the canned tools answer the usual questions, and this
    answers the rest without waiting for a new tool to be written.
    """
    limit = max(1, min(int(limit or 100), MAX_ROWS * 5))
    rows = cat.read_only_rows(sql)
    out = []
    for r in rows[:limit]:
        row = {}
        for k in r.keys():
            v = r[k]
            if isinstance(v, str) and len(v) > SNIPPET * 4:
                v = v[:SNIPPET * 4] + "…"
            row[k] = v
        out.append(row)
    return {"rows": out, "returned": len(out), "found": len(rows),
            "note": None if len(rows) <= limit else
                    "%d rows matched; %d returned." % (len(rows), limit)}


def schema(cat: Catalog, table: Optional[str] = None) -> Dict[str, Any]:
    """What there is to query: the tables, or one table's columns.

    Not one of the six, but query() is unusable without it.
    """
    if table:
        return {"table": table, "columns": cat.columns(table)}
    names = cat.tables()
    counts = {}
    for name in names:
        try:
            counts[name] = cat.rows("SELECT count(*) AS n FROM %s" % name)[0]["n"]
        except CatalogError:
            counts[name] = None
    return {"database": cat.path,
            "tables": [{"table": n, "rows": counts[n]} for n in names if counts[n]],
            "empty_tables": [n for n in names if not counts[n]]}
