"""The MCP server: six reads over one Forms metadata database.

    sqling-mcp /path/to/forms-metadata.sqlite [--reports /path/to/reports-metadata.sqlite]

Nothing here parses a module, runs a form or writes a file. It opens the
database read-only and answers questions about it, which is the whole of the
first version on purpose: it depends on nothing but the standard library and
the MCP SDK, so it cannot break anything else in the pipeline.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer

from . import __version__, navigation, tools
from .db import Catalog, CatalogError

INSTRUCTIONS = """\
This is one Oracle Forms and Reports application, loaded into tables: its
forms, menus, libraries and object libraries, its reports, or both.

A module is identified by its FILE -- forms/ORDERS.fmb, reports/MONTHLY.rdf --
and every answer names files. A module's own name (module_name) is whatever
its developer typed: often not the file's, and two files can share one. Pass
a file name wherever a tool asks for one; the extension and folder may be
left off when that still picks out one file.

Start with list_modules to see what is here and schema() to see what can be
queried. describe_form gives one form's blocks, triggers, program units, menu
code, tables and the reports it runs; describe_report gives one report's
parameters, queries, groups, formulas, program units -- each with what it is
used as -- and the forms that run it. list_code lists the code across the
application (every ON-ERROR, every menu item, every report query) without its
source; read_code gives the source of one trigger, program unit, menu item,
menu startup code or report query; search finds a string in every line of
PL/SQL and report SQL in the application; where_used answers "which files
touch this table", from blocks and code alike. calls and called_by say what
one file navigates to and what navigates to it; reachable follows the calls
from a starting form; never_called lists the files nothing calls. A file's
name passed to a procedure sqling does not know to navigate -- often the
application's own wrapper that runs reports -- is said as "named", apart
from calls. A call
whose target the code works out at run time (CALL_FORM(v_form)) cannot be
followed, and each answer says how many there are -- "nothing calls X" is
only as sure as that. query runs your own SELECT when none of those fits --
join and group by file_name, not module_name; form_* and report_* tables can
be joined directly.

An object subclassed from an object library sets almost nothing itself --
its font, its format mask, an alert's whole text come from the library -- so
its own row reads as an object with nothing on it. effective() puts the two
halves together, and describe_form says which objects are subclassed and
from which file. When a file reports unresolved parents, the library it
names was not loaded into this database and what those objects inherit is
unknown.

