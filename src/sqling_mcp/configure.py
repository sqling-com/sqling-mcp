"""``sqling-mcp setup``: tell an AI client where the metadata database is.

The server is the easy half. What stops people is the other half: finding the
client's config file, writing JSON by hand, getting a Windows path through it
with its backslashes intact, and pointing at a Python that can actually import
this package. This does that, for the clients that read MCP servers from a
file:

    Claude Desktop   claude_desktop_config.json   "mcpServers"
    VS Code          the user mcp.json (Copilot)  "servers"
                     or <workspace>/.vscode/mcp.json with --workspace
    Claude Code      ``claude mcp add --scope user``

Three rules, because these files belong to the user and hold other servers:

* never write a file we could not parse -- a config with comments, or one
  that is simply broken, is left alone and the entry is printed instead;
* keep a ``.bak`` of every file before changing it, and write the new one in
  a single replace, so a crash halfway leaves the old file, not half of one;
* never overwrite an entry of the same name that says something else unless
  asked to (``--replace``), since that entry is probably another application.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import tools
from .db import Catalog, CatalogError

CLIENTS = ("claude-desktop", "vscode", "claude-code")


# ------------------------------------------------------------ the entry
def server_command(database: Optional[str], executable: Optional[str] = None,
                   prefix: Optional[str] = None,
                   reports: Optional[str] = None) -> Dict[str, object]:
    """The command a client runs to start this server on ``database``.

    The Python running setup is the one that has this package installed, so
    its absolute path is written rather than "python" or "sqling-mcp": a
    client started from the Start menu or the Dock does not see the PATH a
    terminal does, and on Windows pip's Scripts folder is often not on it at
    all. ``python -m`` then needs no Scripts folder either.

    uvx is the exception. It runs a tool from a cache directory that it is
    free to clear, so pointing a client at that Python would work today and
    break some later week; for uvx the entry runs uvx again instead.
    """
    executable = executable or sys.executable
    prefix = prefix if prefix is not None else sys.prefix
    args: List[str] = [os.path.abspath(database)] if database else []
    if reports:
        args += ["--reports", os.path.abspath(reports)]
    if re.search(r"[\\/]uv[\\/](cache[\\/])?archive-v\d+[\\/]", prefix):
        return {"command": "uvx", "args": ["sqling-mcp"] + args}
    return {"command": executable, "args": ["-m", "sqling_mcp.server"] + args}


def default_name(database: str) -> str:
    """A server name from the file name: ``C:/x/My App.sqlite`` -> ``my-app``.

    Clients allow letters, digits, ``-`` and ``_`` in a name, and Claude Code
    refuses anything else, so everything else becomes a hyphen.
    """
    stem = os.path.splitext(os.path.basename(database))[0]
    name = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-_")
    return name or "forms"


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or ""))


# ------------------------------------------------------------ where clients keep config
@dataclass
class Place:
    """One config file a client reads, and how its entries are shaped."""
    client: str            # one of CLIENTS
    label: str             # what to call it when telling the user
    path: str              # the file
    key: str               # "mcpServers" (Claude Desktop) or "servers" (VS Code)
    restart: str           # what the user has to do afterwards

    def entry(self, command: Dict[str, object]) -> Dict[str, object]:
        if self.key == "servers":            # VS Code wants the transport named
            return dict({"type": "stdio"}, **command)
        return dict(command)


def _config_home(env: Dict[str, str], home: str, platform: str) -> str:
    """Where per-user application settings live on this platform."""
    if platform == "win32":
        return env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support")
    return env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")


def claude_desktop_places(env: Dict[str, str], home: str,
                          platform: str) -> List[Place]:
    """Claude Desktop's config file -- every copy of it that could be read.

    On Windows the Microsoft Store build of the app runs packaged, and its
    %APPDATA% is redirected into its package folder; a config written to the
    ordinary %APPDATA%\\Claude is then never seen. Both are offered, and only
    the ones whose Claude folder exists are used, so a machine with one build
    gets one file.
    """
    dirs = [os.path.join(_config_home(env, home, platform), "Claude")]
    if platform == "win32":
        local = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        packages = os.path.join(local, "Packages")
        if os.path.isdir(packages):
            for name in sorted(os.listdir(packages)):
                if name.lower().startswith("claude_"):
                    dirs.append(os.path.join(packages, name, "LocalCache",
                                             "Roaming", "Claude"))
    restart = ("Quit Claude Desktop completely (from the tray icon or the menu "
               "bar, not just the window) and open it again.")
    return [Place("claude-desktop", "Claude Desktop",
                  os.path.join(d, "claude_desktop_config.json"),
                  "mcpServers", restart) for d in dirs]


def vscode_places(env: Dict[str, str], home: str, platform: str,
                  workspace: Optional[str] = None) -> List[Place]:
    """VS Code's user mcp.json, for stable and Insiders -- or one workspace's."""
    restart = ("In VS Code, open Copilot Chat, switch it to Agent mode and turn "
               "the server on in the tools picker. If it does not show, run "
               "'MCP: List Servers' from the Command Palette and start it.")
    if workspace:
        return [Place("vscode", "VS Code workspace",
                      os.path.join(os.path.abspath(workspace), ".vscode", "mcp.json"),
                      "servers", restart)]
    base = _config_home(env, home, platform)
    return [Place("vscode", label, os.path.join(base, folder, "User", "mcp.json"),
                  "servers", restart)
            for folder, label in (("Code", "VS Code"),
                                  ("Code - Insiders", "VS Code Insiders"))]


def installed(place: Place) -> bool:
    """Whether the client that reads this file is on the machine.

    The config file itself may not exist yet -- Claude Desktop creates it
    the first time someone opens Edit Config -- but its folder does as soon
    as the app has run once. A workspace is always there: the user named it.
    """
    if place.label == "VS Code workspace":
        return os.path.isdir(os.path.dirname(os.path.dirname(place.path)))
    return os.path.isdir(os.path.dirname(place.path))


# ------------------------------------------------------------ changing one file
@dataclass
class Outcome:
    place: str
    status: str            # added | replaced | unchanged | removed | absent | skipped | would-*
    detail: str = ""
    restart: str = ""


class ConfigError(Exception):
    """A config file we will not touch, with the reason in words."""


def _read(path: str) -> Dict[str, object]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as handle:   # Notepad writes a BOM
        text = handle.read()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "%s is not plain JSON (%s at line %d), so it was left alone. It may "
            "hold comments, which this command does not rewrite."
            % (path, exc.msg, exc.lineno))
    if not isinstance(data, dict):
        raise ConfigError("%s does not hold a JSON object, so it was left alone." % path)
    return data


def _write(path: str, data: Dict[str, object]) -> None:
    """Back up, then replace in one step."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def _same_entry(a: Any, b: Any) -> bool:
    """Whether two entries start the same server.

    A Windows path is one file whatever its case: c:\\sqling\\app.sqlite and
    C:\\sqling\\app.sqlite are the same, and an entry written by hand (or by
    an older setup) that differs only so is not "somewhere else".
    """
    def norm(v):
        if isinstance(v, str) and re.match(r"^[A-Za-z]:[\\/]", v):
            return v.replace("/", "\\").lower()
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        return v
    return a is not None and norm(a) == norm(b)


