"""What calls what: calls, called_by, reachable, never_called.

Northwind's NW_MAIN opens its six forms. The launcher form (conftest) runs
reports every way a form can: RUN_REPORT_OBJECT through a report object,
rp2rro_run_product, and the calls that cannot be followed -- a target in a
variable, a form not loaded, a report object with no file or none at all.
"""
import shutil
import sqlite3

import pytest

from sqling_mcp import navigation as nav
from sqling_mcp import tools
from sqling_mcp.db import Catalog, CatalogError


def test_nw_main_leads_to_its_six_forms(cat):
    out = nav.calls(cat, "NW_MAIN")
    assert out["leads_to"] == ["NW_CUSTOMERS.fmb", "NW_EMPLOYEES.fmb", "NW_LOOKUPS.fmb",
                               "NW_ORDERS.fmb", "NW_PRODUCTS.fmb", "NW_SUPPLIERS.fmb"]
    one = next(c for c in out["calls"] if c["to"] == "NW_ORDERS.fmb")
    assert one["call"] == "CALL_FORM" and one["in"].startswith("trigger MENU.PB_ORDERS")
    assert "note" not in out                     # the built-in DEFAULT&SMARTBAR menu is no call


def test_called_by(cat):
    out = nav.called_by(cat, "NW_ORDERS.fmb")
    assert out["called_by"] == ["NW_MAIN.fmb"] and out["counts"]["calls"] == 1
    assert nav.called_by(cat, "NW_MAIN")["called_by"] == []


def test_reachable_from_the_first_form(cat):
    out = nav.reachable(cat, "NW_MAIN")
    assert [(r["file"], r["steps"]) for r in out["reached"]][0] == ("NW_MAIN.fmb", 0)
    assert out["counts"] == {"reached": 7, "not_reached": 0, "calls_not_followed": 0}
    orders = next(r for r in out["reached"] if r["file"] == "NW_ORDERS.fmb")
    assert orders["path"] == ["NW_MAIN.fmb", "NW_ORDERS.fmb"]
    assert nav.reachable(cat, "NW_MAIN", max_depth=0)["counts"]["reached"] == 1


def test_never_called_is_the_starting_form(cat):
    assert nav.never_called(cat)["files"] == [{"file": "NW_MAIN.fmb", "kind": "form"}]


def test_report_runs_are_followed_to_their_files(rcat):
    out = nav.calls(rcat, "launcher")
    assert out["leads_to"] == ["monthly_sales.rdf", "NW_PRODUCT_CATALOG.rdf"]
    via_object = next(c for c in out["calls"] if c.get("report_object") == "SALES")
    assert via_object["to"] == "monthly_sales.rdf" and via_object["call"] == "RUN_REPORT_OBJECT"
    rp2rro = next(c for c in out["calls"] if c["call"] == "RP2RRO.RP2RRO_RUN_PRODUCT")
    assert rp2rro["to"] == "NW_PRODUCT_CATALOG.rdf"


def test_calls_that_cannot_be_followed_say_why(rcat):
    why = {(c["call"], c.get("target")): c["why"]
           for c in nav.calls(rcat, "launcher")["calls"] if "to" not in c}
    assert "worked out at run time" in why[("CALL_FORM", None)]
    assert why[("CALL_FORM", "NW_ORDERS")] == "no loaded form is called NW_ORDERS"
    assert "names no file" in why[("RUN_REPORT_OBJECT", "PREVIEW")]
    assert "no report object called NOPE" in why[("RUN_REPORT_OBJECT", "NOPE")]


def test_a_report_says_which_forms_run_it_and_the_dynamic_calls(rcat):
    out = nav.called_by(rcat, "NW_PRODUCT_CATALOG")
    assert out["called_by"] == ["launcher.fmb"]
    assert out["dynamic_calls"] == 1 and "may lead here" in out["note"]


def test_never_called_across_forms_and_reports(rcat):
    files = {f["file"] for f in nav.never_called(rcat)["files"]}
    assert "launcher.fmb" in files and "monthly_sales.rdf" not in files
    assert {f["kind"] for f in nav.never_called(rcat, kind="report")["files"]} == {"report"}


def test_describe_form_and_report_carry_a_summary(rcat):
    assert tools.describe_report(rcat, "monthly_sales")["navigation"]["called_by"] == ["launcher.fmb"]
    summary = tools.describe_form(rcat, "launcher")["navigation"]
    assert summary["leads_to"] == ["monthly_sales.rdf", "NW_PRODUCT_CATALOG.rdf"]
    assert summary["calls_not_followed"] == 4
    assert summary["names"] == ["NW_CUSTOMERS_BY_COUNTRY.rdf"]


