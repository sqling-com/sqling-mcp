"""What the six tools answer, asked of a real application.

The assertions are deliberately about Northwind's own contents -- NW_ORDERS
exists, PB_SAVE has a WHEN-BUTTON-PRESSED, CHECK_PACKAGE_FAILURE is a
procedure -- because a tool that returns well-shaped nothing passes every
test written against its shape alone.
"""
import pytest

from sqling_mcp import tools
from sqling_mcp.db import Catalog, CatalogError


# ---------------------------------------------------------------- modules

def test_every_module_is_listed_by_its_file(cat):
    out = tools.list_modules(cat)
    files = [m["file"] for m in out["modules"]]
    assert "NW_ORDERS.fmb" in files and "NW_MAIN.fmb" in files
    assert files == sorted(files)
    assert out["count"] == len(files) == 7
    orders = next(m for m in out["modules"] if m["file"] == "NW_ORDERS.fmb")
    assert orders["blocks"] >= 2 and orders["triggers"] > 0
    assert orders["module_name"] == "NW_ORDERS"      # there, but not the key


def test_a_module_is_found_by_its_file(cat):
    for spelling in ("NW_ORDERS.fmb", "nw_orders.FMB", "NW_ORDERS", "nw_orders"):
        assert tools.describe_form(cat, spelling)["file"] == "NW_ORDERS.fmb"


def test_a_module_name_is_not_a_way_in(tmp_path, database):
    """BIDS.fmb holds BIDS20170612: the name inside a file is whatever its
    developer typed, so a lookup by it would find the wrong file, or two."""
    import shutil, sqlite3
    from sqling_mcp.db import Catalog
    copy = str(tmp_path / "renamed.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    conn.execute("UPDATE form_files SET module_name = 'ORDERS20170612'"
                 " WHERE file_name = 'NW_ORDERS.fmb'")
    conn.execute("UPDATE form_files SET module_name = 'NW_ORDERS'"
                 " WHERE file_name = 'NW_MAIN.fmb'")
    conn.commit(); conn.close()
    renamed = Catalog(copy)
    assert tools.describe_form(renamed, "NW_ORDERS")["file"] == "NW_ORDERS.fmb"
    with pytest.raises(CatalogError):
        tools.describe_form(renamed, "ORDERS20170612")


