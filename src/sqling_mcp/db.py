"""The metadata database, opened read-only.

An Oracle Forms application is loaded into tables by the Batch Oracle Forms
page on sqling.com, which reads the modules in the browser (or by SQLing's
command-line loader, which produces the same file). This reads that file and nothing else: no reader, no interpreter,
no node, no network. The database is a file someone hands over, and it is
never written here.
"""
from __future__ import annotations

import os
import pathlib
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence


class CatalogError(Exception):
    """Something the caller can fix: a missing file, an unknown module."""


# A single read. Anything else -- INSERT, PRAGMA, ATTACH, two statements --
# is refused before SQLite sees it, on top of the read-only connection: a
# caller should get "this tool reads" rather than "attempt to write a
# readonly database", and ATTACH would reach outside the one file.
_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class Catalog:
    """One loaded application: its forms, its reports, or both.

    Forms and reports are two schemas, and the loader writes them to two
    databases (or both into one file). Given two, the reports one is attached
    read-only beside the forms one; their tables do not share a name, so a
    query names report_files and form_files alike, unqualified, and a join
    from a form's report objects to the reports it runs is ordinary SQL.
    """

    FORM_TABLES = ("form_files", "form_blocks", "form_triggers")
    REPORT_TABLES = ("report_files",)

    def __init__(self, path: Optional[str] = None, reports: Optional[str] = None):
        if not path and not reports:
            raise CatalogError("name a forms database, a reports database, or both")
        first = path or reports
        self.path = os.path.abspath(first)
        self.reports_path = os.path.abspath(reports) if (path and reports) else None
        self.schemas = ["main"]
        self.conn = self._open(self.path)
        self.conn.row_factory = sqlite3.Row
        if self.reports_path:
            self._attach(self.reports_path)
        self.conn.execute("PRAGMA query_only = ON")

        self.families = set()
        if all(self._has_table(t) for t in self.FORM_TABLES):
            self.families.add("form")
        if all(self._has_table(t) for t in self.REPORT_TABLES):
            self.families.add("report")
        if not self.families:
            raise CatalogError(
                "%s is not a Forms or Reports metadata database. Build one with "
                "the Batch Oracle Forms or Reports page on sqling.com (choose "
                "'A metadata database' and use the .sqlite it writes)." % self.path)
        if self.reports_path and not self._has_table("report_files", "reports"):
            raise CatalogError(
                "%s is not a Reports metadata database (no report_files). Build "
                "one with the Batch Oracle Reports page on sqling.com."
                % self.reports_path)
        if self.reports_path and self._has_table("report_files", "main"):
            # Both would answer to report_files, and the forms file's would win.
            raise CatalogError(
                "%s already holds reports, so the reports in %s would be hidden. "
                "Open the one file on its own." % (self.path, self.reports_path))

    def _open(self, path: str) -> sqlite3.Connection:
        if not os.path.exists(path):
            raise CatalogError("no such database: %s" % path)
        try:
            # as_uri() percent-encodes the path, so a folder called "Q&A #2"
            # or "My App" is a file name, not a query string or a fragment.
            conn = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro",
                                   uri=True, check_same_thread=False)
            # SQLite opens any file lazily and only finds out on the first
            # read that it is a text file, a zip, an .fmb.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            raise CatalogError(
                "%s is not a SQLite database. Use the .sqlite that the Batch "
                "Oracle Forms or Reports page on sqling.com writes." % path)
        return conn

    def _attach(self, path: str) -> None:
        if not os.path.exists(path):
            raise CatalogError("no such database: %s" % path)
        try:
            self.conn.execute("ATTACH DATABASE ? AS reports",
                              (pathlib.Path(path).as_uri() + "?mode=ro",))
            self.conn.execute("SELECT count(*) FROM reports.sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            raise CatalogError(
                "%s is not a SQLite database. Use the reports .sqlite that the "
                "Batch Oracle Reports page on sqling.com writes." % path)
        self.schemas.append("reports")

    # ------------------------------------------------------------ plumbing
    def has(self, name: str) -> bool:
        """Whether this database carries a table at all.

        Databases outlive the loader that wrote them: one built before
        subclassing was resolved has no form_subclass_links, and the tools
        that read it have to answer anyway rather than fail on a schema
        from last month.
        """
        return self._has_table(name)

    def _has_table(self, name: str, schema: Optional[str] = None) -> bool:
        for one in ([schema] if schema else self.schemas):
            row = self.conn.execute(
                "SELECT 1 FROM %s.sqlite_master WHERE type='table' AND name=?" % one,
                (name,)).fetchone()
            if row is not None:
                return True
        return False

    def rows(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise CatalogError(str(exc))

    def read_only_rows(self, sql: str, params: Sequence[Any] = ()):
        """A caller's own SQL. One SELECT, nothing else."""
        if not _READ_ONLY.match(sql or ""):
            raise CatalogError("only SELECT (or WITH ... SELECT) is allowed here")
        if ";" in sql.strip().rstrip(";"):
            raise CatalogError("one statement at a time")
        return self.rows(sql, params)

    def tables(self) -> List[str]:
        names = set()
        for one in self.schemas:
            names.update(r["name"] for r in self.rows(
                "SELECT name FROM %s.sqlite_master WHERE type='table'" % one))
        return sorted(names)

    def columns(self, table: str) -> List[str]:
        if table not in self.tables():
            raise CatalogError("no such table: %s" % table)
        return [r["name"] for r in self.rows("PRAGMA table_info(%s)" % table)]

    # -------------------------------------------------------------- lookup
    def _files(self) -> List[Dict[str, Any]]:
        """Every loaded file, forms and reports, each with its family."""
        out = []
        if "form" in self.families:
            out += [dict(r, family="form", pk=r["form_file_pk"])
                    for r in self.rows("SELECT * FROM form_files")]
        if "report" in self.families:
            out += [dict(r, family="report", pk=r["report_file_pk"])
                    for r in self.rows("SELECT * FROM report_files")]
        return sorted(out, key=lambda r: (r["file_name"] or "").upper())

    def find(self, name: str, family: Optional[str] = None) -> Dict[str, Any]:
        """One file, form or report, by its name -- the only thing that
        identifies a module.

        A module's own name is whatever its developer typed: BIDS.fmb holds
        BIDS20170612, CONTRACTUAL_RECEIVING.rdf holds CONTRACTUAL_RECEIVING27,
        and two applications both having a FIS0061 is normal. So this matches
        the file the module was loaded from, spelt however someone will type
        it: forms/NW_ORDERS.fmb, NW_ORDERS.fmb, or NW_ORDERS with no
        extension. Never the module name.
        """
        wanted = (name or "").strip().replace("\\", "/")
        if not wanted:
            raise CatalogError("name a module's file")
        rows = self._files()
        key = wanted.upper()

        def spellings(file_name):
            f = (file_name or "").replace("\\", "/").upper()
            leaf = f.rsplit("/", 1)[-1]
            return {f, leaf, os.path.splitext(f)[0], os.path.splitext(leaf)[0]}

        found = [r for r in rows if key in spellings(r["file_name"])]
        if not found:
            near = [r["file_name"] for r in rows
                    if os.path.splitext(key.rsplit("/", 1)[-1])[0]
                    in (r["file_name"] or "").upper()][:8]
            hint = (" Did you mean %s?" % ", ".join(near)) if near else ""
            raise CatalogError("no file called %s in this application.%s" % (name, hint))
        if len(found) > 1:
            raise CatalogError(
                "%s matches %d files (%s); give the folder or the extension to say which."
                % (name, len(found), ", ".join(r["file_name"] for r in found)))
        got = found[0]
        if family and got["family"] != family:
            raise CatalogError(
                "%s is a %s, not a %s; use %s." % (
                    got["file_name"], got["family"], family,
                    "describe_report" if got["family"] == "report" else "describe_form"))
        return got

    def module(self, name: str) -> Dict[str, Any]:
        """One form, menu, library or object library, by its file."""
        if "form" not in self.families:
            raise CatalogError("no forms are loaded in this database, only reports")
        return self.find(name, family="form")

    def report(self, name: str) -> Dict[str, Any]:
        """One report, by its file."""
        if "report" not in self.families:
            raise CatalogError("no reports are loaded; open a reports database "
                               "with --reports")
        return self.find(name, family="report")


def value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        got = row[key]
    except (IndexError, KeyError):
        return default
    return default if got is None else got


def clip(text: Optional[str], limit: int) -> Dict[str, Any]:
    """Long text, cut where it stops being worth sending, and said so."""
    body = text or ""
    if len(body) <= limit:
        return {"text": body}
    return {"text": body[:limit],
            "truncated": True,
            "of_characters": len(body)}