def test_a_form_leads_to_its_menu_module(database, tmp_path):
    copy = str(tmp_path / "menu.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    top = conn.execute("SELECT max(form_file_pk) FROM form_files").fetchone()[0] + 1
    conn.execute("INSERT INTO form_files (form_file_pk, file_name, module_name, module_kind)"
                 " VALUES (?, 'menus/app_menu.mmb', 'APP_MENU', 'menu')", (top,))
    conn.execute("UPDATE form_files SET menu_module = 'APP_MENU' WHERE file_name = 'NW_MAIN.fmb'")
    conn.commit(); conn.close()
    c = Catalog(copy)
    assert "menus/app_menu.mmb" in nav.calls(c, "NW_MAIN")["leads_to"]
    assert nav.called_by(c, "app_menu")["called_by"] == ["NW_MAIN.fmb"]


def test_two_files_of_one_name_are_said_not_guessed(database, tmp_path):
    copy = str(tmp_path / "two.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    conn.execute("UPDATE form_files SET file_name = 'old/NW_ORDERS.fmb' WHERE file_name = 'NW_ORDERS.fmb'")
    top = conn.execute("SELECT max(form_file_pk) FROM form_files").fetchone()[0] + 1
    conn.execute("INSERT INTO form_files (form_file_pk, file_name, module_name, module_kind)"
                 " VALUES (?, 'new/NW_ORDERS.fmb', 'NW_ORDERS', 'form')", (top,))
    conn.commit(); conn.close()
    call = next(c for c in nav.calls(Catalog(copy), "NW_MAIN")["calls"] if c["target"] == "NW_ORDERS")
    assert "to" not in call and call["candidates"] == ["new/NW_ORDERS.fmb", "old/NW_ORDERS.fmb"]


def test_a_database_built_before_calls_were_recorded(database, tmp_path):
    copy = str(tmp_path / "old.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    conn.execute("DROP TABLE form_code_calls"); conn.execute("DROP TABLE form_code_tables")
    conn.commit(); conn.close()
    old = Catalog(copy)
    with pytest.raises(CatalogError) as raised:
        nav.calls(old, "NW_MAIN")
    assert "built before calls were recorded" in str(raised.value)
    assert "navigation" not in tools.describe_form(old, "NW_MAIN")
    assert "blocks' data sources" in tools.where_used(old, "ORDERS")["code_uses_note"]


def test_where_used_counts_a_table_used_only_in_code(rcat):
    out = tools.where_used(rcat, "audit_log")
    assert out["modules"] == [{"file": "launcher.fmb", "kind": "form", "table": "AUDIT_LOG",
                               "operations": "C"}]
    assert out["code_uses"][0]["in"] == "trigger" and out["code_uses"][0]["verb"] == "insert"


# ------------------------------------------- names passed to other procedures

def test_a_report_named_through_the_applications_own_wrapper(rcat):
    """Reported against a real application: most of its reports are run through
    its own procedure (PKG.BUILD_REPORT_URL('NAME', 'PDF')) and read as never
    called. The name is said as a name, not a call."""
    out = nav.calls(rcat, "launcher")
    assert out["names"] == ["NW_CUSTOMERS_BY_COUNTRY.rdf"]
    assert out["named_in"][0]["call"] == "APP_PKG.BUILD_REPORT_URL"
    assert "NW_CUSTOMERS_BY_COUNTRY.rdf" not in out["leads_to"]
    by = nav.called_by(rcat, "NW_CUSTOMERS_BY_COUNTRY")
    assert by["called_by"] == [] and by["named_by"] == ["launcher.fmb"]
    assert "pass its name to a procedure" in by["note"]


def test_never_called_lists_named_only_files_apart(rcat):
    out = nav.never_called(rcat, kind="report")
    assert "NW_CUSTOMERS_BY_COUNTRY.rdf" not in [f["file"] for f in out["files"]]
    assert [f["file"] for f in out["named_only"]] == ["NW_CUSTOMERS_BY_COUNTRY.rdf"]
    assert out["named_only"][0]["named_in"] == ["APP_PKG.BUILD_REPORT_URL"]
    assert "named_only" in out["note"]


def test_reachable_follows_names_only_when_asked(rcat):
    plain = nav.reachable(rcat, "launcher")
    assert "NW_CUSTOMERS_BY_COUNTRY.rdf" in plain["not_reached"] and plain["names_not_followed"] == 1
    named = nav.reachable(rcat, "launcher", follow_named=True)
    assert "NW_CUSTOMERS_BY_COUNTRY.rdf" in [r["file"] for r in named["reached"]]


def test_never_called_kinds(cat, database, tmp_path):
    with pytest.raises(CatalogError) as raised:
        nav.never_called(cat, kind="object library")
    assert "subclassed from, not called" in str(raised.value)
    copy = str(tmp_path / "libs.sqlite")
    shutil.copy(database, copy)
    conn = sqlite3.connect(copy)
    top = conn.execute("SELECT max(form_file_pk) FROM form_files").fetchone()[0]
    conn.execute("INSERT INTO form_files (form_file_pk, file_name, module_name, module_kind)"
                 " VALUES (?, 'libs/used.pll', 'USED', 'library')", (top + 1,))
    conn.execute("INSERT INTO form_files (form_file_pk, file_name, module_name, module_kind)"
                 " VALUES (?, 'libs/spare.pll', 'SPARE', 'library')", (top + 2,))
    fk = conn.execute("SELECT form_file_pk FROM form_files WHERE file_name = 'NW_MAIN.fmb'").fetchone()[0]
    conn.execute("INSERT INTO form_attached_libraries (form_attached_library_pk, form_file_fk,"
                 " form_attached_library_name, library_location) VALUES (?, ?, 'USED', 'used.pll')",
                 (top + 3, fk))
    conn.commit(); conn.close()
    out = nav.never_called(Catalog(copy), kind="library")
    assert out["files"] == [{"file": "libs/spare.pll", "kind": "library"}]
    assert "attached to forms, not called" in out["note"]


def test_where_used_explains_dual_under_a_schema(rcat):
    out = tools.where_used(rcat, "SYS.DUAL")
    assert not out["found"] and out["modules"] == [] and "spellings" not in out
    assert "one-row dummy table" in out["note"]


def test_a_form_and_a_report_of_one_base_name_are_both_kept(database, report_databases, tmp_path):
    """NW_ORDERS.fmb beside NW_ORDERS.rdf: each call goes to its own kind,
    a name passed to a procedure keeps both (it could mean either), and a lookup
    by the bare name asks which."""
    forms, reports = str(tmp_path / "f.sqlite"), str(tmp_path / "r.sqlite")
    shutil.copy(database, forms)
    shutil.copy(report_databases[1], reports)
    conn = sqlite3.connect(reports)
    conn.execute("UPDATE report_files SET file_name = 'NW_ORDERS.rdf'"
                 " WHERE file_name = 'NW_ORDERS_BY_CUSTOMER.rdf'")
    conn.commit(); conn.close()
    conn = sqlite3.connect(forms)
    fk = conn.execute("SELECT form_file_pk FROM form_files WHERE file_name = 'NW_MAIN.fmb'").fetchone()[0]
    for line, call, kind in ((1, "RP2RRO.RP2RRO_RUN_PRODUCT", "report"), (2, "APP_PKG.BUILD_URL", "mention"),
                             (3, "APP_PKG.BUILD_REPORT_URL", "mention")):
        conn.execute("INSERT INTO form_code_calls (form_file_fk, owner_type, owner_name, line_no,"
                     " call_name, target_kind, target, written) VALUES (?, 'ProgramUnit', 'PRINT_IT',"
                     " ?, ?, ?, 'NW_ORDERS', '''NW_ORDERS''')", (fk, line, call, kind))
    conn.commit(); conn.close()
    c = Catalog(forms, reports=reports)
    with pytest.raises(CatalogError) as raised:
        c.find("NW_ORDERS")
    assert "NW_ORDERS.fmb, NW_ORDERS.rdf" in str(raised.value)
    out = nav.calls(c, "NW_MAIN")
    assert {e["to"] for e in out["calls"] if e["call"] == "CALL_FORM" and e["target"] == "NW_ORDERS"} == {"NW_ORDERS.fmb"}
    assert [e["to"] for e in out["calls"] if e["call"].startswith("RP2RRO")] == ["NW_ORDERS.rdf"]
    assert out["names"] == ["NW_ORDERS.fmb", "NW_ORDERS.rdf"]
    # A procedure whose name says nothing keeps both, each saying what else it matches.
    vague = [e for e in out["named_in"] if e["call"] == "APP_PKG.BUILD_URL"]
    assert {e["to"]: e["also_matches"] for e in vague} == {
        "NW_ORDERS.fmb": ["NW_ORDERS.rdf"], "NW_ORDERS.rdf": ["NW_ORDERS.fmb"]}
    # One whose name says it runs reports names the report alone, and says why.
    sure = [e for e in out["named_in"] if e["call"] == "APP_PKG.BUILD_REPORT_URL"]
    assert [e["to"] for e in sure] == ["NW_ORDERS.rdf"] and "runs reports" in sure[0]["chosen_by"]


def test_what_a_procedures_name_says_it_runs():
    assert nav._kind_named_by("APP.URL_PKG.BUILD_REPORT_URL") == "report"
    assert nav._kind_named_by("FIND_CALL_FORM") == "form"
    assert nav._kind_named_by("APP_PKG.BUILD_URL") is None
    assert nav._kind_named_by("FORM_REPORT_LINK") is None          # says both: no choice
