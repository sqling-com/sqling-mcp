# Contributing

Issues and pull requests are welcome. A few things make them easier to take:

- **A question the tools cannot answer** is the most useful kind of issue. Say
  what you asked, what came back, and -- if you can share it -- the smallest
  form that shows it. Please don't attach a customer's application.
- **Keep the server read-only and dependency-free.** It depends on the standard
  library's `sqlite3` and the MCP SDK and nothing else; a change that needs
  more should say why in the pull request.
- **Answers are data, not prose.** Tools return dicts the client can read; a
  `CatalogError` becomes `{"error": ...}` rather than a traceback.

## Running the tests

```console
python -m pip install -e . pytest
python -m pytest tests -q
```

The `setup` tests run anywhere: they build a pretend home folder and a
synthetic database, and never touch a real client's configuration.

The tool tests read fixture databases (`tests/northwind.sqlite`,
`tests/subclassed.sqlite` and a few more beside them). They are built from Forms modules by SQLing's
loader and are not in the repository; without them those tests skip rather
than pass. A database built with the Batch Oracle Forms page works too: put it
at `tests/northwind.sqlite` to run the tool tests against it, though the
assertions expect the Northwind sample.

## Layout

| File | |
|---|---|
| `src/sqling_mcp/db.py` | `Catalog`: the database, opened read-only |
| `src/sqling_mcp/tools.py` | every tool as a plain function over a `Catalog`, testable without MCP |
| `src/sqling_mcp/navigation.py` | what calls what: `calls`, `called_by`, `reachable`, `never_called` |
| `src/sqling_mcp/server.py` | MCP wiring only |
| `src/sqling_mcp/configure.py` | `sqling-mcp setup` |
