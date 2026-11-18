"""How an application's modules lead to one another: what calls what.

Read from the calls the loader recorded (form_code_calls, report_code_calls:
every CALL_FORM, OPEN_FORM, NEW_FORM, GO_FORM, RUN_PRODUCT, rp2rro_run_product,
SRW.RUN_REPORT and RUN_REPORT_OBJECT, with where it is), the report objects a
form holds, and the menu module each form names. A call names its target as a
file -- folder and extension off, so CALL_FORM('E:\\APP\\ORDERS.fmx') is ORDERS
-- and is matched here to the loaded file of that name. Never by module name:
a module's own name is whatever its developer typed.

A call the code works out at run time (CALL_FORM(v_form)) has no target. It is
kept and said, because "nothing calls X" is only as true as the calls that can
be followed, and an answer that hides the others would overstate it.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Any, Dict, List, Optional

from .db import Catalog, CatalogError
from .tools import _have

#: Forms' own menus: a form that names one of these names no file.
_BUILT_IN_MENUS = {"DEFAULT", "DEFAULT&SMARTBAR"}


def file_key(path: Optional[str]) -> str:
    """A file as a call names it: no folder, no extension, upper case.

    ORDERS.fmb, forms/orders.fmb and orders.fmb.xml are all ORDERS.
    """
    leaf = (path or "").replace("\\", "/").strip().rsplit("/", 1)[-1]
    if leaf.lower().endswith(".xml"):
        leaf = leaf[:-4]
    return re.sub(r"\.(fmb|fmx|mmb|mmx|pll|plx|olb|rdf|rep|rex|jsp)$", "", leaf, flags=re.I).upper()


def _recorded(cat: Catalog) -> bool:
    return (("form" in cat.families and cat.has("form_code_calls"))
            or ("report" in cat.families and cat.has("report_code_calls")))


def _need_calls(cat: Catalog) -> None:
    if not _recorded(cat):
        raise CatalogError(
            "This database was built before calls were recorded, so what calls "
            "what is not in it. Build it again on the Batch Oracle Forms (or "
            "Reports) page on sqling.com.")


def _files(cat: Catalog) -> List[Dict[str, Any]]:
    out = []
    if "form" in cat.families:
        for r in cat.rows("SELECT form_file_pk AS pk, file_name, module_kind FROM form_files"):
            out.append({"pk": r["pk"], "file": r["file_name"], "family": "form",
                        "kind": (r["module_kind"] or "form").lower()})
    if "report" in cat.families:
        for r in cat.rows("SELECT report_file_pk AS pk, file_name FROM report_files"):
            out.append({"pk": r["pk"], "file": r["file_name"], "family": "report",
                        "kind": "report"})
    return out


def graph(cat: Catalog) -> Dict[str, Any]:
    """Every edge in the application, worked out once per catalog."""
    cached = getattr(cat, "_navigation", None)
    if cached is not None:
        return cached
    files = _files(cat)
    by_key: Dict[tuple, List[Dict[str, Any]]] = {}
    for f in files:
        by_key.setdefault((f["kind"], file_key(f["file"])), []).append(f)
    form_of = {f["pk"]: f for f in files if f["family"] == "form"}
    report_of = {f["pk"]: f for f in files if f["family"] == "report"}

    # A form's report objects, by name: RUN_REPORT_OBJECT runs one of them,
    # and the object names the .rdf.
    objects: Dict[tuple, Optional[str]] = {}
    if "form" in cat.families and _have(cat, "form_reports", "filename"):
        for r in cat.rows("SELECT form_file_fk, form_report_name, filename FROM form_reports"):
            objects[(r["form_file_fk"], (r["form_report_name"] or "").upper())] = r["filename"]

    def resolve(kind: str, key: Optional[str]):
        if not key:
            return None, []
        want = {"form": ["form"], "report": ["report"], "menu": ["menu"]}[kind]
        found = [f for k in want for f in by_key.get((k, key), [])]
        if len(found) == 1:
            return found[0]["file"], []
        return None, sorted((f["file"] for f in found), key=str.upper)

    edges: List[Dict[str, Any]] = []

    def add(src: Dict[str, Any], row, family: str):
        kind = row["target_kind"]
        target = row["target"]
        if kind == "mention":
            # A module's name passed to some other call -- the application's own
            # report wrapper, say. Most mentions name nothing loaded; those are
            # noise and are dropped. The rest are said as names, not calls.
            # A name can match more than one file -- ORDERS.fmb and
            # ORDERS.rdf, or one form in two folders -- and nothing in a
            # procedure's arguments says which was meant. Each is kept, and
            # says what else the name matches, rather than the name being lost.
            hits = [f for k in ("form", "menu", "report") for f in by_key.get((k, target), [])
                    if f["file"] != src["file"]]
            # When the name matches files of more than one kind, the procedure's
            # own name usually says which it runs: BUILD_REPORT_URL a report,
            # FIND_CALL_FORM a form. Taken when it says one and only one.
            chosen_by_call = False
            if len({h["kind"] for h in hits}) > 1:
                said = _kind_named_by(row["call_name"])
                pick = [h for h in hits if h["kind"] == said]
                if said and pick:
                    hits, chosen_by_call = pick, True
            for hit in hits:
                edge = {"from": src["file"], "from_kind": src["kind"],
                        "in": "%s %s" % (_words(row["owner_type"]), row["owner_name"]),
                        "line": row["line_no"], "call": row["call_name"],
                        "kind": hit["kind"], "target": target, "written": row["written"],
                        "to": hit["file"], "named": True}
                if len(hits) > 1:
                    edge["also_matches"] = sorted((h["file"] for h in hits if h is not hit),
                                                  key=str.upper)
                if chosen_by_call:
                    edge["chosen_by"] = ("the name matches a form and a report; %s runs %ss"
                                         % (row["call_name"], hit["kind"]))
                edges.append(edge)
            return
        edge = {"from": src["file"], "from_kind": src["kind"],
                "in": "%s %s" % (_words(row["owner_type"]), row["owner_name"]),
                "line": row["line_no"], "call": row["call_name"], "kind": kind,
                "target": target, "written": row["written"], "to": None}
        if kind == "report object":
            edge["report_object"] = target
            if target is None:
                edge["why"] = "the report object is held in a variable"
            elif src["kind"] == "library":
                # A library runs the report object of the form that attached it.
                edge["why"] = ("a library: %s is the report object of whichever form attaches "
                               "it" % target)
            elif (src["pk"], target) not in objects:
                edge["why"] = "this form has no report object called %s" % target
            else:
                filename = objects[(src["pk"], target)]
                if not filename:
                    edge["why"] = ("report object %s names no file: the form sets it at "
                                   "run time" % target)
                else:
                    edge["target"] = file_key(filename)
                    edge["to"], cand = resolve("report", edge["target"])
                    if cand:
                        edge["candidates"] = cand
                    elif not edge["to"]:
                        edge["why"] = "%s is not loaded" % filename
            edge["kind"] = "report"
        elif target is None:
            edge["why"] = "the target is worked out at run time: %s" % (row["written"] or "?")
        else:
            edge["to"], cand = resolve(kind if kind in ("form", "report") else "form", target)
            if cand:
                edge["candidates"] = cand
                edge["why"] = "%d loaded files have that name; give the folder" % len(cand)
            elif not edge["to"]:
                edge["why"] = "no loaded %s is called %s" % (kind, target)
        edges.append(edge)

    if "form" in cat.families and cat.has("form_code_calls"):
        for row in cat.rows("SELECT * FROM form_code_calls ORDER BY form_file_fk, owner_name, line_no"):
            src = form_of.get(row["form_file_fk"])
            if src:
                add(src, row, "form")
    if "report" in cat.families and cat.has("report_code_calls"):
        for row in cat.rows("SELECT * FROM report_code_calls ORDER BY report_file_fk, owner_name, line_no"):
            src = report_of.get(row["report_file_fk"])
            if src:
                add(src, row, "report")
    # A form names its menu module; that is how the menu gets to run at all.
    if "form" in cat.families and _have(cat, "form_files", "menu_module"):
        for r in cat.rows("SELECT form_file_pk, menu_module FROM form_files WHERE menu_module IS NOT NULL"):
            src = form_of.get(r["form_file_pk"])
            key = file_key(r["menu_module"])
            if not src or not key or key in _BUILT_IN_MENUS:
                continue
            to, cand = resolve("menu", key)
            edge = {"from": src["file"], "from_kind": src["kind"], "in": "the form's Menu Module",
                    "line": None, "call": "MENU MODULE", "kind": "menu", "target": key,
                    "written": r["menu_module"], "to": to}
            if cand:
                edge["candidates"] = cand
            elif not to:
                edge["why"] = "no loaded menu is called %s" % key
            edges.append(edge)

    out = {"files": files, "edges": edges}
    cat._navigation = out
    return out


def _kind_named_by(call: Optional[str]) -> Optional[str]:
    """What kind of module a procedure's name says it runs, if it says one:
    BUILD_REPORT_URL, RUN_RPT -- a report; FIND_CALL_FORM, OPEN_A_FORM -- a form."""
    words = set(re.split(r"[^A-Z]+", (call or "").upper()))
    report = bool(words & {"REPORT", "REPORTS", "RPT", "RDF"}) or "REPORT" in (call or "").upper()
    form = bool(words & {"FORM", "FORMS", "FMX", "FMB"}) or "FORM" in (call or "").upper()
    if report != form:
        return "report" if report else "form"
    return None


def _words(name: Optional[str]) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name or "code").lower()


def _public(edge: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in edge.items() if k != "from_kind" and v is not None}


# ------------------------------------------------------------------ tools

def calls(cat: Catalog, name: str) -> Dict[str, Any]:
    """What one file navigates to, call by call."""
    _need_calls(cat)
    found = cat.find(name)
    every = [e for e in graph(cat)["edges"] if e["from"] == found["file_name"]]
    mine = [e for e in every if not e.get("named")]
    named = [e for e in every if e.get("named")]
    leads = sorted({e["to"] for e in mine if e["to"]}, key=str.upper)
    open_ends = [e for e in mine if not e["to"]]
    out = {"file": found["file_name"], "leads_to": leads,
           "calls": [_public(e) for e in mine],
           "counts": {"calls": len(mine), "files": len(leads), "not_followed": len(open_ends)}}
    if named:
        out["names"] = sorted({e["to"] for e in named}, key=str.upper)
        out["named_in"] = [_public(e) for e in named]
        out["names_note"] = ("These files are named in calls sqling does not know to navigate "
                             "-- often the application's own wrapper that opens a form or runs "
                             "a report. Read the call to be sure.")
    if open_ends:
        out["note"] = ("%d call(s) could not be followed to a loaded file -- each says why "
                       "(a target in a variable, a file not loaded)." % len(open_ends))
    return out


def called_by(cat: Catalog, name: str) -> Dict[str, Any]:
    """Everything in the application that navigates to one file."""
    _need_calls(cat)
    found = cat.find(name)
    g = graph(cat)
    every = [e for e in g["edges"] if e["to"] == found["file_name"]
             or found["file_name"] in (e.get("candidates") or [])]
    mine = [e for e in every if not e.get("named")]
    named = [e for e in every if e.get("named")]
    by = sorted({e["from"] for e in mine}, key=str.upper)
    out = {"file": found["file_name"], "called_by": by,
           "calls": [_public(e) for e in mine],
           "counts": {"calls": len(mine), "files": len(by)}}
    if named:
        out["named_by"] = sorted({e["from"] for e in named}, key=str.upper)
        out["named_in"] = [_public(e) for e in named]
        out["counts"]["named_in"] = len(named)
    dynamic = sum(1 for e in g["edges"] if e["target"] is None and not e.get("named"))
    if not mine:
        out["note"] = "Nothing loaded calls %s%s." % (
            found["file_name"], "" if not named else
            ", but %d call(s) in %d file(s) pass its name to a procedure -- see named_in"
            % (len(named), len(out["named_by"])))
    if dynamic:
        out["dynamic_calls"] = dynamic
        out.setdefault("note", "")
        out["note"] = (out["note"] + " %d call(s) in the application work their target out "
                       "at run time and cannot be followed; one of them may lead here."
                       % dynamic).strip()
    return out


def reachable(cat: Catalog, start: str, max_depth: Optional[int] = None,
              follow_named: bool = False) -> Dict[str, Any]:
    """Every file reachable from a starting file, nearest first.

    ``follow_named`` follows a file's name passed to a call sqling does not know
    to navigate -- an application's own wrapper -- as if it were a call.
    """
    _need_calls(cat)
    first = cat.find(start)["file_name"]
    g = graph(cat)
    out_edges: Dict[str, List[Dict[str, Any]]] = {}
    skipped_names = 0
    for e in g["edges"]:
        if e.get("named") and not follow_named:
            continue
        out_edges.setdefault(e["from"], []).append(e)
    distance = {first: 0}
    via: Dict[str, str] = {}
    queue = deque([first])
    open_ends = []
    while queue:
        here = queue.popleft()
        if max_depth is not None and distance[here] >= max_depth:
            continue
        for e in out_edges.get(here, []):
            if not e["to"]:
                open_ends.append(_public(e))
                continue
            if e["to"] not in distance:
                distance[e["to"]] = distance[here] + 1
                via[e["to"]] = here
                queue.append(e["to"])

    def path(f):
        steps = [f]
        while steps[-1] in via:
            steps.append(via[steps[-1]])
        return list(reversed(steps))

    reached = sorted(distance, key=lambda f: (distance[f], f.upper()))
    everything = {f["file"]: f for f in g["files"]}
    not_reached = sorted(f for f, info in everything.items()
                         if f not in distance and info["kind"] in ("form", "menu", "report"))
    out = {"start": first,
           "reached": [{"file": f, "steps": distance[f], "path": path(f)} for f in reached],
           "not_reached": not_reached,
           "counts": {"reached": len(reached), "not_reached": len(not_reached),
                      "calls_not_followed": len(open_ends)}}
    if not follow_named:
        skipped_names = sum(1 for e in g["edges"] if e.get("named") and e["from"] in distance
                            and e["to"] not in distance)
        if skipped_names:
            out["names_not_followed"] = skipped_names
            out["names_note"] = ("%d file(s) are named by reached files in calls sqling does not "
                                 "know to navigate (an application's own wrapper, often). Pass "
                                 "follow_named=true to follow them." % skipped_names)
    if open_ends:
        out["calls_not_followed"] = open_ends[:50]
        out["note"] = ("%d call(s) on the way could not be followed (a target worked out at "
                       "run time, or a file not loaded), so not_reached may hold files that "
                       "are in fact reached." % len(open_ends))
    return out


def never_called(cat: Catalog, kind: Optional[str] = None) -> Dict[str, Any]:
    """Forms, menus and reports that nothing in the application calls.

    A file nothing calls but whose name is passed to some procedure -- an
    application's own report wrapper -- is listed apart, as named_only: it may
    well be run that way. Libraries are attached, not called; kind="library"
    lists the ones no loaded form attaches.
    """
    want = (kind or "").strip().lower() or None
    if want in ("library", "libraries"):
        return _never_attached(cat)
    if want and want not in ("form", "menu", "report"):
        raise CatalogError("kind is form, menu, report or library%s"
                           % (" -- an object library is subclassed from, not called "
                              "(see effective)" if "object" in want else ""))
    _need_calls(cat)
    g = graph(cat)
    called = {e["to"] for e in g["edges"] if e["to"] and not e.get("named")}
    for e in g["edges"]:
        if not e.get("named"):
            called.update(e.get("candidates") or [])
    named: Dict[str, set] = {}
    for e in g["edges"]:
        if e.get("named"):
            named.setdefault(e["to"], set()).add(e["call"])
    kinds = ("form", "menu", "report") if not want else (want,)
    rows = sorted((f for f in g["files"] if f["kind"] in kinds and f["file"] not in called),
                  key=lambda f: f["file"].upper())
    plain = [f for f in rows if f["file"] not in named]
    only_named = [f for f in rows if f["file"] in named]
    dynamic = sum(1 for e in g["edges"] if e["target"] is None and not e.get("named"))
    out = {"files": [{"file": f["file"], "kind": f["kind"]} for f in plain],
           "count": len(plain)}
    notes = ["An application's starting form is called by nothing, and so is a form run "
             "only from outside it (a desktop shortcut, another application)."]
    if only_named:
        out["named_only"] = [{"file": f["file"], "kind": f["kind"],
                              "named_in": sorted(named[f["file"]])} for f in only_named]
        notes.append("%d more are called by nothing sqling knows, but their names are passed "
                     "to %s -- most likely the application's own way of running them; see "
                     "named_only." % (len(only_named),
                                      ", ".join(sorted({c for f in only_named for c in named[f["file"]]})[:4])))
    if dynamic:
        out["dynamic_calls"] = dynamic
        notes.append("%d call(s) work their target out at run time and cannot be followed; a "
                     "file listed here may be reached through one of them." % dynamic)
    out["note"] = " ".join(notes)
    return out


def _never_attached(cat: Catalog) -> Dict[str, Any]:
    """Libraries no loaded form attaches -- the question "never called" means
    for a library, which is attached to a form rather than called."""
    if "form" not in cat.families:
        raise CatalogError("no forms are loaded, so there are no libraries")
    libraries = [r["file_name"] for r in cat.rows(
        "SELECT file_name FROM form_files WHERE lower(module_kind) = 'library' ORDER BY file_name")]
    attached = set()
    if _have(cat, "form_attached_libraries", "form_attached_library_name"):
        cols = cat.columns("form_attached_libraries")
        for r in cat.rows("SELECT * FROM form_attached_libraries"):
            for col in ("library_location", "form_attached_library_name"):
                if col in cols and r[col]:
                    attached.add(file_key(r[col]))
    loose = [f for f in libraries if file_key(f) not in attached]
    return {"files": [{"file": f, "kind": "library"} for f in loose], "count": len(loose),
            "note": "Libraries are attached to forms, not called. These are the libraries no "
                    "loaded form attaches; %d of %d are attached by at least one."
                    % (len(libraries) - len(loose), len(libraries))}