def add_to_file(place: Place, name: str, command: Dict[str, object],
                replace: bool = False, dry_run: bool = False) -> Outcome:
    try:
        data = _read(place.path)
    except ConfigError as exc:
        return Outcome(place.label, "skipped", str(exc))
    servers = data.get(place.key)
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        return Outcome(place.label, "skipped",
                       '%s has a "%s" that is not an object, so it was left alone.'
                       % (place.path, place.key))
    entry = place.entry(command)
    existing = servers.get(name)
    if _same_entry(existing, entry):
        return Outcome(place.label, "unchanged",
                       "%s already has %r pointing here." % (place.path, name),
                       place.restart)
    if existing is not None and not replace:
        return Outcome(place.label, "skipped",
                       "%s already has a server called %r that points somewhere "
                       "else. Pick another name with --name, or pass --replace."
                       % (place.path, name))
    status = "replaced" if existing is not None else "added"
    if dry_run:
        return Outcome(place.label, "would " + ("replace" if existing else "add"),
                       "%s: %s" % (place.path, json.dumps({name: entry})))
    servers[name] = entry
    data[place.key] = servers
    _write(place.path, data)
    return Outcome(place.label, status, place.path, place.restart)


def remove_from_file(place: Place, name: str, dry_run: bool = False) -> Outcome:
    try:
        data = _read(place.path)
    except ConfigError as exc:
        return Outcome(place.label, "skipped", str(exc))
    servers = data.get(place.key)
    if not isinstance(servers, dict) or name not in servers:
        return Outcome(place.label, "absent", "no %r in %s" % (name, place.path))
    if dry_run:
        return Outcome(place.label, "would remove", place.path)
    del servers[name]
    _write(place.path, data)
    return Outcome(place.label, "removed", place.path, place.restart)