def test_two_folders_with_the_same_file_need_the_folder(tmp_path, database):
    import shutil, sqlite3
    from sqling_mcp.db import Catalog
    copy = str(tmp_path / "two.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    conn.execute("UPDATE form_files SET file_name = 'old/NW_MAIN.fmb' WHERE file_name = 'NW_MAIN.fmb'")
    conn.execute("UPDATE form_files SET file_name = 'new/NW_MAIN.fmb' WHERE file_name = 'NW_ORDERS.fmb'")
    conn.commit(); conn.close()
    two = Catalog(copy)
    with pytest.raises(CatalogError) as raised:
        tools.describe_form(two, "NW_MAIN")
    assert "old/NW_MAIN.fmb" in str(raised.value) and "new/NW_MAIN.fmb" in str(raised.value)
    assert tools.describe_form(two, "new/NW_MAIN.fmb")["file"] == "new/NW_MAIN.fmb"


def test_an_unknown_module_says_what_is_near(cat):
    with pytest.raises(CatalogError) as raised:
        tools.describe_form(cat, "NW_ORDER")
    assert "NW_ORDERS" in str(raised.value)      # the name it almost was


# --------------------------------------------------------------- describe

def test_describe_gives_blocks_triggers_units_and_tables(cat):
    out = tools.describe_form(cat, "NW_ORDERS")
    blocks = {b["block"] for b in out["blocks"]}
    assert "ORDERS" in blocks and "ORDER_DETAILS" in blocks
    assert out["counts"]["triggers"] == len(out["triggers"]) > 0
    assert any(t["table"] == "ORDERS" for t in out["tables"])
    assert any(u["kind"] == "Procedure" for u in out["program_units"])


def test_a_trigger_says_which_object_it_fires_on(cat):
    out = tools.describe_form(cat, "NW_CUSTOMERS")
    on = {(t["trigger"], t["on"]) for t in out["triggers"]}
    assert ("WHEN-BUTTON-PRESSED", "TOOLBAR.PB_SAVE") in on
    assert any(t["scope"] == "Block" for t in out["triggers"])


# ------------------------------------------------------------------- code

def test_a_program_unit_comes_back_whole(cat):
    out = tools.read_code(cat, "NW_CUSTOMERS", "CHECK_PACKAGE_FAILURE")
    assert out["kind"] == "Procedure"
    assert "PROCEDURE" in out["text"].upper()
    assert not out.get("truncated")


def test_one_trigger_of_many_is_picked_by_the_object_it_is_on(cat):
    several = tools.read_code(cat, "NW_CUSTOMERS", "WHEN-BUTTON-PRESSED")
    assert "several" in several and len(several["several"]) > 1

    one = tools.read_code(cat, "NW_CUSTOMERS", "WHEN-BUTTON-PRESSED",
                          on="TOOLBAR.PB_SAVE")
    assert one["on"] == "TOOLBAR.PB_SAVE"
    assert "COMMIT" in one["text"].upper()


def test_asking_for_code_that_is_not_there(cat):
    with pytest.raises(CatalogError) as raised:
        tools.read_code(cat, "NW_MAIN", "WHEN-NEVER-HAPPENS")
    assert "no trigger, program unit or menu item" in str(raised.value)


# ----------------------------------------------------------------- search

def test_search_finds_the_line_and_says_where_it_is(cat):
    out = tools.search(cat, "CHECK_PACKAGE_FAILURE")
    assert out["found"] > 1                      # called as well as declared
    hit = out["hits"][0]
    assert hit["line"] >= 1 and "CHECK_PACKAGE_FAILURE" in hit["text"].upper()
    assert hit["file"].startswith("NW_") and hit["file"].endswith(".fmb")


def test_search_is_case_insensitive_and_capped(cat):
    lower = tools.search(cat, "commit", limit=5)
    upper = tools.search(cat, "COMMIT", limit=5)
    assert lower["found"] == upper["found"] > 0
    assert lower["shown"] == 5 and lower["note"]


def test_search_wants_something_to_search_for(cat):
    with pytest.raises(CatalogError):
        tools.search(cat, "   ")


# -------------------------------------------------------------- where used

def test_where_used_names_the_modules_and_the_blocks(cat):
    out = tools.where_used(cat, "orders")
    assert out["table"] == "ORDERS"
    files = {m["file"] for m in out["modules"]}
    assert "NW_ORDERS.fmb" in files
    assert all(set(m["operations"]) <= set("CRUD") for m in out["modules"])
    assert any(b["block"] == "ORDERS" for b in out["blocks"])


@pytest.mark.parametrize("asked", ["Order Details", '"Order Details"', "order details"])
def test_where_used_finds_a_block_on_a_quoted_table(cat, asked):
    """Forms stores a data source as typed, quotes and all: NW_ORDERS's
    ORDER_DETAILS block is on "Order Details". Reported from Claude Desktop."""
    out = tools.where_used(cat, asked)
    assert [(b["file"], b["block"]) for b in out["blocks"]] == [("NW_ORDERS.fmb", "ORDER_DETAILS")]
    assert [m["file"] for m in out["modules"]] == ["NW_ORDERS.fmb"]
    assert out["table"] == '"Order Details"'           # as the application spells it


@pytest.mark.parametrize("asked,shown", [("orders", "ORDERS"), ("ORDERS", "ORDERS"),
                                         ("order details", '"Order Details"')])
def test_where_used_names_the_table_as_the_application_does(cat, asked, shown):
    out = tools.where_used(cat, asked)
    assert out["found"] and out["table"] == shown
    assert "spellings" not in out and "note" not in out


def test_where_used_says_no_such_table_and_suggests(cat):
    out = tools.where_used(cat, "Order Detail")
    assert not out["found"] and out["modules"] == [] and out["blocks"] == []
    assert out["did_you_mean"] == ['"Order Details"']
    # The text still turns up in code, and the note says that is all it is.
    assert out["counts"]["code_lines"] > 0 and "a text match, not a table" in out["note"]


def test_where_used_flags_names_that_differ_only_in_case(database, tmp_path):
    """Oracle keeps a quoted name's case, so "Orders" and ORDERS can be two
    tables. Matching ignores case; the answer says when that merged two."""
    import shutil, sqlite3
    from sqling_mcp.db import Catalog
    copy = str(tmp_path / "variants.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    pk = conn.execute("SELECT max(form_block_pk) FROM form_blocks").fetchone()[0] + 1000
    file_pk = conn.execute("SELECT min(form_file_pk) FROM form_files").fetchone()[0]
    conn.execute("INSERT INTO form_blocks (form_block_pk, form_file_fk, form_block_name,"
                 " query_data_source_name) VALUES (?, ?, 'MIXED', '\"Orders\"')", (pk, file_pk))
    conn.commit(); conn.close()
    out = tools.where_used(Catalog(copy), "orders")
    assert set(out["spellings"]) == {"ORDERS", '"Orders"'}
    assert "different tables" in out["note"]


def test_oracle_notation():
    from sqling_mcp.tools import _oracle_name
    assert _oracle_name("Order Details") == '"Order Details"'
    assert _oracle_name("orders") == "ORDERS"
    assert _oracle_name("HR.Order Details") == 'HR."Order Details"'
    assert _oracle_name('"HR"."Orders"') == '"HR"."Orders"'


def test_where_used_matches_a_schema_qualified_source():
    from sqling_mcp.tools import _names_table, _table_key
    assert _names_table('"HR"."Order Details"', _table_key("Order Details"))
    assert _names_table("SCOTT.EMP", "EMP")
    assert not _names_table("EMPLOYEES", "EMP")


def test_list_modules_names_the_original_files(cat):
    """The file each module came from, as its owner knows it -- NW_ORDERS.fmb,
    not the NW_ORDERS.xml it went through on the way into the database."""
    files = {m["module_name"]: m["file"] for m in tools.list_modules(cat)["modules"]}
    assert files["NW_ORDERS"] == "NW_ORDERS.fmb"
    assert all(f.endswith(".fmb") for f in files.values()), files


# ------------------------------------------------------------------ query

def test_a_select_runs_and_a_write_does_not(cat):
    out = tools.query(cat, "SELECT module_name FROM form_files ORDER BY 1")
    assert out["returned"] == out["found"] == 7

    for refused in ("DELETE FROM form_files",
                    "UPDATE form_files SET module_name = 'X'",
                    "ATTACH DATABASE 'other.sqlite' AS other",
                    "PRAGMA table_info(form_files)",
                    "SELECT 1; DROP TABLE form_files"):
        with pytest.raises(CatalogError):
            tools.query(cat, refused)


def test_the_database_itself_is_opened_read_only(cat):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        cat.conn.execute("CREATE TABLE scribble (x)")


def test_query_caps_what_it_returns(cat):
    out = tools.query(cat, "SELECT * FROM form_items", limit=5)
    assert out["returned"] == 5 and out["found"] > 5 and out["note"]


def test_schema_lists_tables_and_columns(cat):
    whole = tools.schema(cat)
    names = {t["table"] for t in whole["tables"]}
    assert {"form_files", "form_blocks", "form_triggers"} <= names
    assert all(t["rows"] for t in whole["tables"])
    one = tools.schema(cat, "form_triggers")
    assert "trigger_text" in one["columns"]


# ------------------------------------------------------------- the opening

def test_a_database_that_is_not_one_is_refused(tmp_path):
    empty = tmp_path / "plain.sqlite"
    import sqlite3
    sqlite3.connect(str(empty)).execute("CREATE TABLE t (x)")
    with pytest.raises(CatalogError) as raised:
        Catalog(str(empty))
    assert "not a Forms or Reports metadata database" in str(raised.value)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(CatalogError):
        Catalog(str(tmp_path / "nope.sqlite"))


# -------------------------------------------------------------- effective

def test_effective_block_item_is_the_item_only(cat):
    """ORDERS.CUSTOMERID is an item. An LOV column and a record group column
    that are also called CUSTOMERID live in an LOV and a record group, not in
    block ORDERS, so they do not answer to that path. Reported from Claude
    Desktop, where it came back as three objects."""
    out = tools.effective(cat, "NW_ORDERS", "ORDERS.CUSTOMERID")
    assert "several" not in out, out.get("note")
    assert out["table"] == "form_items" and out["object"].upper() == "CUSTOMERID"


def test_effective_bare_name_still_lists_every_kind(cat):
    out = tools.effective(cat, "NW_ORDERS", "CUSTOMERID")
    kinds = {o["table"] for o in out["several"]}
    assert {"form_items", "form_lov_columns", "form_record_group_columns"} <= kinds


def test_effective_container_of_another_kind_is_honoured(cat):
    """The container part matches whatever holds the object: an LOV column's
    LOV, a record group column's group -- not only an item's block."""
    lov = cat.rows("SELECT form_lov_name, form_lov_column_name FROM form_lov_columns c"
                   " JOIN form_files f ON f.form_file_pk = c.form_file_fk"
                   " WHERE f.module_name = 'NW_ORDERS' AND upper(form_lov_column_name) = 'CUSTOMERID'")[0]
    out = tools.effective(cat, "NW_ORDERS", "%s.CUSTOMERID" % lov["form_lov_name"])
    assert out["table"] == "form_lov_columns"


# -------------------------------------------------- found against a real app

def _copy(database, tmp_path, name):
    import shutil
    copy = str(tmp_path / name)
    shutil.copy(database, copy)
    return copy


def test_list_modules_counts_across_many_files(database, tmp_path):
    """The counts are taken in one GROUP BY per table now, not a subquery per
    file -- which read the table once per file and took 8 seconds on a real
    557-file application (0.15 s now). This holds the counts right across a
    thousand files; it is too small to show the old cost."""
    import sqlite3, time
    from sqling_mcp.db import Catalog
    copy = _copy(database, tmp_path, "many.sqlite")
    conn = sqlite3.connect(copy)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(form_items)")]
    items = conn.execute("SELECT * FROM form_items").fetchall()
    top = conn.execute("SELECT max(form_file_pk) FROM form_files").fetchone()[0]
    for k in range(1, 1000):
        conn.execute("INSERT INTO form_files (form_file_pk, file_name, module_name, module_kind)"
                     " VALUES (?, ?, 'X', 'form')", (top + k, "copy/%d.fmb" % k))
    fk = cols.index("form_file_fk"); pk = cols.index("form_item_pk")
    rows = []
    for k in range(1, 40):
        for r in items:
            r = list(r); r[pk] += 10 ** 6 * k; r[fk] = top + k; rows.append(r)
    conn.executemany("INSERT INTO form_items (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))), rows)
    conn.commit(); conn.close()
    cat = Catalog(copy)
    started = time.time()
    out = tools.list_modules(cat)
    assert time.time() - started < 1.0
    assert out["count"] == 7 + 999
    assert {m["file"]: m["items"] for m in out["modules"]}["copy/1.fmb"] == len(items)


def test_where_used_keeps_schemas_apart(database, tmp_path):
    """Reported against a real application: VENDORS_V came back as one schema's
    table though the forms used two, and the reports a third's."""
    import sqlite3
    from sqling_mcp.db import Catalog
    copy = _copy(database, tmp_path, "schemas.sqlite")
    conn = sqlite3.connect(copy)
    files = [r[0] for r in conn.execute("SELECT form_file_pk FROM form_files ORDER BY 1")]
    top = conn.execute("SELECT max(form_block_pk) FROM form_blocks").fetchone()[0]
    for n, (fk, src) in enumerate([(files[0], "sales.vendors_v"), (files[1], "SALES.VENDORS_V"),
                                    (files[2], "buy.vendors_v")], 1):
        conn.execute("INSERT INTO form_blocks (form_block_pk, form_file_fk, form_block_name,"
                     " query_data_source_name) VALUES (?, ?, 'VENDORS', ?)", (top + n, fk, src))
        conn.execute("INSERT INTO form_table_usage (form_file_fk, table_name, reads)"
                     " VALUES (?, ?, 'Y')", (fk, src.upper()))
    conn.commit(); conn.close()
    out = tools.where_used(Catalog(copy), "vendors_v")
    assert out["table"] == "VENDORS_V"
    assert out["spellings"] == ["BUY.VENDORS_V", "SALES.VENDORS_V"]
    assert "differ in schema" in out["note"]
    by_table = {}
    for m in out["modules"]:
        by_table.setdefault(m["table"], set()).add(m["file"])
    assert len(by_table["SALES.VENDORS_V"]) == 2 and len(by_table["BUY.VENDORS_V"]) == 1
    # Asked for one schema's, only that one answers.
    one = tools.where_used(Catalog(copy), "buy.vendors_v")
    assert {m["table"] for m in one["modules"]} == {"BUY.VENDORS_V"} and "spellings" not in one


def test_effective_does_not_count_an_object_groups_pointer(database, tmp_path):
    """Reported against a real application: an alert in an object group came
    back as two objects, the alert and the group's child that points at it."""
    import sqlite3
    from sqling_mcp.db import Catalog
    copy = _copy(database, tmp_path, "groups.sqlite")
    conn = sqlite3.connect(copy)
    fk = conn.execute("SELECT form_file_pk FROM form_files WHERE file_name = 'NW_ORDERS.fmb'").fetchone()[0]
    top = conn.execute("SELECT max(form_block_pk) FROM form_blocks").fetchone()[0] + 500
    conn.execute("INSERT INTO form_alerts (form_alert_pk, form_file_fk, form_alert_name, title)"
                 " VALUES (?, ?, 'STOP_ALERT', 'Stop')", (top, fk))
    conn.execute("INSERT INTO form_object_group_children (form_object_group_child_pk, form_file_fk,"
                 " form_object_group_child_name, form_object_group_name) VALUES (?, ?, 'STOP_ALERT', 'GRP')",
                 (top + 1, fk))
    conn.commit(); conn.close()
    out = tools.effective(Catalog(copy), "NW_ORDERS.fmb", "STOP_ALERT")
    assert "several" not in out, out.get("note")
    assert out["table"] == "form_alerts"
