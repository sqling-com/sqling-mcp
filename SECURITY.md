# Security

sqling-mcp is meant to be safe to point at an application's code: it opens one
SQLite file read-only, accepts only single `SELECT` statements through `query`,
makes no network connections and writes nothing except, in `sqling-mcp setup`,
the AI client config files it names on screen.

If you find a way around any of that -- a tool that writes, reads a file other
than the database, reaches the network, or a `query` that escapes the `SELECT`
check -- please report it privately rather than in a public issue:

- GitHub: **Security > Report a vulnerability** on this repository, or
- the contact form at https://sqling.com/contact/

Please include the version (`sqling-mcp --version`), your platform, and the
smallest steps that show the problem. You will get an answer within a few days.

## What is out of scope

What an AI client does with a tool's answer -- sending it to its model
provider, keeping it in a conversation history -- belongs to that client and
its provider, not to this server.