# ------------------------------------------------------------ Claude Code
Runner = Callable[[List[str]], "subprocess.CompletedProcess[str]"]


def _run(args: List[str]) -> "subprocess.CompletedProcess[str]":
    # claude prints a check mark (U+2714). Read as the Windows code page, that
    # is an error in the thread that reads the output: say UTF-8.
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _claude_scope(claude: str, name: str, run: Runner) -> Optional[str]:
    """The scope Claude Code holds a server in -- user, project or local --
    or None when it has no server of that name.

    `claude mcp get` says it two ways: "Scope: Project config", and the very
    command that removes it ("claude mcp remove NAME -s project"). A server
    added by hand in a project is not in the user scope this command adds to.
    """
    done = run([claude, "mcp", "get", name])
    if done.returncode != 0:
        return None
    text = (done.stdout or "") + "\n" + (done.stderr or "")
    said = re.search(r"claude mcp remove \S+ (?:-s|--scope) (\w+)", text)
    if said:
        return said.group(1).lower()
    said = re.search(r"Scope:\s*(\w+)", text)
    return said.group(1).lower() if said else "user"


def claude_code_add(claude: str, name: str, command: Dict[str, object],
                    replace: bool = False, dry_run: bool = False,
                    run: Runner = _run) -> Outcome:
    """Claude Code keeps its own file and its own command for changing it.

    ``--scope user`` so the server is there in every folder, which is what
    the other two clients do. Claude Code will not add a name it already
    has, so the entry is looked up first and only removed when asked to.
    """
    label = "Claude Code"
    restart = "Start a new Claude Code session; /mcp lists the server."
    add = [claude, "mcp", "add", "--scope", "user", name, "--",
           str(command["command"])] + [str(a) for a in command["args"]]
    scope = _claude_scope(claude, name, run)
    had = scope is not None
    if had and not replace:
        return Outcome(label, "skipped",
                       "Claude Code already has a server called %r. Pick another "
                       "name with --name, or pass --replace." % name)
    if dry_run:
        return Outcome(label, "would " + ("replace" if had else "add"),
                       " ".join(_quote(a) for a in add))
    if had:
        run([claude, "mcp", "remove", "--scope", scope, name])
    done = run(add)
    if done.returncode:
        return Outcome(label, "skipped", (done.stderr or done.stdout).strip()
                       or "claude mcp add failed")
    return Outcome(label, "replaced" if had else "added", "user scope", restart)


def claude_code_remove(claude: str, name: str, dry_run: bool = False,
                       run: Runner = _run) -> Outcome:
    label = "Claude Code"
    scope = _claude_scope(claude, name, run)
    if scope is None:
        return Outcome(label, "absent", "no %r" % name)
    if dry_run:
        return Outcome(label, "would remove", "%s scope" % scope)
    done = run([claude, "mcp", "remove", "--scope", scope, name])
    if done.returncode:
        return Outcome(label, "skipped", (done.stderr or done.stdout).strip())
    return Outcome(label, "removed", "%s scope" % scope,
                   "Start a new Claude Code session.")


def probe(command: Dict[str, object]) -> Optional[str]:
    """Start the entry's Python the way a client will, and ask its version.

    A client that cannot start a server says little more than "failed", and
    says it in a log the user has never opened. Running the exact command
    first turns that into a sentence here, before any file is changed. uvx
    is not probed: it would fetch the package to answer.
    """
    if command["command"] == "uvx":
        return None
    args = [str(command["command"])] + [str(a) for a in command["args"][:2]] + ["--version"]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "could not run %s: %s" % (command["command"], exc)
    if done.returncode:
        return ("%s cannot start the server -- is sqling-mcp installed for that "
                "Python? (%s)" % (command["command"],
                                  (done.stderr or done.stdout).strip().splitlines()[-1:]
                                  or "no output"))
    return None


