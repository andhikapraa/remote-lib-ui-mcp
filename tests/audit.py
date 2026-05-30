"""Full per-provider audit: access, type (database vs tool), search, download.

Runs sequentially through one authenticated session (gentle on the gateway).
Writes a JSON report to /tmp/remote_lib_audit.json and prints a table.

    uv run python tests/audit.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup

from remote_lib_mcp import auth, fetch, search
from remote_lib_mcp.client import RemoteLibClient
from remote_lib_mcp.config import Config

# Curated nature of each resource (slug -> (kind, category)).
# kind: "database" = searchable content repository; "tool" = utility, not an
# article corpus you search+download from.
KIND = {
    "access-pharmacy": ("database", "medical reference/ebooks"),
    "apa-psycarticles": ("database", "psychology journals"),
    "acm-digital-library": ("database", "CS journals/proceedings"),
    "alexander-street-press": ("database", "streaming media/primary sources"),
    "american-library-association-ebooks": ("database", "ebooks"),
    "annual-reviews": ("database", "review journals"),
    "cambridge-core": ("database", "journals + ebooks"),
    "clinicalkey": ("database", "clinical reference"),
    "clinicalkey-nursing": ("database", "clinical reference"),
    "cochrane": ("database", "systematic reviews"),
    "ebrary": ("database", "ebooks (Ebook Central)"),
    "ebscohost": ("database", "aggregator/discovery"),
    "emerald-insight": ("database", "management/business journals"),
    "heinonline": ("database", "law journals"),
    "hukum-online": ("database", "Indonesian legal database"),
    "ieee-xplore": ("database", "engineering journals/proceedings"),
    "jstor": ("database", "journal archive"),
    "oxford-journals": ("database", "journals (OUP)"),
    "oxford-ebook": ("database", "ebooks"),
    "proquest": ("database", "aggregator/dissertations"),
    "royal-society-of-chemistry": ("database", "chemistry journals"),
    "sage-journals": ("database", "social science journals"),
    "sage-knowledge": ("database", "ebooks/reference"),
    "sciencedirect": ("database", "journals + ebooks (Elsevier)"),
    "scival": ("tool", "research-metrics analytics"),
    "scopus": ("database", "abstract & citation index"),
    "springerlink": ("database", "journals + ebooks"),
    "taylor-francis": ("database", "journals"),
    "trinka-ai": ("tool", "AI writing/grammar assistant"),
    "taylor-francis-ebooks": ("database", "ebooks"),
    "wiley-journal-of-finance": ("database", "single journal"),
    "wiley-strategic-management-journal": ("database", "single journal"),
}

QUERY = "analysis"
PER_STEP = 55  # seconds per search/download step


def title_of(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        t = soup.find("title")
        return " ".join(t.get_text().split())[:60] if t else ""
    except Exception:
        return ""


async def audit_one(c: RemoteLibClient, r) -> dict:
    slug = r.slug
    kind, category = KIND.get(slug, ("database", "?"))
    row = {
        "name": r.name,
        "slug": slug,
        "kind": kind,
        "category": category,
        "proxied": r.proxied,
        "access": "",
        "title": "",
        "adapter": search.has_adapter(slug),
        "search_rows": None,
        "reliability": "",
        "download": "",
    }
    if not r.proxied:
        row["access"] = "direct-link (not proxied)"
        return row
    # access probe (http)
    try:
        sess = await asyncio.wait_for(c.open_resource(r.target_url), PER_STEP)
        row["title"] = title_of(sess.html)
        if auth.is_sso_url(sess.proxied_url, c.cfg):
            row["access"] = "auth-redirect"
        elif fetch.looks_blocked(sess.html, sess.status_code):
            row["access"] = "blocked(CF)->browser"
        elif fetch.looks_like_js_shell(sess.html):
            row["access"] = "js-shell->browser"
        elif sess.status_code == 200 and len(sess.html) > 1500:
            row["access"] = "ok(http)"
        else:
            row["access"] = f"http {sess.status_code} len={len(sess.html)}"
    except Exception as e:
        row["access"] = f"ERR {type(e).__name__}"
    # search (auto -> browser if needed), tools too (to show they aren't article search)
    try:
        s = await asyncio.wait_for(
            c.search(r.name, QUERY, filters={"year_from": 2018}, limit=3, render="auto"), PER_STEP
        )
        row["search_rows"] = s.get("count")
        row["reliability"] = s.get("reliability", "")
        results = s.get("results", [])
    except Exception as e:
        row["search_rows"] = f"ERR {type(e).__name__}"
        results = []
    # download (only if a row resolved)
    if results:
        try:
            d = await asyncio.wait_for(c.get_download_url(results[0]["url"]), PER_STEP)
            row["download"] = (
                "yes" + ("(derived)" if d.get("download_url_derived") else "")
                if d.get("download_url")
                else "no"
            )
        except Exception as e:
            row["download"] = f"ERR {type(e).__name__}"
    else:
        row["download"] = "n/a (no row)"
    return row


async def main() -> None:
    cfg = Config.from_env()
    c = RemoteLibClient(cfg)
    await c.ensure_login()
    print("LOGIN OK; auditing", len(c.catalog.all()), "resources...\n", flush=True)
    rows = []
    for r in c.catalog.all():
        row = await audit_one(c, r)
        rows.append(row)
        print(
            f"  done: {row['name'][:36]:<38} {row['access']:<22} rows={row['search_rows']} dl={row['download']}",
            flush=True,
        )
    await c.aclose()

    with open("/tmp/remote_lib_audit.json", "w") as fh:
        json.dump(rows, fh, indent=2)

    print("\n" + "=" * 110)
    print(f"{'RESOURCE':<37}{'KIND':<9}{'ACCESS':<22}{'SRCH':<6}{'DL':<14}{'CATEGORY'}")
    print("-" * 110)
    for x in rows:
        print(
            f"{x['name'][:36]:<37}{x['kind']:<9}{x['access']:<22}"
            f"{x['search_rows']!s:<6}{x['download']!s:<14}{x['category']}"
        )
    tools = [x["name"] for x in rows if x["kind"] == "tool"]
    print("\nTOOLS (not article databases):", ", ".join(tools))


if __name__ == "__main__":
    asyncio.run(main())
