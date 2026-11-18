"""``sqling-mcp setup``: the config files it writes, and the ones it refuses to.

Nothing here touches a real client. Every test builds a pretend home folder
and a pretend set of installed apps, and the command is told that is the
machine. The database is a few empty tables -- the setup only has to know
the file is a Forms metadata database -- so these run anywhere, with no
fixture to build: on a CI runner, on Windows, on a machine with no node.
"""
import io
import json
import os
import sqlite3
import subprocess

import pytest

from sqling_mcp import configure


def _database(path, modules=(("EMP", "form"), ("EMP_LIB", "library"))):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE form_files (form_file_pk INTEGER PRIMARY KEY, module_name TEXT,
            file_name TEXT, file_kind TEXT, module_kind TEXT, module_version TEXT);
        CREATE TABLE form_blocks (form_file_fk INTEGER);
        CREATE TABLE form_items (form_file_fk INTEGER);
        CREATE TABLE form_triggers (form_file_fk INTEGER);
        CREATE TABLE form_program_units (form_file_fk INTEGER);
    """)
    for name, kind in modules:
        conn.execute("INSERT INTO form_files (module_name, file_name, module_kind) "
                     "VALUES (?, ?, ?)", (name, name.lower() + ".xml", kind))
    conn.commit()
    conn.close()
    return str(path)


class Machine:
    """A home folder with whichever clients the test installs in it."""

    def __init__(self, root, platform="darwin"):
        self.home = str(root / "home")
        os.makedirs(self.home)
        self.platform = platform
        self.env = {}
        if platform == "win32":
            self.env = {"APPDATA": os.path.join(self.home, "AppData", "Roaming"),
                        "LOCALAPPDATA": os.path.join(self.home, "AppData", "Local")}
        self.claude = None            # path of the claude command, if "installed"
        self.calls = []               # every claude command run
        self.known = set()            # servers Claude Code already has
        self.broken = None            # what probing the entry's Python says, if anything

    def check(self, command):
        return self.broken

    def base(self):
        return configure._config_home(self.env, self.home, self.platform)

    def install_desktop(self):
        os.makedirs(os.path.join(self.base(), "Claude"), exist_ok=True)
        return os.path.join(self.base(), "Claude", "claude_desktop_config.json")

    def install_vscode(self):
        os.makedirs(os.path.join(self.base(), "Code", "User"), exist_ok=True)
        return os.path.join(self.base(), "Code", "User", "mcp.json")

    def install_claude_code(self):
        self.claude = "/usr/local/bin/claude"

    def run(self, args):
        self.calls.append(args)
        verb = args[2]
        if verb == "get":                      # claude mcp get NAME
            code = 0 if args[3] in self.known else 1
        elif verb == "add":                    # claude mcp add --scope user NAME -- ...
            self.known.add(args[5])
            code = 0
        else:                                  # claude mcp remove --scope user NAME
            self.known.discard(args[5])
            code = 0
        return subprocess.CompletedProcess(args, code, "", "")

    def setup(self, *argv):
        out = io.StringIO()
        code = configure.main(list(argv), env=self.env, home=self.home,
                              platform=self.platform,
                              which=lambda cmd: self.claude if cmd == "claude" else None,
                              run=self.run, check=self.check, out=out)
        return code, out.getvalue()


def _json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ------------------------------------------------------------ the entry itself
def test_entry_runs_this_python_on_the_absolute_path(tmp_path):
    db = _database(tmp_path / "app.sqlite")
    entry = configure.server_command(db, executable="/opt/py/bin/python3", prefix="/opt/py")
    assert entry == {"command": "/opt/py/bin/python3",
                     "args": ["-m", "sqling_mcp.server", os.path.abspath(db)]}


@pytest.mark.parametrize("prefix", [
    "/home/u/.cache/uv/archive-v0/AbC123",
    r"C:\Users\u\AppData\Local\uv\cache\archive-v0\AbC123",
])
def test_under_uvx_the_entry_runs_uvx_not_its_cache(tmp_path, prefix):
    entry = configure.server_command("/x/app.sqlite", executable="/c/python", prefix=prefix)
    assert entry["command"] == "uvx"
    assert entry["args"][0] == "sqling-mcp"


@pytest.mark.parametrize("path,name", [
    ("/x/billing-forms.sqlite", "billing-forms"),
    (r"C:\sqling\My App.sqlite", "my-app"),
    ("/x/forms.sqlite", "forms"),
    ("/x/!!!.sqlite", "forms"),
])
def test_default_name_is_a_name_every_client_accepts(path, name):
    if "\\" in path and os.sep != "\\":
        path = path.replace("\\", "/")
    assert configure.default_name(path) == name
    assert configure.valid_name(name)


# ------------------------------------------------------------ finding the clients
def test_sets_up_every_client_it_finds_and_only_those(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    vscode_dir = os.path.join(m.base(), "Code", "User")
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 0, out
    assert "app" in _json(desktop)["mcpServers"]
    assert not os.path.exists(vscode_dir)          # not installed, not created
    assert m.calls == []                           # no claude on PATH
    assert "2 modules -- form: 1, library: 1" in out
    assert "Quit Claude Desktop" in out


def test_nothing_installed_prints_every_entry(tmp_path):
    m = Machine(tmp_path)
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 0
    assert "No AI client found" in out
    assert '"mcpServers"' in out and '"servers"' in out and "claude mcp add" in out


def test_naming_a_client_creates_its_config_even_if_never_run(tmp_path):
    m = Machine(tmp_path)
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db, "--client", "vscode")
    assert code == 0, out
    path = os.path.join(m.base(), "Code", "User", "mcp.json")
    assert _json(path)["servers"]["app"]["type"] == "stdio"


def test_windows_paths_and_the_store_build(tmp_path):
    m = Machine(tmp_path, platform="win32")
    plain = m.install_desktop()
    store = os.path.join(m.env["LOCALAPPDATA"], "Packages", "Claude_pzs8sxrjxfjjc",
                         "LocalCache", "Roaming", "Claude")
    os.makedirs(store)
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 0, out
    for path in (plain, os.path.join(store, "claude_desktop_config.json")):
        assert _json(path)["mcpServers"]["app"]["args"][-1] == os.path.abspath(db)


def test_workspace_goes_to_its_own_vscode_folder(tmp_path):
    m = Machine(tmp_path)
    work = tmp_path / "project"
    work.mkdir()
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db, "--workspace", str(work))
    assert code == 0, out
    assert "app" in _json(str(work / ".vscode" / "mcp.json"))["servers"]


# ------------------------------------------------------------ the user's file is theirs
def test_keeps_other_servers_and_settings_and_backs_up(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    before = {"mcpServers": {"other": {"command": "x"}}, "globalShortcut": "Ctrl+Space"}
    with open(desktop, "w") as handle:
        json.dump(before, handle)
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 0, out
    after = _json(desktop)
    assert after["mcpServers"]["other"] == {"command": "x"}
    assert after["globalShortcut"] == "Ctrl+Space"
    assert _json(desktop + ".bak") == before
    assert not os.path.exists(desktop + ".tmp")


def test_a_file_with_comments_is_left_alone(tmp_path):
    m = Machine(tmp_path)
    vscode = m.install_vscode()
    text = '{\n  // my servers\n  "servers": {}\n}\n'
    with open(vscode, "w") as handle:
        handle.write(text)
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 1
    assert open(vscode).read() == text
    assert "left alone" in out and '"servers"' in out      # and the entry is printed


def test_reads_a_file_notepad_saved_with_a_bom(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    with open(desktop, "w", encoding="utf-8-sig") as handle:
        json.dump({"mcpServers": {}}, handle)
    code, out = m.setup(_database(tmp_path / "app.sqlite"))
    assert code == 0, out
    assert "app" in _json(desktop)["mcpServers"]


def test_same_name_elsewhere_needs_replace(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    (tmp_path / "one").mkdir()
    first = _database(tmp_path / "one" / "app.sqlite")
    second = _database(tmp_path / "two.sqlite")
    assert m.setup(first)[0] == 0
    code, out = m.setup(second, "--name", "app")
    assert code == 1 and "--replace" in out
    assert _json(desktop)["mcpServers"]["app"]["args"][-1] == os.path.abspath(first)
    code, out = m.setup(second, "--name", "app", "--replace")
    assert code == 0, out
    assert _json(desktop)["mcpServers"]["app"]["args"][-1] == os.path.abspath(second)


def test_running_it_twice_changes_nothing(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    db = _database(tmp_path / "app.sqlite")
    m.setup(db)
    stamp = os.stat(desktop).st_mtime_ns
    code, out = m.setup(db)
    assert code == 0 and "unchanged" in out
    assert os.stat(desktop).st_mtime_ns == stamp


def test_dry_run_and_print_write_nothing(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    m.install_claude_code()
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db, "--dry-run")
    assert code == 0 and "would add" in out and "Nothing changed" in out
    code, out = m.setup(db, "--print")
    assert code == 0 and '"mcpServers"' in out
    assert not os.path.exists(desktop)
    assert all(call[2] == "get" for call in m.calls)


# ------------------------------------------------------------ Claude Code
def test_claude_code_is_added_at_user_scope(tmp_path):
    m = Machine(tmp_path)
    m.install_claude_code()
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 0, out
    add = [c for c in m.calls if c[2] == "add"][0]
    assert add[3:7] == ["--scope", "user", "app", "--"]
    assert add[-3:] == ["-m", "sqling_mcp.server", os.path.abspath(db)]


def test_claude_code_existing_name_needs_replace(tmp_path):
    m = Machine(tmp_path)
    m.install_claude_code()
    m.known.add("app")
    db = _database(tmp_path / "app.sqlite")
    code, out = m.setup(db)
    assert code == 1 and "--replace" in out
    code, out = m.setup(db, "--replace")
    assert code == 0, out
    assert [c[2] for c in m.calls][-3:] == ["get", "remove", "add"]


# ------------------------------------------------------------ remove, and bad input
def test_remove_takes_it_out_of_every_client(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    vscode = m.install_vscode()
    m.install_claude_code()
    db = _database(tmp_path / "app.sqlite")
    assert m.setup(db)[0] == 0
    code, out = m.setup("--remove", "app")
    assert code == 0, out
    assert "app" not in _json(desktop)["mcpServers"]
    assert "app" not in _json(vscode)["servers"]
    assert "app" not in m.known
    code, out = m.setup("--remove", "app")
    assert code == 1 and "No client" in out


def test_refuses_a_file_that_is_not_a_metadata_database(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    other = tmp_path / "other.sqlite"
    sqlite3.connect(str(other)).execute("CREATE TABLE t (x)").connection.commit()
    code, out = m.setup(str(other))
    assert code == 2 and "not a Forms or Reports metadata database" in out
    code, out = m.setup(str(tmp_path / "missing.sqlite"))
    assert code == 2 and "no such database" in out
    assert not os.path.exists(desktop)


def test_refuses_a_file_that_is_not_sqlite_at_all(tmp_path):
    m = Machine(tmp_path)
    fmb = tmp_path / "orders.fmb"
    fmb.write_bytes(b"\x00\x01binary Forms module, not a database" * 40)
    code, out = m.setup(str(fmb))
    assert code == 2 and "is not a SQLite database" in out


def test_a_path_with_url_characters_opens(tmp_path):
    folder = tmp_path / "Q&A #2 My App"
    folder.mkdir()
    db = _database(folder / "app.sqlite")
    m = Machine(tmp_path)
    m.install_desktop()
    code, out = m.setup(db)
    assert code == 0, out
    assert "2 modules" in out


def test_refuses_a_bad_name(tmp_path):
    m = Machine(tmp_path)
    code, out = m.setup(_database(tmp_path / "app.sqlite"), "--name", "my app!")
    assert code == 2 and "not a usable name" in out


def test_setup_is_reached_through_the_main_command(tmp_path, monkeypatch):
    from sqling_mcp import server
    seen = {}
    monkeypatch.setattr(configure, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    server.main(["setup", "x.sqlite", "--dry-run"])
    assert seen["argv"] == ["x.sqlite", "--dry-run"]


def test_a_python_that_cannot_start_the_server_changes_nothing(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    m.broken = "/usr/bin/python3 cannot start the server"
    code, out = m.setup(_database(tmp_path / "app.sqlite"))
    assert code == 2 and "Nothing was changed" in out
    assert not os.path.exists(desktop)


def test_the_probe_runs_the_entry_it_would_write(tmp_path):
    """The real probe, against this Python -- which has the package installed
    when the suite runs from a checkout with ``pip install -e .``."""
    entry = configure.server_command(str(tmp_path / "app.sqlite"))
    try:
        import importlib.metadata as md
        md.version("sqling-mcp")
    except Exception:
        pytest.skip("sqling-mcp is not installed into this Python")
    assert configure.probe(entry) is None
    assert "cannot start" in configure.probe(
        {"command": entry["command"], "args": ["-m", "no_such_module_here", "x"]})


# ------------------------------------------------------------------ reports

def _reports_database(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE report_files (report_file_pk INTEGER PRIMARY KEY, file_name TEXT,
            file_kind TEXT, module_name TEXT, module_kind TEXT, module_version TEXT);
        INSERT INTO report_files (file_name, module_name, module_kind)
            VALUES ('MONTHLY.rdf', 'MONTHLY7', 'report');
    """)
    conn.commit()
    conn.close()
    return str(path)