def _quote(arg: str) -> str:
    return '"%s"' % arg if re.search(r"\s", arg) else arg


# ------------------------------------------------------------ what to print instead
def snippets(name: str, command: Dict[str, object]) -> str:
    """Every client's entry, for the ones this could not or did not write."""
    desktop = {"mcpServers": {name: command}}
    vscode = {"servers": {name: dict({"type": "stdio"}, **command)}}
    code = " ".join(["claude mcp add --scope user", name, "--",
                     _quote(str(command["command"]))]
                    + [_quote(str(a)) for a in command["args"]])
    return "\n".join([
        "Claude Desktop -- Settings > Developer > Edit Config, merge in:",
        _indent(json.dumps(desktop, indent=2)),
        "",
        "VS Code (GitHub Copilot) -- 'MCP: Open User Configuration', or",
        ".vscode/mcp.json in a workspace, merge in:",
        _indent(json.dumps(vscode, indent=2)),
        "",
        "Claude Code:",
        "  " + code,
        "",
        "Any other MCP client: run this command over stdio --",
        "  " + " ".join(_quote(str(a)) for a in [command["command"]] + list(command["args"])),
    ])


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


# ------------------------------------------------------------ the command
def describe_database(path: Optional[str], reports: Optional[str] = None) -> str:
    """Open it the way the server will, and say what is in it."""
    cat = Catalog(path, reports=reports)      # raises CatalogError
    kinds: Dict[str, int] = {}
    for module in tools.list_modules(cat)["modules"]:
        kind = module["kind"] or "other"
        kinds[kind] = kinds.get(kind, 0) + 1
    total = sum(kinds.values())
    if not total:
        return "a metadata database with no modules in it"
    return "%d module%s -- %s" % (total, "" if total == 1 else "s",
                                  ", ".join("%s: %d" % (k, n)
                                            for k, n in sorted(kinds.items())))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sqling-mcp setup",
        description="Register a Forms metadata database with the AI clients on "
                    "this machine: Claude Desktop, VS Code (GitHub Copilot) and "
                    "Claude Code. Each client found is set up; the entry for "
                    "any other is printed.",
        epilog="Examples:\n"
               "  sqling-mcp setup C:\\sqling\\myapp.sqlite\n"
               "  sqling-mcp setup ~/forms/myapp.sqlite --name myapp --client vscode\n"
               "  sqling-mcp setup myapp-forms.sqlite --reports myapp-reports.sqlite\n"
               "  sqling-mcp setup --remove myapp",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reports", metavar="DATABASE",
                   help="the .sqlite built from your reports, opened beside the forms")
    p.add_argument("database", nargs="?",
                   help="the .sqlite built from your forms")
    p.add_argument("--name",
                   help="what the clients call the server (default: from the "
                        "file name). One per application.")
    p.add_argument("--client", action="append", choices=CLIENTS + ("all",),
                   help="set up only this client (repeatable). Default: every "
                        "client found on this machine.")
    p.add_argument("--workspace", metavar="DIR",
                   help="for VS Code, write DIR/.vscode/mcp.json instead of the "
                        "user configuration")
    p.add_argument("--replace", action="store_true",
                   help="overwrite an entry of the same name that points elsewhere")
    p.add_argument("--remove", metavar="NAME",
                   help="take the server NAME out of every client instead")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="change nothing; print each client's entry")
    p.add_argument("--dry-run", action="store_true",
                   help="say what would change, change nothing")
    return p


