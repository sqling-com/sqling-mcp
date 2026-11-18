"""The server end: a real MCP client, over stdio, against a real database.

The tools can all be right and the server still useless -- a tool that never
appears in list_tools, an argument the schema does not describe, an answer
that will not serialise. This starts the server the way a client starts it
and asks it the questions through the protocol.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

pytest_plugins = ()


def _content(result):
    """The JSON a tool answered with, however the SDK wrapped it.

    The structured result is ``structured_content`` in this SDK and
    ``structuredContent`` in others; reading only one of them silently falls
    through to the text block and tests a path the client may never take.
    """
    for attr in ("structured_content", "structuredContent"):
        body = getattr(result, attr, None)
        if body:
            # A tool that returns a plain dict is wrapped by the SDK as
            # {"result": ...} in the structured output, while the text block
            # carries the dict itself. Unwrap, so a test reads what the tool
            # returned either way.
            if isinstance(body, dict) and set(body) == {"result"}:
                return body["result"]
            return body
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("no content in %r" % (result,))


@pytest.fixture
def session(database):
    """A client talking to sqling-mcp as a subprocess, for one test."""
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sqling_mcp.server", database],
        env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src")))

    class Runner:
        def __call__(self, body):
            async def go():
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        return await body(client)
            return anyio.run(go)

    return Runner()


def test_the_six_tools_are_offered(session):
    async def body(client):
        return [t.name for t in (await client.list_tools()).tools]
    names = session(body)
    assert {"list_modules", "describe_form", "read_code", "search", "list_code",
            "calls", "called_by", "reachable", "never_called", "describe_report",
            "where_used", "query", "schema"} <= set(names)


def test_a_tool_answers_through_the_protocol(session):
    async def body(client):
        return await client.call_tool("describe_form", {"file": "NW_ORDERS.fmb"})
    result = session(body)
    assert getattr(result, "structured_content", None), \
        "the tool answered as text only -- a client reading structured output gets nothing"
    out = _content(result)
    assert out["file"] == "NW_ORDERS.fmb"
    assert any(b["block"] == "ORDER_DETAILS" for b in out["blocks"])


def test_a_bad_argument_comes_back_as_an_answer_not_a_crash(session):
    async def body(client):
        return await client.call_tool("describe_form", {"file": "NOT_A_FORM"})
    out = _content(session(body))
    assert "error" in out and "no file called" in out["error"]


def test_search_through_the_protocol(session):
    async def body(client):
        return await client.call_tool("search", {"text": "COMMIT_FORM", "limit": 5})
    out = _content(session(body))
    assert out["found"] >= 1
    assert out["hits"][0]["file"].startswith("NW_")


def test_reports_through_the_protocol(report_databases):
    """--reports opens the reports beside the forms, and describe_report answers."""
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    forms, reports = report_databases
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sqling_mcp.server", forms, "--reports", reports],
        env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src")))

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = [t.name for t in (await client.list_tools()).tools]
                got = await client.call_tool("describe_report", {"file": "monthly_sales"})
                return names, _content(got)
    names, out = anyio.run(go)
    assert "describe_report" in names
    assert out["file"] == "monthly_sales.rdf"
    assert out["run_by"] == [{"form": "launcher.fmb", "report_object": "SALES"}]
