"""Reports: opened beside the forms, described, and their code read.

The fixture (conftest.REPORT and LAUNCHER) is Northwind's three reports, one
report written to have every kind of code, and a form that runs reports.
"""
import os
import shutil
import sqlite3

import pytest

from sqling_mcp import tools
from sqling_mcp.db import Catalog, CatalogError


# ------------------------------------------------------------------ opening

def test_forms_and_reports_open_together(rcat):
    assert rcat.families == {"form", "report"}
    assert "report_files" in rcat.tables() and "form_files" in rcat.tables()
    # One query reaches both, unqualified.
    got = tools.query(rcat, "SELECT count(*) AS n FROM report_files, form_files")
    assert got["rows"][0]["n"] == 4


def test_a_reports_database_opens_on_its_own(report_databases):
    alone = Catalog(None, reports=report_databases[1])
    assert alone.families == {"report"}
    assert {m["kind"] for m in tools.list_modules(alone)["modules"]} == {"report"}
    with pytest.raises(CatalogError) as raised:
        tools.describe_form(alone, "NW_ORDERS")
    assert "no file called" in str(raised.value)


def test_a_reports_database_given_as_the_only_file_is_read_as_one(report_databases):
    assert Catalog(report_databases[1]).families == {"report"}


def test_reports_that_are_not_reports_are_refused(report_databases, database):
    with pytest.raises(CatalogError) as raised:
        Catalog(report_databases[0], reports=database)        # a forms database
    assert "not a Reports metadata database" in str(raised.value)


def test_a_forms_file_that_already_holds_reports_is_not_given_more(tmp_path, report_databases):
    both = str(tmp_path / "both.sqlite")
    shutil.copy(report_databases[1], both)
    with pytest.raises(CatalogError) as raised:
        Catalog(both, reports=report_databases[1])
    assert "would be hidden" in str(raised.value)


# ------------------------------------------------------------- list, find

def test_list_modules_has_the_reports_by_file(rcat):
    mods = {m["file"]: m for m in tools.list_modules(rcat)["modules"]}
    assert set(mods) == {"launcher.fmb", "monthly_sales.rdf", "NW_CUSTOMERS_BY_COUNTRY.rdf",
                         "NW_ORDERS_BY_CUSTOMER.rdf", "NW_PRODUCT_CATALOG.rdf"}
    sales = mods["monthly_sales.rdf"]
    assert sales["kind"] == "report" and sales["module_name"] == "SALES_SUMMARY7"
    assert (sales["queries"], sales["formulas"], sales["placeholders"],
            sales["program_units"], sales["parameters"]) == (2, 1, 1, 7, 1)


def test_a_report_is_found_by_its_file_not_its_module_name(rcat):
    assert tools.describe_report(rcat, "monthly_sales")["file"] == "monthly_sales.rdf"
    with pytest.raises(CatalogError):
        tools.describe_report(rcat, "SALES_SUMMARY7")


# ---------------------------------------------------------- describe_report

def test_describe_report(rcat):
    out = tools.describe_report(rcat, "monthly_sales.rdf")
    assert out["parameters"] == [{"parameter": "P_REGION", "datatype": "character",
                                  "initial_value": "EAST", "label": "Region",
                                  "validation_trigger": "p_regionvalidtrigger"}]
    q = next(q for q in out["queries"] if q["query"] == "Q_SALES")
    assert q["query"] == "Q_SALES" and q["groups"] == ["G_REGION"]
    assert "JOIN REGIONS" in q["text"]
    assert out["groups"] == [{"group": "G_REGION", "query": "Q_SALES"},
                             {"group": "G_LINES", "query": "Q_LINES"}]
    assert out["formulas"] == [{"formula": "CF_TAX", "datatype": "number", "unit": "cf_taxformula"}]
    assert out["placeholders"][0]["unit"] == "cp_titleformula"
    assert {(t["trigger"], t["unit"]) for t in out["report_triggers"]} == {
        ("before report trigger", "beforereport"), ("after parameter form trigger", "afterpform")}


def test_every_program_unit_says_what_it_is_for(rcat):
    units = {u["unit"]: u["used_as"] for u in
             tools.describe_report(rcat, "monthly_sales")["program_units"]}
    assert units["beforereport"] == ["before report trigger"]
    assert units["cf_taxformula"] == ["formula of formula CF_TAX"]
    assert units["cp_titleformula"] == ["formula of placeholder CP_TITLE"]
    assert units["f_totalformattrigger"] == ["format trigger of field F_TOTAL"]
    assert units["p_regionvalidtrigger"] == ["validation trigger of parameter P_REGION"]
    assert units["unused_helper"] == []


def test_a_unit_nothing_points_at_is_said(rcat):
    assert "unused_helper" in tools.describe_report(rcat, "monthly_sales")["note"]