def main(argv: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
         home: Optional[str] = None, platform: Optional[str] = None,
         which: Callable[[str], Optional[str]] = shutil.which,
         run: Runner = _run, check: Callable[[Dict[str, object]], Optional[str]] = probe,
         out=None) -> int:
    """The whole command. Everything about the machine is a parameter, so the
    tests set up a pretend home folder instead of touching a real one."""
    out = out or sys.stdout
    env = dict(os.environ) if env is None else env
    home = home or os.path.expanduser("~")
    platform = platform or sys.platform
    options = parser().parse_args(argv)
    say = lambda *lines: print(*lines, sep="\n", file=out)

    wanted = set(options.client or ["all"])
    if "all" in wanted:
        wanted = set(CLIENTS)
    explicit = options.client is not None and "all" not in options.client

    if options.remove:
        if not valid_name(options.remove):
            say("sqling-mcp: %r is not a server name." % options.remove)
            return 2
        return _remove(options, wanted, env, home, platform, which, run, say)

    if not options.database and not options.reports:
        parser().print_usage(out)
        say("sqling-mcp setup: name the .sqlite built from your forms (and/or "
            "--reports the one built from your reports)")
        return 2
    database = (os.path.abspath(os.path.expanduser(options.database))
                if options.database else None)
    reports = (os.path.abspath(os.path.expanduser(options.reports))
               if options.reports else None)
    try:
        summary = describe_database(database, reports)
    except CatalogError as exc:
        say("sqling-mcp: %s" % exc)
        return 2
    name = options.name or default_name(database or reports)
    if not valid_name(name):
        say("sqling-mcp: %r is not a usable name -- letters, digits, - and _ only."
            % name)
        return 2
    command = server_command(database, reports=reports)
    say("%s: %s" % (" + ".join(p for p in (database, reports) if p), summary),
        "Server name: %s" % name, "")

    if options.print_only:
        say(snippets(name, command))
        return 0
    problem = check(command)
    if problem:
        say("sqling-mcp: %s" % problem, "Nothing was changed.")
        return 2

    outcomes: List[Outcome] = []
    places: List[Place] = []
    if "claude-desktop" in wanted:
        places += claude_desktop_places(env, home, platform)
    if "vscode" in wanted:
        places += vscode_places(env, home, platform, options.workspace)
    for place in places:
        if installed(place) or (explicit and place is _first(places, place.client)):
            outcomes.append(add_to_file(place, name, command, options.replace,
                                        options.dry_run))
    if "claude-code" in wanted:
        claude = which("claude")
        if claude:
            outcomes.append(claude_code_add(claude, name, command, options.replace,
                                            options.dry_run, run))
        elif explicit:
            outcomes.append(Outcome("Claude Code", "skipped",
                                    "the claude command is not on PATH"))
    return _report(outcomes, name, command, say)


def _first(places: List[Place], client: str) -> Optional[Place]:
    """The main config for a client -- the one to create when the user named
    the client but it has never been run (so its folder is not there yet)."""
    for place in places:
        if place.client == client:
            return place
    return None


def _remove(options, wanted, env, home, platform, which, run, say) -> int:
    name = options.remove
    outcomes: List[Outcome] = []
    places: List[Place] = []
    if "claude-desktop" in wanted:
        places += claude_desktop_places(env, home, platform)
    if "vscode" in wanted:
        places += vscode_places(env, home, platform, options.workspace)
    for place in places:
        if os.path.exists(place.path):
            outcomes.append(remove_from_file(place, name, options.dry_run))
    if "claude-code" in wanted and which("claude"):
        outcomes.append(claude_code_remove(which("claude"), name,
                                           options.dry_run, run))
    done = [o for o in outcomes if o.status != "absent"]
    for o in done:
        say("  %-18s %s  %s" % (o.place, o.status, o.detail))
    if not done:
        say("No client on this machine has a server called %r." % name)
        return 1
    for restart in _unique(o.restart for o in done if o.restart):
        say("", restart)
    return 0 if all(o.status != "skipped" for o in done) else 1


def _report(outcomes: List[Outcome], name: str, command: Dict[str, object],
            say) -> int:
    for o in outcomes:
        say("  %-18s %s  %s" % (o.place, o.status, o.detail))
    written = [o for o in outcomes
               if o.status in ("added", "replaced", "unchanged")]
    planned = [o for o in outcomes if o.status.startswith("would")]
    if not written and not planned:
        if not outcomes:
            say("No AI client found here (Claude Desktop, VS Code, Claude Code).")
        say("", "Add this server to your client by hand:", "", snippets(name, command))
        return 1 if any(o.status == "skipped" for o in outcomes) else 0
    if planned:
        say("", "Nothing changed (--dry-run).")
        return 0
    say("", "Next:")
    for restart in _unique(o.restart for o in written if o.restart):
        say("  - " + restart)
    say("  - Then ask it something about the application, e.g. "
        "\"Which forms update the ORDERS table?\"")
    return 0 if all(o.status != "skipped" for o in outcomes) else 1


def _unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
