"""Live end-to-end smoke test against remote-lib.ui.ac.id.

Reads credentials from REMOTE_LIB_USERNAME / REMOTE_LIB_PASSWORD. Exercises
login, proxy-URL minting, fetch, filtered search, and download-URL resolution.

    uv run python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from remote_lib_mcp.client import RemoteLibClient
from remote_lib_mcp.config import Config


def show(title: str, obj) -> None:
    print(f"\n=== {title} ===")
    if isinstance(obj, (dict, list)):
        s = json.dumps(obj, indent=2, ensure_ascii=False)
        print(s[:2500])
    else:
        print(str(obj)[:2500])


async def main() -> None:
    cfg = Config.from_env()
    if not cfg.has_credentials:
        print("Set REMOTE_LIB_USERNAME and REMOTE_LIB_PASSWORD first.")
        sys.exit(1)

    c = RemoteLibClient(cfg)
    try:
        await c.ensure_login()
        print("LOGIN OK")

        show(
            "list_resources (first 5)",
            {"count": len(c.catalog.all()), "resources": [r.name for r in c.catalog.all()[:5]]},
        )

        show("get_proxy_url ScienceDirect", await c.get_proxy_url("ScienceDirect"))

        out = await c.fetch_url("JSTOR", render="http", fmt="text")
        out["content"] = (out.get("content") or "")[:300]
        show("fetch_url JSTOR (text, http)", out)

        show(
            "search ScienceDirect (filtered, http)",
            await c.search(
                "ScienceDirect",
                "machine learning",
                filters={"year_from": 2020, "year_to": 2024, "open_access": True},
                limit=5,
                render="http",
            ),
        )

        show(
            "search SpringerLink (filtered, http)",
            await c.search(
                "SpringerLink",
                "graph neural networks",
                filters={"year_from": 2021, "sort": "date"},
                limit=5,
                render="http",
            ),
        )

        # Download-url resolution against a Springer article landing page, if any.
        sr = await c.search("SpringerLink", "transformer architecture", limit=3, render="http")
        if sr["results"]:
            show(
                "get_download_url (first Springer result)",
                await c.get_download_url(sr["results"][0]["url"]),
            )
    finally:
        await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