def test_describe_form_on_a_report_describes_the_report(rcat):
    assert tools.describe_form(rcat, "monthly_sales.rdf")["kind"] == "report"


# -------------------------------------------------------- forms run reports

def test_a_form_says_which_reports_it_runs(rcat):
    runs = {r["report_object"]: r for r in tools.describe_form(rcat, "launcher")["runs_reports"]}
    assert runs["SALES"]["report_file"] == "monthly_sales.rdf"           # folder and .rdf off
    assert runs["CATALOG"]["report_file"] == "NW_PRODUCT_CATALOG.rdf"    # no extension given
    assert runs["GONE"]["report_file"] is None
    assert "missing_report.rdf" in tools.describe_form(rcat, "launcher")["runs_reports_note"]


def test_a_report_says_which_forms_run_it(rcat):
    assert tools.describe_report(rcat, "monthly_sales")["run_by"] == [
        {"form": "launcher.fmb", "report_object": "SALES"}]
    assert tools.describe_report(rcat, "NW_ORDERS_BY_CUSTOMER")["run_by"] == []


def test_without_reports_loaded_a_form_says_so(report_databases):
    forms_only = Catalog(report_databases[0])
    out = tools.describe_form(forms_only, "launcher")
    assert all(r["report_file"] is None for r in out["runs_reports"])
    assert "--reports" in out["runs_reports_note"]


def test_report_key():
    from sqling_mcp.tools import _report_key
    assert _report_key("/apps/reports/Monthly.rdf") == "MONTHLY"
    assert _report_key(r"E:\APP\REPORTS\MONTHLY.REP") == "MONTHLY"
    assert _report_key("reports/MONTHLY.rdf.xml") == "MONTHLY"
    assert _report_key("MONTHLY") == "MONTHLY"


# -------------------------------------------------------------------- code

def test_read_code_gives_a_report_unit_as_builder_shows_it(rcat):
    out = tools.read_code(rcat, "monthly_sales", "cf_taxformula")
    assert out["kind"] == "program unit" and out["used_as"] == ["formula of formula CF_TAX"]
    assert out["text"].startswith("function CF_TAXFormula")    # no leading blank line or indent


def test_read_code_gives_a_query(rcat):
    out = tools.read_code(rcat, "monthly_sales", "Q_SALES")
    assert out["kind"] == "query" and "FROM ORDERS O" in out["text"]


def test_search_reaches_report_code_and_sql(rcat):
    hits = tools.search(rcat, "SRW.MESSAGE")["hits"]
    assert [(h["file"], h["name"], h["line"]) for h in hits] == [("monthly_sales.rdf", "beforereport", 3)]
    sql = tools.search(rcat, "REGIONS")["hits"]
    assert sql and sql[0]["kind"] == "query" and sql[0]["name"] == "Q_SALES"
    # A form's code is still found beside it.
    assert any(h["file"] == "launcher.fmb" for h in tools.search(rcat, "RUN_REPORT_OBJECT")["hits"])


def test_list_code_lists_report_code(rcat):
    reps = tools.list_code(rcat, module_kind="report")
    assert reps["counts"]["program unit"] == 7 and reps["counts"]["query"] >= 5
    one = tools.list_code(rcat, file="monthly_sales.rdf", kind="query")
    assert [c["name"] for c in one["code"]] == ["Q_SALES", "Q_LINES"]


# --------------------------------------------------------------- where_used

def test_where_used_on_reports_alone_answers(report_databases):
    """Reported from Claude Desktop: a reports-only database has no
    form_table_usage, and where_used failed on it."""
    alone = Catalog(None, reports=report_databases[1])
    out = tools.where_used(alone, "ORDERS")
    assert out["found"] and out["blocks"] == []
    assert {(m["file"], m["kind"], m["operations"]) for m in out["modules"]} == {
        ("monthly_sales.rdf", "report", "R"), ("NW_ORDERS_BY_CUSTOMER.rdf", "report", "R")}


def test_where_used_counts_report_queries(rcat):
    """Reported from Claude Desktop: report queries did not count as usage."""
    out = tools.where_used(rcat, "REGIONS")
    assert out["table"] == "REGIONS"
    assert out["modules"] == [{"file": "monthly_sales.rdf", "kind": "report", "table": "REGIONS",
                               "operations": "R"}]
    assert out["code_uses"] == [{"file": "monthly_sales.rdf", "kind": "report", "in": "query",
                                 "name": "Q_SALES", "table": "REGIONS", "verb": "query"}]


def test_where_used_finds_a_quoted_table_in_report_sql(rcat):
    out = tools.where_used(rcat, "order details")
    assert out["table"] == '"Order Details"'
    assert [m["file"] for m in out["modules"]] == ["NW_ORDERS_BY_CUSTOMER.rdf"]


