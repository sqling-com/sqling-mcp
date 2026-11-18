# Changelog

## 0.2.0 (2026-11-17)

First public release: Forms, Reports and navigation.

### Reports and navigation

- `calls`, `called_by`, `reachable` and `never_called`: what calls what, from the
  calls recorded when the database is built (CALL_FORM, OPEN_FORM, NEW_FORM,
  GO_FORM, RUN_PRODUCT, rp2rro_run_product, SRW.RUN_REPORT, RUN_REPORT_OBJECT and
  a form's menu module), followed to loaded files by file name. A call that
  cannot be followed says why. `describe_form` and `describe_report` carry a
  short summary.
- A file's name passed to a procedure that is not a known navigation call -- an
  application's own report wrapper -- is said as a name, apart from calls:
  `calls` and `called_by` list it as `named_in`, `never_called` lists such files
  as `named_only`, and `reachable(follow_named=true)` follows them.
- `never_called(kind="library")` lists the libraries no form attaches (libraries
  are attached, not called); other kinds than form, menu, report are refused.
- `effective` compares an object with its parent: values that match it are the
  parent's, unchanged (Forms keeps a subclassed trigger's code in the form too),
  and values that differ are overrides.
- A name matching a form and a report, passed to a procedure whose name says
  which it runs (BUILD_REPORT_URL, FIND_CALL_FORM), names that one alone and says
  why (`chosen_by`); otherwise both are kept, each with `also_matches`.
- `setup` on Windows: an entry whose paths differ only in case (c:\ against C:\)
  is the same entry, not "somewhere else"; claude's output is read as UTF-8, so
  `--remove` no longer fails on its check mark; `--remove` and `--replace` use the
  scope Claude Code holds the server in (user, project or local) and say it.
- `where_used("DUAL")` says it is not counted again, under any schema (SYS.DUAL).
- `where_used` reads the table usage recorded for code -- triggers, program
  units, record groups, FROM-clause blocks, report queries and program units --
  beside the blocks' own, as `code_uses`; a form that only updates a table from
  a trigger counts as updating it.

- `--reports DATABASE` opens a reports database beside the forms one, read-only;
  a reports database also opens on its own, and one file may hold both. Form
  and report tables can be joined in `query`. `setup` takes `--reports` too.
- `list_modules` lists reports by file, with their queries, groups, formulas,
  placeholders, program units and parameters.
- `describe_report` gives a report's parameters, queries with their SQL and
  groups, formulas, placeholders, report triggers, and every program unit with
  what it is used as -- found from every column that names one.
- `search`, `read_code` and `list_code` cover report program units and query SQL.
- `describe_form` says which loaded report each of a form's report objects runs,
  by file name; `describe_report` says which forms run it.
- `where_used` counts reports: the tables their queries and program units name,
  with the operation, listed beside the forms; `describe_report` lists a
  report's tables. It no longer fails on a database with reports only.
- `describe_report` lists summaries (what, of which column, per which group) and
  data links (parent group and column to child query), and a parameter's
  validation trigger; a pointer kept in `report_properties`, as that one is, now
  labels its program unit too.
- `read_code` on a report takes the name of the object that uses a program unit:
  `CF_TAX` gives its formula `cf_taxformula`, a field its format trigger, a
  parameter its validation trigger, and says which unit that was.
- Found against a real 557-file application: `describe_form` no longer fails on a
  report object with no file name (it says so instead); `list_modules` counts in
  one pass per table (8.4 s -> 0.15 s); `where_used` keeps schemas apart -- two
  tables of one name in different schemas are listed separately, with which file
  uses which; `effective` no longer counts an object group's pointer to an
  object as a second object of that name.
- `describe_form` says what an RP2RRO report object is: the generic object Oracle's
  Forms Migration Assistant adds, whose file rp2rro_run_product sets on each call.
- `effective` says when an object came in through a subclassed object group
  (`through_object_group`); with formsrt's loader fixed, such an object now
  inherits from the object of its own name in the group's module.
- `where_used("DUAL")` says DUAL is left out of table usage on purpose, rather
  than that the application has no such table.

### Forms

0.1.0 was never released on its own; what it held ships here.

- MCP server over a Forms metadata database, read-only, stdio:
  `list_modules`, `describe_form`, `read_code`, `search`, `where_used`,
  `effective`, `query`, `schema`.
- `effective` and `describe_form` report what an object inherits from an object
  library or property class, and name the libraries that were not loaded.
- `sqling-mcp setup DATABASE` registers a database with Claude Desktop, VS Code
  (GitHub Copilot) and Claude Code; `--remove`, `--dry-run`, `--print`,
  `--replace`, `--workspace`, `--client`, `--name`.
- `sqling-mcp --version`.
- `where_used` finds blocks on a quoted table (`"Order Details"`), on a
  schema-qualified one, and blocks whose DML target is the table.
- A menu's code is read like any other: `search` covers menu item commands and
  menu startup code, `read_code` gives either (`MENU.ITEM`, `STARTUP_CODE`), and
  `describe_form` lists a menu's code.
- `list_code` lists triggers, program units, menu items and startup code across
  the application or in one file, by kind, name (`%` wildcard) or module kind.
- `search` takes `%` and `_` literally.
- A module is identified by its **file** everywhere: every answer names files
  (`forms/ORDERS.fmb`), tools take `file=` rather than `module=`, a file is
  found by its name with or without folder and extension, and a module's own
  name is never a way in -- it is reported as `module_name`, secondary, in
  `list_modules` and `describe_form`. A subclassed object's parent is named by
  the file it was loaded from.
- `where_used` names the table as the application spells it (`"Order Details"`,
  `ORDERS`), says when names that differ only in case both matched, and when
  nothing matches says so and suggests close names.
- `effective("BLOCK.ITEM")` matches only objects inside that container; an LOV
  column or record group column of the same name no longer answers.