Everything is read-only, and nothing leaves this machine.
"""


def build(path: Optional[str], reports: Optional[str] = None) -> MCPServer:
    catalog = Catalog(path, reports=reports)
    server = MCPServer(name="sqling-mcp", instructions=INSTRUCTIONS)

    def answer(fn, *args, **kwargs) -> Dict[str, Any]:
        """Run a tool; turn a CatalogError into an answer rather than a crash.

        A missing file or a bad SELECT is something the caller can fix, and
        it reads better as a sentence than as a traceback.
        """
        try:
            return fn(catalog, *args, **kwargs)
        except CatalogError as exc:
            return {"error": str(exc)}

    @server.tool(description="Every module in the application by its file, with its "
                             "kind and how many blocks, items, triggers and program "
                             "units it holds.")
    def list_modules() -> Dict[str, Any]:
        return answer(tools.list_modules)

    @server.tool(description="One module's shape: blocks and their data sources, "
                             "every trigger and what it fires on, program units, "
                             "and the tables the module uses.")
    def describe_form(file: str) -> Dict[str, Any]:
        return answer(tools.describe_form, file)

    @server.tool(description="The source of one trigger, program unit, menu item "
                             "(ITEM or MENU.ITEM) or menu startup code (STARTUP_CODE). "
                             "A trigger name repeats across a module, so pass on= (a "
                             "block, or BLOCK.ITEM) to pick which one.")
    def read_code(file: str, unit: str, on: Optional[str] = None) -> Dict[str, Any]:
        return answer(tools.read_code, file, unit, on)

    @server.tool(description="Find a string in every line of PL/SQL in the "
                             "application -- triggers, program units, menu item "
                             "commands and menu startup code -- with the file, the "
                             "object it is on and the line number.")
    def search(text: str, limit: int = 50) -> Dict[str, Any]:
        return answer(tools.search, text, limit)

    @server.tool(description="List code without its source: every trigger, program "
                             "unit, menu item command and menu startup code, across the "
                             "application or in one file. kind= narrows to trigger, "
                             "program unit, menu item or startup code; name= to one name "
                             "(ON-ERROR; % is a wildcard); module_kind= to form, menu, "
                             "library or object library. read_code gives the source.")
    def list_code(file: Optional[str] = None, kind: Optional[str] = None,
                  name: Optional[str] = None, module_kind: Optional[str] = None,
                  limit: int = 200) -> Dict[str, Any]:
        return answer(tools.list_code, file, kind, name, module_kind, limit)

    @server.tool(description="What one file navigates to, call by call: every "
                             "CALL_FORM, OPEN_FORM, NEW_FORM, GO_FORM, RUN_PRODUCT, "
                             "rp2rro_run_product, SRW.RUN_REPORT and RUN_REPORT_OBJECT, and "
                             "its menu module, with where each call is and the loaded file "
                             "it leads to -- or why it cannot be followed (a target worked "
                             "out at run time, a file not loaded).")
    def calls(file: str) -> Dict[str, Any]:
        return answer(navigation.calls, file)

    @server.tool(description="Everything in the application that navigates to one file: "
                             "which files call it, by which call, from where.")
    def called_by(file: str) -> Dict[str, Any]:
        return answer(navigation.called_by, file)

    @server.tool(description="Every file reachable from a starting file -- usually the "
                             "application's first form -- following its calls, with how many "
                             "steps away each is and the path to it, and the forms, menus and "
                             "reports not reached. max_depth limits the steps; "
                             "follow_named=true also follows a file's name passed to a "
                             "procedure that is not a known navigation call (an "
                             "application's own report wrapper, say).")
    def reachable(start: str, max_depth: Optional[int] = None,
                  follow_named: bool = False) -> Dict[str, Any]:
        return answer(navigation.reachable, start, max_depth, follow_named)

    @server.tool(description="Forms, menus and reports nothing in the application calls -- "
                             "candidates for dead code, or entry points -- with, apart, the "
                             "ones only named in a call to some other procedure. kind= "
                             "narrows to form, menu or report; kind=library lists the "
                             "libraries no form attaches.")
    def never_called(kind: Optional[str] = None) -> Dict[str, Any]:
        return answer(navigation.never_called, kind)

    @server.tool(description="Which modules read, insert, update or delete a table, "
                             "which blocks are based on it, and where its name turns "
                             "up in code.")
    def where_used(table: str) -> Dict[str, Any]:
        return answer(tools.where_used, table)

    @server.tool(description="Run your own SELECT over the metadata tables. One "
                             "statement, read-only. Use schema() first to see the "
                             "tables and columns.")
    def query(sql: str, limit: int = 100) -> Dict[str, Any]:
        return answer(tools.query, sql, limit)

    @server.tool(description="One report's shape: its parameters, its queries with "
                             "their SQL and groups, formulas, placeholders, report "
                             "triggers, every program unit and what it is used as (a "
                             "formula, a format trigger, the before-report trigger), and "
                             "the forms that run it. Name the report by its file.")
    def describe_report(file: str) -> Dict[str, Any]:
        return answer(tools.describe_report, file)

    @server.tool(description="One object as the form runs it: the properties it "
                             "sets itself, the ones it takes from the object "
                             "library or property class it is subclassed from, "
                             "and the two merged. Name it as BLOCK.ITEM, BLOCK, "
                             "or a top-level object such as an alert.")
    def effective(file: str, object: str, table: Optional[str] = None) -> Dict[str, Any]:
        return answer(tools.effective, file, object, table)

    @server.tool(description="The tables in this database with their row counts, or "
                             "one table's columns.")
    def schema(table: Optional[str] = None) -> Dict[str, Any]:
        return answer(tools.schema, table)

    return server


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["setup"]:
        from . import configure
        return configure.main(argv[1:])
    parser = argparse.ArgumentParser(
        prog="sqling-mcp",
        description="MCP server over an Oracle Forms and Reports metadata "
                    "database. An AI client starts this over stdio; to register "
                    "a database with the clients on this machine, run "
                    "'sqling-mcp setup DATABASE' instead.")
    parser.add_argument("database", nargs="?",
                        default=os.environ.get("SQLING_MCP_DATABASE"),
                        help="the .sqlite built from your forms by the Batch "
                             "Oracle Forms page on sqling.com (or one holding "
                             "forms and reports). Defaults to $SQLING_MCP_DATABASE.")
    parser.add_argument("--reports", metavar="DATABASE",
                        default=os.environ.get("SQLING_MCP_REPORTS"),
                        help="the .sqlite built from your reports by the Batch "
                             "Oracle Reports page, opened beside the forms. "
                             "Defaults to $SQLING_MCP_REPORTS.")
    parser.add_argument("--version", action="version",
                        version="sqling-mcp " + __version__)
    options = parser.parse_args(argv)
    if not options.database and not options.reports:
        parser.error("name the metadata database (or set SQLING_MCP_DATABASE)")
    try:
        server = build(options.database, options.reports)
    except CatalogError as exc:
        print("sqling-mcp: %s" % exc, file=sys.stderr)
        return 2
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