def test_where_used_with_no_reports_says_no_such_table_as_before(rcat):
    out = tools.where_used(rcat, "NOSUCH_TABLE")
    assert not out["found"] and out["modules"] == [] and out["code_uses"] == []


def test_describe_report_lists_its_tables(rcat):
    tables = {t["table"]: t for t in tools.describe_report(rcat, "monthly_sales")["tables"]}
    assert set(tables) == {"ORDERS", "REGIONS", "ORDER_LINES"}
    assert tables["REGIONS"] == {"table": "REGIONS", "operations": "R", "in": ["query Q_SALES"]}


def test_server_on_a_reports_database_alone(report_databases):
    import anyio, sys
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "sqling_mcp.server", report_databases[1]],
        env=dict(os.environ, PYTHONPATH=os.path.join(root, "src")))

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                answers = {}
                for tool, args in (("list_modules", {}), ("where_used", {"table": "ORDERS"}),
                                   ("search", {"text": "SRW"}), ("schema", {}),
                                   ("describe_form", {"file": "monthly_sales"})):
                    got = await client.call_tool(tool, args)
                    answers[tool] = got
                return answers
    answers = anyio.run(go)
    for tool, got in answers.items():
        assert not getattr(got, "is_error", False), (tool, got)
        body = got.structured_content or {}
        body = body.get("result", body)
        assert "error" not in body, (tool, body)


def test_describe_report_has_summaries_and_links(rcat):
    """Both were loaded and left out of the answer (reported from Claude Desktop)."""
    out = tools.describe_report(rcat, "monthly_sales")
    assert out["summaries"] == [{"summary": "CS_REGION_TOTAL", "function": "sum", "of": "TOTAL",
                                 "per": "G_REGION", "datatype": "number",
                                 "label": "Region total"}]
    assert out["links"] == [{"parent_group": "G_REGION", "parent_column": "REGION",
                             "child_query": "Q_LINES", "child_column": "ORDERID1",
                             "condition": "eq", "clause": "where"}]
    assert out["counts"]["summaries"] == 1 and out["counts"]["links"] == 1


# ------------------------------------------- read_code by the object's name

@pytest.mark.parametrize("asked,unit,note", [
    ("CF_TAX", "cf_taxformula", "CF_TAX is a formula column; its formula is the program unit cf_taxformula."),
    ("cf_tax", "cf_taxformula", None),
    ("CP_TITLE", "cp_titleformula", "CP_TITLE is a placeholder column; its formula"),
    ("F_TOTAL", "f_totalformattrigger", "F_TOTAL is a field; its format trigger"),
    ("P_REGION", "p_regionvalidtrigger", "P_REGION is a parameter; its validation trigger"),
])
def test_read_code_by_the_object_that_uses_the_unit(rcat, asked, unit, note):
    """Reported from Claude Desktop: read_code(report, "CF_DESNAME") failed and
    only cf_desnameformula worked. A formula column's PL/SQL is a function of
    another name, and so is a format trigger's and a validation trigger's."""
    out = tools.read_code(rcat, "monthly_sales", asked)
    assert out["name"] == unit and out["text"].startswith("function")
    if note:
        assert out["note"].startswith(note)


def test_read_code_by_the_unit_name_says_nothing_extra(rcat):
    out = tools.read_code(rcat, "monthly_sales", "cf_taxformula")
    assert out["name"] == "cf_taxformula" and "note" not in out


# -------------------------------------------------------------------- DUAL

def test_where_used_explains_dual(rcat):
    """Reported from Claude Desktop: DUAL is left out of table usage on purpose,
    and the answer read "no table called DUAL in this application"."""
    out = tools.where_used(rcat, "dual")
    assert not out["found"]
    assert "one-row dummy table" in out["note"] and "No table called" not in out["note"]


def test_a_report_object_with_no_file_is_said_not_a_crash(rcat):
    """Reported against a real application: most of its report objects name no
    .rdf (the form sets one at run time), and describe_form failed on them."""
    out = tools.describe_form(rcat, "launcher")
    runs = {r["report_object"]: r for r in out["runs_reports"]}
    assert runs["PREVIEW"]["filename"] is None and runs["PREVIEW"]["report_file"] is None
    assert "PREVIEW names no .rdf file" in out["runs_reports_unnamed_note"]
    assert "PREVIEW" not in out["runs_reports_note"]          # not "not loaded"


def test_the_migration_assistants_report_object_is_said_for_what_it_is(rcat):
    """RP2RRO has no file by design: rp2rro_run_product sets it on each call.
    The note says so, where the generic one guessed it might be unused."""
    note = tools.describe_form(rcat, "launcher")["runs_reports_unnamed_note"]
    assert "RP2RRO is the generic report object" in note and "rp2rro_run_product" in note
    assert "unused" not in note
