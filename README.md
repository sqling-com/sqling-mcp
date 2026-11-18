# sqling-mcp

Ask an AI assistant about an Oracle Forms and Reports application: which
forms update a table, where a procedure is called, what fires when a button
is pressed, why an item looks the way it does, what a report's formulas do
and which forms run it. **sqling-mcp** is an
[MCP](https://modelcontextprotocol.io) server that answers those questions
from a metadata database built from your `.fmb`, `.mmb`, `.pll`, `.olb` and
`.rdf` files, so the assistant can search the whole application at once
instead of you opening modules one at a time in Form Builder or Reports
Builder.

- **Read-only.** The database is opened read-only, and the tools only read.
- **Local.** The server is a program on your own computer. It makes no
  network connections, and your forms never leave the machine you build the
  database on.
- **No Oracle install.** You don't need Forms Builder, `frmf2xml` or an
  `ORACLE_HOME` to build the database or to run the server.
- **Works with** Claude Desktop, VS Code with GitHub Copilot, Claude Code,
  and any other client that speaks MCP over stdio.

## Getting started

Four steps: install the package, build a database from your modules,
register it with your AI client, and ask.

### 1. Install

sqling-mcp is a Python package. It needs **Python 3.10 or later**.

**Windows**

1. If `python --version` in a Command Prompt doesn't print 3.10 or later,
   install Python from [python.org/downloads](https://www.python.org/downloads/).
   On the installer's first screen, tick **Add python.exe to PATH**.
2. Open a **new** Command Prompt and run:

   ```console
   python -m pip install sqling-mcp
   ```

3. Check it:

   ```console
   sqling-mcp --version
   ```

   If Windows says `sqling-mcp` is not recognised, pip's Scripts folder
   isn't on your PATH. Nothing is wrong with the install: use
   `python -m sqling_mcp.server` wherever this guide says `sqling-mcp`
   (note the **underscore**).

**macOS and Linux**

Use [pipx](https://pipx.pypa.io), which installs command-line tools in a
place of their own (a plain `pip install` is refused by Homebrew's Python
and by most Linux distributions):

```console
brew install pipx          # macOS; on Linux: sudo apt install pipx, or your distribution's package
pipx ensurepath            # once; then open a new terminal
pipx install sqling-mcp
sqling-mcp --version
```

[uv](https://docs.astral.sh/uv/) works too: `uv tool install sqling-mcp`.

**Upgrading** later: `python -m pip install --upgrade sqling-mcp` on Windows,
`pipx upgrade sqling-mcp` elsewhere. On Windows, quit your AI client first
(see [Troubleshooting](#troubleshooting)).

### 2. Build the metadata database

The database is built in your browser, by a free page that reads your
modules on your own computer. Nothing is uploaded, and there is nothing to
install: no Oracle software, no Java.

**Forms, menus and libraries**

1. Open the **Batch Oracle Forms** page:
   [sqling.com/tools/fmb2xml.html](https://sqling.com/tools/fmb2xml.html).
   If your network blocks sqling.com, the same page is at
   [www.ddginc-usa.com/fmb2xml.html](https://www.ddginc-usa.com/fmb2xml.html).
2. Click **Choose folder** and pick the folder that holds the application's
   `.fmb` forms, `.mmb` menus, `.pll` libraries and `.olb` object libraries.
   Leave **Include subfolders** ticked if they're spread over several. Your
   browser asks permission to read the folder; allow it.
3. Under *Produce*, pick **A metadata database**, and leave **plus a
   ready-made `.sqlite`** ticked.
4. Choose where the results go: into the same folder, a separate output
   folder, or a download. (Edge and Chrome offer all three; Firefox and
   Safari download.)
5. Click **Convert files**. The first run takes a little longer while the
   page loads its engine. When the status line says *Done*, you have two
   files:

   | File | What it is |
   |---|---|
   | `forms-metadata.sqlite` | **The database sqling-mcp opens.** |
   | `forms-metadata.sql` | The same contents as a SQL script, to load into another database if you want to. sqling-mcp doesn't use it. |

6. Move `forms-metadata.sqlite` somewhere permanent and name it after the
   application, for example `C:\sqling\billing.sqlite` or
   `~/sqling/billing.sqlite`. The next step registers this path with your
   AI client, so don't leave it in Downloads.

Include **every** `.pll` and `.olb` your forms use, even ones kept in
another folder: copy them in beside the forms before you choose the
folder. Without them, what subclassed objects inherit stays unknown, and
the tools say so (see
[Subclassing](#subclassing)). A module that can't be read is listed with a
warning; the rest of the database is still built.

**Reports** (optional)

The same steps on the **Batch Oracle Reports** page,
[sqling.com/tools/rdf2xml.html](https://sqling.com/tools/rdf2xml.html)
(or [www.ddginc-usa.com/rdf2xml.html](https://www.ddginc-usa.com/rdf2xml.html)):
choose the folder of `.rdf` files, pick **A metadata database**, and keep the
`reports-metadata.sqlite` it writes beside the forms one, for example
`C:\sqling\billing-reports.sqlite`. sqling-mcp opens the two together, so
it can answer which forms run which reports.

**When the modules change,** build again and replace the file. Your AI
client sees the new contents the next time it starts the server.

### 3. Register it with your AI client

```console
sqling-mcp setup C:\sqling\billing.sqlite
```

With reports too:

```console
sqling-mcp setup C:\sqling\billing.sqlite --reports C:\sqling\billing-reports.sqlite
```

`setup` checks that the file is a Forms metadata database and that the server
starts. Then it adds the server to every client it finds on this computer:

| Client | Where it goes |
|---|---|
| Claude Desktop | `claude_desktop_config.json` (`mcpServers`) |
| VS Code (GitHub Copilot) | your user `mcp.json` (`servers`), or `<folder>/.vscode/mcp.json` with `--workspace <folder>` |
| Claude Code | `claude mcp add --scope user` |

It keeps your other servers and settings and writes a `.bak` of each file
first. It won't touch a file it can't parse, such as a `mcp.json` with
comments; it prints the entry for you to paste instead. Run it again any
time; it changes nothing when the entry is already right.

```console
sqling-mcp setup myapp.sqlite --name myapp     # choose the server's name (default: from the file name)
sqling-mcp setup myapp.sqlite --client vscode  # only this client (claude-desktop, vscode, claude-code)
sqling-mcp setup myapp.sqlite --dry-run        # show what would change
sqling-mcp setup myapp.sqlite --print          # print every client's entry, change nothing
sqling-mcp setup myapp.sqlite --replace        # overwrite an entry of the same name
sqling-mcp setup --remove myapp                # take it out again
```

### 4. Restart the client and ask

- **Claude Desktop:** quit it completely (from the tray icon on Windows, or
  the menu bar on a Mac) and open it again. The server's tools appear under
  the tools button in the chat box.
- **VS Code:** open Copilot Chat, switch to **Agent** mode, and turn the
  server on in the tools picker. If it isn't listed, run **MCP: List
  Servers** from the Command Palette. Your organisation's Copilot policy must
  allow MCP servers.
- **Claude Code:** start a new session; `/mcp` lists the server.

Then ask:

- *Which forms update the ORDERS table?*
- *Show me the WHEN-VALIDATE-ITEM trigger on EMP.SAL in the EMPLOYEES form.*
- *Where is `pkg_billing.post_invoice` called?*
- *Which blocks have no data source, and which forms have the most triggers?*

### One database per application

Each application gets its own database and its own server name:

```console
sqling-mcp setup C:\sqling\billing.sqlite
sqling-mcp setup C:\sqling\scheduling.sqlite
```

### Setting it up by hand

`sqling-mcp setup --print <database>` prints the exact entry for each client.
The server is a stdio command:

```console
python -m sqling_mcp.server /path/to/myapp.sqlite
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "myapp": {
      "command": "python",
      "args": ["-m", "sqling_mcp.server", "C:/sqling/myapp.sqlite"]
    }
  }
}
```

VS Code (`mcp.json`):

```json
{
  "servers": {
    "myapp": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "sqling_mcp.server", "C:/sqling/myapp.sqlite"]
    }
  }
}
```

In JSON, write Windows paths with forward slashes or doubled backslashes. If
the client can't find `python`, give the full path to `python.exe`. `setup`
always writes the full path, which is why it's the easier route.

## The tools

| Tool | Answers |
|---|---|
| `list_modules` | every module by its file, with its kind and how many blocks, items, triggers and program units it holds |
| `describe_form(file)` | one module's blocks and their data sources, every trigger and what it fires on, its program units, a menu's code, the tables it uses, and the reports it runs |
| `describe_report(file)` | one report's parameters, queries with their SQL and groups, formulas and placeholders, report triggers, every program unit and what it is used as (a formula, a format trigger, the before-report trigger), and the forms that run it |
| `read_code(file, unit, on=)` | the source of one trigger, program unit, menu item (`ORDERS` or `MAIN_MENU.ORDERS`) or menu startup code (`STARTUP_CODE`). A trigger name repeats across a module, so `on=` picks the object (`TOOLBAR.PB_SAVE`) |
| `search(text, limit=)` | every line of PL/SQL in the application that mentions something -- triggers, program units, menu item commands and menu startup code -- with file, object and line number |
| `list_code(file=, kind=, name=, module_kind=)` | the code without its source, across the application or in one file: every `ON-ERROR`, every menu item, every program unit in the libraries, every report query. `name=` takes `%` as a wildcard |
| `where_used(table)` | which modules read, insert, update or delete a table, which blocks are based on it, and where its name turns up in code |
| `effective(file, object, table=)` | one object as the form runs it: what it sets itself, what it takes from the object library or property class it is subclassed from, and the two merged |
| `calls(file)` | what one file navigates to, call by call -- CALL_FORM, OPEN_FORM, NEW_FORM, GO_FORM, RUN_PRODUCT, rp2rro_run_product, SRW.RUN_REPORT, RUN_REPORT_OBJECT, its menu module -- with where each call is and the file it leads to, or why it can't be followed |
| `called_by(file)` | everything that navigates to one file |
| `reachable(start, max_depth=)` | every file reachable from a starting form, how many steps away and by what path, and what isn't reached |
| `never_called(kind=)` | forms, menus and reports nothing calls: dead code, or entry points |
| `query(sql, limit=)` | your own `SELECT` over the metadata tables, when none of the above fits |
| `schema(table=)` | the tables with row counts, or one table's columns: what `query` has to work with |

`query` is what makes questions about the whole application possible, such
as "every form that updates ORDERS and also calls COMMIT_FORM" or "trigger
counts per module". The assistant writes the SQL; `schema` tells it what's
there.

## Navigation

When the database is built, every call each piece of code makes is recorded
with where it is: in triggers (with the code a subclassed trigger inherits),
program units, menu commands and a menu's startup code, and in reports.
`calls`, `called_by`, `reachable` and `never_called` follow them to the
loaded files, by file name, never by module name.

A call names its target as a file -- `CALL_FORM('E:\APP\ORDERS.fmx')` leads to
`ORDERS.fmb` -- and `RUN_REPORT_OBJECT` is followed through the form's report
object to its `.rdf`. Reports run through Oracle's migration library
(`rp2rro.rp2rro_run_product`) are followed like `RUN_PRODUCT`.

Some calls can't be followed, and each says why: a target the code works out at
run time (`CALL_FORM(v_form)`), a file that isn't loaded, a report object whose
file is set at run time. `never_called` and `called_by` say how many run-time
targets there are, because "nothing calls X" is only as sure as that.

A database built before calls were recorded says so; build it again on the
Batch page to get them.

## Reports

A report's PL/SQL is all program units -- every formula, format trigger and
report trigger is one -- and objects point at the unit they use by name.
`describe_report` reads those pointers and says what each unit is used as, so
`cf_taxformula` is "formula of formula CF_TAX" and `f_totalformattrigger` is
"format trigger of field F_TOTAL". A unit nothing points at is listed too:
other PL/SQL may call it, and `search` will say where.

A form's report objects name the `.rdf` they run. With both databases open,
`describe_form` says which loaded report each one is, by file name, and
`describe_report` says which forms run it. A report object whose file isn't
loaded is listed as such.

Reports open three ways: a forms database with `--reports` beside it, a
reports database on its own, or one file that holds both.

## Subclassing

An object subclassed from an object library sets almost nothing itself. An
alert's title, style and buttons, or an item's font and format mask, all come
from the library. Read on its own, the object's row looks like an object with
nothing on it. The database records where each object comes from, and
`effective` puts the two halves together:

```
effective ORDERS DELETE_CONFIRM
  sets itself: nothing
  from STD_OBJECTS.STD_CONFIRM:
     alert_message    Delete this record?
     alert_style      Caution
     button1_label    Yes
     button2_label    No
```

`describe_form` says which objects are subclassed and from where. When it
reports **unresolved parents**, the library they come from wasn't in the
folder the database was built from. Rebuild with the library included, or
what those objects inherit stays unknown. "Inherited from a library I can't
see" and "inherits nothing" are different answers, and only one of them is
true of the application.

## Privacy and security

- The server reads one SQLite file, opened read-only (`mode=ro` and
  `PRAGMA query_only`). `query` refuses anything but a single `SELECT`: no
  `ATTACH`, no `PRAGMA`, no second statement.
- It makes no network connections and writes no files. Only `setup` writes
  anything, and only to the client config files listed above.
- **What your AI client does with the answers is up to the client.** When the
  assistant calls a tool, the result (form structure, PL/SQL source) goes
  into the conversation, and so to whichever model provider the client uses.
  That's the same as opening the code in an editor with an AI assistant
  turned on. Use a client and provider your organisation has approved for
  that code.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Troubleshooting

- **"is not a Forms or Reports metadata database"**: the file wasn't built by the
  Batch Oracle Forms page with *A metadata database* chosen. Use
  `forms-metadata.sqlite`, not `forms-metadata.sql`; an XML export or some other
  SQLite file won't do either.
- **The client says the server failed to start**: run
  `sqling-mcp setup --print <database>`, copy the command it shows for
  "any other MCP client", and run it in a terminal. It should sit waiting
  quietly; an error there is the reason.
- **VS Code shows no tools**: Copilot Chat must be in Agent mode, and your
  organisation's policy must allow MCP servers in Copilot.
- **`sqling-mcp` is not recognised (Windows)**: pip's Scripts folder isn't
  on your PATH. Run `python -m sqling_mcp.server setup <database>` instead;
  it's the same command. The module name has an **underscore**,
  `sqling_mcp`; only the package you install is spelt `sqling-mcp`.
- **`setup` says your database is an unrecognised argument**: an older
  copy without `setup` is still installed. `python -m sqling_mcp.server
  --version` should print a version; if it errors, reinstall with
  `pip install --force-reinstall sqling-mcp`.
- **Reinstalling fails because a file is in use (Windows)**: your AI client
  is still running the server. Quit Claude Desktop from its tray icon (closing
  the window leaves it running) and close VS Code, then install again.

## License

[MIT](LICENSE). sqling-mcp is part of [SQLing](https://sqling.com), tools for
running and modernizing Oracle Forms and Reports applications.
