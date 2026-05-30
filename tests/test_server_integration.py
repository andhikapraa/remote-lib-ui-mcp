"""End-to-end tests through an in-memory MCP client. Upstream OpenAlex is mocked
with respx; no real network is used."""

from __future__ import annotations

import json

import httpx
import respx
from mcp.shared.memory import create_connected_server_and_client_session as connect


def _payload(call):
    return json.loads(call.content[0].text)


async def test_capabilities_listed():
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        tools = {t.name for t in (await s.list_tools()).tools}
        assert {
            "search",
            "fetch_url",
            "get_download_url",
            "list_resources",
            "set_resource_enabled",
            "get_proxy_url",
            "refresh_catalog",
        } <= tools
        resources = {str(r.uri) for r in (await s.list_resources()).resources}
        assert "catalog://resources" in resources
        templates = {t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates}
        assert "catalog://resources/{resource_id}" in templates
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        assert {"search_by_field", "download_all_pdfs", "search_help"} <= prompts


async def test_catalog_resource_reads():
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        out = json.loads((await s.read_resource("catalog://resources")).contents[0].text)
        assert out["count"] > 20
        entry = json.loads((await s.read_resource("catalog://resources/scopus")).contents[0].text)
        assert entry["name"] == "Scopus"


async def test_fetch_url_blocks_ssrf():
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        out = _payload(
            await s.call_tool("fetch_url", {"resource_or_url": "http://169.254.169.254/"})
        )
        assert out.get("error") == "InvalidTargetError"


@respx.mock
async def test_search_via_openalex():
    sample = {
        "results": [
            {
                "title": "Graphene study",
                "doi": "https://doi.org/10.1/g",
                "publication_year": 2023,
                "authorships": [{"author": {"display_name": "X"}}],
                "primary_location": {
                    "landing_page_url": "https://pub.test/g",
                    "source": {"display_name": "J"},
                },
                "open_access": {"is_oa": False},
            }
        ]
    }
    respx.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(200, json=sample)
    )
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        out = _payload(
            await s.call_tool(
                "search", {"resource": "scopus", "query": "graphene", "year_from": 2020}
            )
        )
        assert out["count"] == 1
        assert out["backend"] == "openalex/general"
        assert out["results"][0]["title"] == "Graphene study"
        # download link routed through the gateway
        assert "remote-lib.ui.ac.id/login" in out["results"][0]["url"]


@respx.mock
async def test_search_openalex_409_returns_actionable_note():
    respx.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(409, json={"error": "key required"})
    )
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        out = _payload(await s.call_tool("search", {"resource": "scopus", "query": "x"}))
        assert out["count"] == 0
        assert "OPENALEX_API_KEY" in (out.get("note") or "")


async def test_disabled_resource_errors():
    from remote_lib_mcp.server import mcp

    async with connect(mcp._mcp_server) as s:
        await s.call_tool("set_resource_enabled", {"resource": "scopus", "enabled": False})
        out = _payload(await s.call_tool("search", {"resource": "scopus", "query": "x"}))
        assert out.get("error") == "ResourceDisabledError"