def test_the_entry_carries_the_reports_database(tmp_path):
    entry = configure.server_command("/x/forms.sqlite", executable="/py", prefix="/p",
                                     reports="/x/reports.sqlite")
    assert entry["args"] == ["-m", "sqling_mcp.server", os.path.abspath("/x/forms.sqlite"),
                             "--reports", os.path.abspath("/x/reports.sqlite")]
    only = configure.server_command(None, executable="/py", prefix="/p",
                                    reports="/x/reports.sqlite")
    assert only["args"][2:] == ["--reports", os.path.abspath("/x/reports.sqlite")]


def test_setup_with_forms_and_reports(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    forms = _database(tmp_path / "app.sqlite")
    reports = _reports_database(tmp_path / "app-reports.sqlite")
    code, out = m.setup(forms, "--reports", reports)
    assert code == 0, out
    assert "report: 1" in out and "form: 1" in out
    args = _json(desktop)["mcpServers"]["app"]["args"]
    assert args[-2:] == ["--reports", os.path.abspath(reports)]


def test_setup_with_reports_alone(tmp_path):
    m = Machine(tmp_path)
    desktop = m.install_desktop()
    reports = _reports_database(tmp_path / "billing-reports.sqlite")
    code, out = m.setup("--reports", reports)
    assert code == 0, out
    assert "billing-reports" in _json(desktop)["mcpServers"]


def test_setup_refuses_reports_that_are_not(tmp_path):
    m = Machine(tmp_path)
    m.install_desktop()
    forms = _database(tmp_path / "app.sqlite")
    code, out = m.setup(forms, "--reports", forms)
    assert code == 2 and "not a Reports metadata database" in out


# ------------------------------------------------ reported from a Windows box

def test_a_drive_letters_case_is_not_somewhere_else(tmp_path):
    """Windows paths are one file whatever their case: an entry that says c:\\
    where setup would write C:\\ points here, not somewhere else."""
    place = configure.Place("claude-desktop", "Claude Desktop", str(tmp_path / "c.json"),
                            "mcpServers", "restart")
    written = {"command": r"c:\python312\python.exe",
               "args": ["-m", "sqling_mcp.server", r"c:\sqling\app.sqlite"]}
    with open(place.path, "w") as handle:
        json.dump({"mcpServers": {"app": written}}, handle)
    asked = {"command": r"C:\Python312\python.exe",
             "args": ["-m", "sqling_mcp.server", r"C:\sqling\app.sqlite"]}
    out = configure.add_to_file(place, "app", asked)
    assert out.status == "unchanged", out.detail
    # a different file is still somewhere else
    other = dict(asked, args=["-m", "sqling_mcp.server", r"C:\sqling\other.sqlite"])
    assert configure.add_to_file(place, "app", other).status == "skipped"


def test_claudes_output_is_read_as_utf8(monkeypatch):
    """claude prints a check mark; read in the Windows code page that failed in
    the thread reading the output, and --remove crashed."""
    seen = {}
    def fake(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "\u2714 Connected", "")
    monkeypatch.setattr(configure.subprocess, "run", fake)
    configure._run(["claude", "mcp", "get", "app"])
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


GET_PROJECT = """app:
  Scope: Project config (shared via .mcp.json)
  Status: \u2714 Connected
  Type: stdio

To remove this server, run: claude mcp remove app -s project
"""


def test_remove_uses_and_reports_the_scope_claude_says(tmp_path):
    """--remove said "user scope" and removed from the user scope, for a server
    held at project level."""
    calls = []
    def run(args):
        calls.append(args)
        if args[2] == "get":
            return subprocess.CompletedProcess(args, 0, GET_PROJECT, "")
        return subprocess.CompletedProcess(args, 0, "", "")
    out = configure.claude_code_remove("claude", "app", run=run)
    assert out.status == "removed" and out.detail == "project scope"
    assert calls[-1] == ["claude", "mcp", "remove", "--scope", "project", "app"]


def test_replace_removes_from_the_scope_it_was_in(tmp_path):
    calls = []
    def run(args):
        calls.append(args)
        if args[2] == "get":
            return subprocess.CompletedProcess(args, 0, GET_PROJECT, "")
        return subprocess.CompletedProcess(args, 0, "", "")
    command = {"command": "/py", "args": ["-m", "sqling_mcp.server", "/x/app.sqlite"]}
    out = configure.claude_code_add("claude", "app", command, replace=True, run=run)
    assert out.status == "replaced"
    assert ["claude", "mcp", "remove", "--scope", "project", "app"] in calls


def test_scope_read_from_the_scope_line_alone():
    run = lambda args: subprocess.CompletedProcess(args, 0, "app:\n  Scope: Local config\n", "")
    assert configure._claude_scope("claude", "app", run) == "local"
