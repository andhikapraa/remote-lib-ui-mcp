"""High-level client orchestrating auth, proxy, fetch, browser, and search."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from cachetools import TTLCache

from . import auth, fetch, proxy, search
from .browser import BrowserEngine, httpx_cookies_to_playwright
from .config import Config
from .exceptions import (
    AuthError,
    BlockedError,
    InvalidTargetError,
    ResourceDisabledError,
    ResourceNotFoundError,
)
from .logging_config import get_logger
from .resources import Catalog, Resource, scrape_catalog
from .tls import build_ssl_context

log = get_logger("client")
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}
_verify_warning_emitted = False

# Auth cookies to preserve when pruning publisher-cookie bloat. Because EZproxy
# is port-based, every database's cookies share the remote-lib domain and pile
# up in one jar, eventually overflowing strict upstreams' header limits.
_AUTH_COOKIE_NAMES = {"ezproxy", "ezproxyl", "ezproxyn", "castgc", "jsessionid", "casprivacy"}


def _is_cookie_too_large(resp: httpx.Response) -> bool:
    if resp.status_code != 400:
        return False
    try:
        body = resp.text.lower()
    except Exception:
        return False
    return "too large" in body and ("cookie" in body or "header" in body)


def _validate_public_target(url: str) -> None:
    """Reject raw target URLs that could turn the institutional proxy into an
    SSRF tool: non-http(s) schemes and internal/loopback/private/link-local
    hosts. Catalog resources are trusted and skip this check."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise InvalidTargetError(
            f"Unsupported URL scheme '{p.scheme}'; only http/https are allowed."
        )
    host = (p.hostname or "").lower()
    if not host:
        raise InvalidTargetError("Target URL has no host.")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".local"):
        raise InvalidTargetError(f"Refusing to proxy internal host '{host}'.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise InvalidTargetError(f"Refusing to proxy non-public IP address '{host}'.")


class RemoteLibClient:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config.from_env()
        self.catalog = Catalog(disabled=list(self.cfg.disabled_resources))
        self._http: httpx.AsyncClient | None = None
        self._browser = BrowserEngine(self.cfg)
        self._login_lock = asyncio.Lock()
        self._catalog_lock = asyncio.Lock()
        self._catalog_cache: TTLCache = TTLCache(maxsize=1, ttl=6 * 3600)
        self._authenticated = False

    # ----- lifecycle ----------------------------------------------------- #
    def _new_http(self) -> httpx.AsyncClient:
        global _verify_warning_emitted
        if self.cfg.verify_ssl:
            verify: Any = build_ssl_context()
        else:
            verify = False
            if not _verify_warning_emitted:
                _verify_warning_emitted = True
                log.warning(
                    "TLS certificate verification is DISABLED (REMOTE_LIB_VERIFY_SSL); "
                    "credentials and session cookies are exposed to network MITM attackers."
                )
        limits = httpx.Limits(
            max_connections=self.cfg.max_connections,
            max_keepalive_connections=self.cfg.max_keepalive_connections,
            keepalive_expiry=30.0,
        )
        transport = httpx.AsyncHTTPTransport(
            retries=2, verify=verify, http2=self.cfg.http2, limits=limits
        )
        client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self.cfg.http_timeout, connect=self.cfg.http_connect_timeout),
            follow_redirects=True,
            headers={"User-Agent": self.cfg.user_agent},
        )
        self._load_session(client)
        return client

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = self._new_http()
        return self._http

    async def aclose(self) -> None:
        """Idempotent: safe to call repeatedly and when nothing was started."""
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        try:
            await self._browser.aclose()
        except Exception:
            pass

    # ----- session persistence ------------------------------------------ #
    def _load_session(self, client: httpx.AsyncClient) -> None:
        path = self.cfg.session_file
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        for c in data.get("cookies", []):
            name, value = c.get("name"), c.get("value")
            if not name or value is None:
                continue  # skip malformed entries without aborting the rest
            try:
                client.cookies.set(name, value, domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                continue

    def _save_session(self) -> None:
        path = self.cfg.session_file
        if not path or self._http is None:
            return
        try:
            cookies = [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self._http.cookies.jar
            ]
            # Open the temp file 0o600 up front so live session tokens are never
            # briefly world-readable (no TOCTOU window before chmod).
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"cookies": cookies}, fh)
            os.replace(tmp, path)
        except Exception:
            pass

    # ----- auth ---------------------------------------------------------- #
    async def ensure_login(self, force: bool = False) -> None:
        if self._authenticated and not force:
            return
        async with self._login_lock:
            if self._authenticated and not force:
                return
            client = await self._client()
            await auth.login(client, self.cfg)
            self._authenticated = True
            self._save_session()

    # ----- proxy --------------------------------------------------------- #
    async def open_resource(self, target_url: str) -> proxy.ProxySession:
        """Open an upstream URL through the gateway, re-authenticating once if
        the EZproxy session has lapsed."""
        await self.ensure_login()
        client = await self._client()
        login_url = proxy.build_login_url(self.cfg.base_url, target_url)
        resp = await client.get(login_url, follow_redirects=True)
        if auth.is_sso_url(resp.url, self.cfg):
            # session expired -> re-login and retry once
            await self.ensure_login(force=True)
            resp = await client.get(login_url, follow_redirects=True)
        if auth.is_sso_url(resp.url, self.cfg):
            raise AuthError(
                "Authenticated, but the resource still redirects to SSO; "
                "access may be denied for this resource or the session is unstable."
            )
        if _is_cookie_too_large(resp):
            # Cookie-header bloat from prior databases -> shed publisher cookies, retry.
            self._prune_publisher_cookies()
            resp = await client.get(login_url, follow_redirects=True)
        return proxy.ProxySession(
            target_url=target_url,
            proxied_url=str(resp.url),
            base=proxy.proxied_base(resp.url),
            status_code=resp.status_code,
            html=resp.text,
        )

    async def _get_proxied(self, proxied_url: str) -> httpx.Response:
        """GET a URL that already lives on the proxy host, re-auth once if needed."""
        await self.ensure_login()
        client = await self._client()
        resp = await client.get(proxied_url, follow_redirects=True)
        if auth.is_sso_url(resp.url, self.cfg):
            await self.ensure_login(force=True)
            resp = await client.get(proxied_url, follow_redirects=True)
        if auth.is_sso_url(resp.url, self.cfg):
            raise AuthError(
                "Session could not be re-established; the request keeps redirecting to SSO."
            )
        if _is_cookie_too_large(resp):
            self._prune_publisher_cookies()
            resp = await client.get(proxied_url, follow_redirects=True)
        return resp

    def _browser_cookies(self) -> list[dict[str, Any]]:
        if self._http is None:
            return []
        proxy_host = urlparse(self.cfg.base_url).hostname or "remote-lib.ui.ac.id"
        return httpx_cookies_to_playwright(self._http.cookies, proxy_host)

    def _prune_publisher_cookies(self) -> None:
        """Drop accumulated per-publisher cookies, keeping only the EZproxy/CAS
        auth set. Recovers from Cookie-header bloat after many databases in one
        long-lived session."""
        if self._http is None:
            return
        targets = [
            (c.name, c.domain, c.path)
            for c in self._http.cookies.jar
            if c.name.lower() not in _AUTH_COOKIE_NAMES
        ]
        for name, domain, path in targets:
            try:
                self._http.cookies.delete(name, domain=domain, path=path)
            except Exception:
                pass

    # ----- catalog ------------------------------------------------------- #
    def resolve(self, resource: str) -> Resource:
        r = self.catalog.find(resource)
        if r is None:
            raise ResourceNotFoundError(
                f"Unknown resource '{resource}'. Use list_resources to see valid names."
            )
        if not self.catalog.is_enabled(r.slug):
            raise ResourceDisabledError(
                f"Resource '{r.name}' is switched off. Re-enable it with "
                f"set_resource_enabled('{r.slug}', true), or remove it from REMOTE_LIB_DISABLED."
            )
        return r

    def set_resource_enabled(self, resource: str, enabled: bool) -> dict:
        r = self.catalog.set_enabled(resource, enabled)
        if r is None:
            raise ResourceNotFoundError(
                f"Unknown resource '{resource}'. Use list_resources to see valid names."
            )
        return {"resource": r.name, "slug": r.slug, "enabled": enabled}

    async def refresh_catalog(self, force: bool = False) -> int:
        """Re-scrape /menu, cached for 6h unless ``force``."""
        async with self._catalog_lock:
            if not force and self._catalog_cache.get("menu"):
                return len(self.catalog.all())
            await self.ensure_login()
            resp = await self._get_proxied(self.cfg.base_url + "/menu")
            scraped = scrape_catalog(resp.text, self.cfg.base_url)
            self.catalog.replace(scraped)
            self._catalog_cache["menu"] = True
            self._save_session()
            return len(self.catalog.all())

    def _resource_dict(self, r: Resource, adapters: set[str]) -> dict:
        return {
            "name": r.name,
            "slug": r.slug,
            "target_url": r.target_url,
            "proxied": r.proxied,
            "enabled": self.catalog.is_enabled(r.slug),
            "has_search_adapter": r.slug in adapters,
            "note": r.note or None,
        }

    def catalog_dict(self) -> dict:
        """Serializable catalog snapshot (for the MCP resource + list tool)."""
        adapters = set(search.supported_slugs())
        items = [self._resource_dict(r, adapters) for r in self.catalog.all()]
        return {
            "count": len(items),
            "enabled": sum(1 for it in items if it["enabled"]),
            "resources": items,
        }

    def get_resource(self, resource_id: str) -> dict | None:
        """Single catalog entry by name or slug (for the resource template)."""
        r = self.catalog.find(resource_id)
        if r is None:
            return None
        return self._resource_dict(r, set(search.supported_slugs()))

    # ----- public operations -------------------------------------------- #
    async def get_proxy_url(self, resource_or_url: str) -> dict:
        target, name, proxied = self._target_of(resource_or_url)
        if not proxied:
            return {
                "resource": name,
                "url": target,
                "proxied": False,
                "note": "Direct external link (not routed through EZproxy).",
            }
        sess = await self.open_resource(target)
        return {"resource": name, "target_url": target, "url": sess.proxied_url, "proxied": True}

    def _target_of(self, resource_or_url: str) -> tuple[str, str, bool]:
        s = resource_or_url.strip()
        if s.lower().startswith(("http://", "https://")):
            # Raw URL from the caller: validate it can't be used to reach
            # internal/loopback hosts through the authenticated proxy.
            _validate_public_target(s)
            return s, s, True
        r = self.resolve(s)
        return r.target_url, r.name, r.proxied

    async def fetch_url(
        self, resource_or_url: str, render: str = "auto", fmt: str = "markdown"
    ) -> dict:
        target, name, proxied = self._target_of(resource_or_url)
        if not proxied:
            return {
                "resource": name,
                "url": target,
                "proxied": False,
                "note": "Not a proxied resource; open the URL directly.",
            }

        if proxy.is_proxied(target, self.cfg.base_url):
            resp = await self._get_proxied(target)
            final_url, html, status = str(resp.url), resp.text, resp.status_code
        else:
            sess = await self.open_resource(target)
            final_url, html, status = sess.proxied_url, sess.html, sess.status_code

        want_browser = render == "browser" or (
            render == "auto"
            and (fetch.looks_blocked(html, status) or fetch.looks_like_js_shell(html))
        )
        used_browser = False
        if want_browser and self._browser.available:
            final_url, html, used_browser = await self._render_with_browser(final_url)
        elif render == "browser":
            raise BlockedError(
                f"{name} needs the browser fallback. Install it: uv sync --extra browser"
            )

        if fetch.looks_blocked(html, status) and not used_browser and not self._browser.available:
            raise BlockedError(
                f"{name} returned a bot wall and the browser fallback is unavailable. "
                "Install the browser extra: uv sync --extra browser"
            )

        body = self._format(html, final_url, fmt)
        download_url = fetch.find_download_url(html, final_url)
        out: dict[str, Any] = {
            "resource": name,
            "url": final_url,
            "format": fmt,
            "rendered_with": "browser" if used_browser else "http",
            "content": body,
        }
        notes = []
        if not (body or "").strip():
            notes.append(
                "No content extracted (page may be JS-rendered or blocked); try render='browser'."
            )
        if fetch.looks_blocked(html, status) and not used_browser:
            notes.append(
                "Upstream served a bot wall; enable the browser extra for reliable access."
            )
        if notes:
            out["note"] = " ".join(notes)
        if download_url:
            out["download_url"] = download_url
        return out

    async def _render_with_browser(
        self, url: str, wait_selector: str | None = None
    ) -> tuple[str, str, bool]:
        final_url, html = await self._browser.render(
            url, self._browser_cookies(), wait_selector=wait_selector
        )
        return final_url, html, True

    def _format(self, html: str, url: str, fmt: str) -> str:
        if fmt == "html":
            return html
        if fmt == "text":
            return fetch.extract_main_text(html)
        if fmt == "links":
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
            from urllib.parse import urljoin

            seen, lines = set(), []
            for a in soup.find_all("a", href=True):
                t = " ".join(a.get_text().split())
                href = urljoin(url, a["href"])
                if href in seen:
                    continue
                seen.add(href)
                lines.append(f"{t or '(no text)'} -> {href}")
            return "\n".join(lines)
        return fetch.html_to_markdown(html)

    async def search(
        self,
        resource: str,
        query: str,
        filters: dict | None = None,
        limit: int = 10,
        render: str = "auto",
    ) -> dict:
        r = self.resolve(resource)
        provider = search.get_provider(r.slug) or search.GenericProvider(r.target_url, r.name)
        flt = search.SearchFilters.from_dict(filters)
        plan = provider.build(query, flt)

        if getattr(provider, "external", False):
            return await self._search_external(r, query, provider, plan, limit)

        sess = await self.open_resource(plan.target_url)
        final_url, html, status = sess.proxied_url, sess.html, sess.status_code

        want_browser = render == "browser" or (
            render == "auto"
            and (
                plan.render == "browser"
                or fetch.looks_blocked(html, status)
                or fetch.looks_like_js_shell(html)
            )
        )
        used_browser = False
        if want_browser and self._browser.available:
            final_url, html, used_browser = await self._render_with_browser(
                final_url, plan.ready_selector
            )
        elif want_browser and render == "browser":
            raise BlockedError(
                f"{r.name} search needs the browser fallback. Install it: uv sync --extra browser"
            )

        parsed = provider.parse(html, sess.base, limit)
        seen: set[str] = set()
        results = []
        for res in parsed:
            if res.url in seen:
                continue
            seen.add(res.url)
            if not res.source:
                res.source = r.name
            results.append(res)

        # If the native scrape yielded nothing, fall back to the OpenAlex
        # metadata index so the resource still returns rows + filters + downloads
        # — but only where OpenAlex is a faithful stand-in (openalex_for returns
        # None for legal/media/ebook niches and non-database tools).
        if not results:
            oa = search.openalex_for(r.slug, r.name)
            if oa is not None:
                oa_out = await self._search_external(r, query, oa, oa.build(query, flt), limit)
                if oa_out.get("count"):
                    oa_out["note"] = (
                        f"Native search of {r.name} returned no parsable rows; "
                        "fell back to the OpenAlex scholarly index. " + (oa_out.get("note") or "")
                    )
                    oa_out["proxied_search_url"] = final_url
                    return oa_out

        note_parts = []
        if not search.has_adapter(r.slug):
            note_parts.append(
                "No tailored adapter for this resource; results are generic best-effort. Open proxied_search_url in a browser for the full UI."
            )
        if not results:
            note_parts.append(
                "No results parsed (page may be JS-rendered or blocked). Try render='browser' or open proxied_search_url."
            )
        if fetch.looks_blocked(html, status) and not used_browser:
            note_parts.append(
                "Upstream served a bot wall; install the browser extra for reliable access."
            )

        return {
            "resource": r.name,
            "query": query,
            "proxied_search_url": final_url,
            "rendered_with": "browser" if used_browser else "http",
            "reliability": plan.reliability,
            "filters_applied": plan.applied,
            "filters_ignored": plan.ignored,
            "count": len(results),
            "results": [res.to_dict() for res in results],
            "note": " ".join(note_parts) or None,
        }

    async def _search_external(self, r, query, provider, plan, limit: int) -> dict:
        """Metadata-API search (OpenAlex) for scrape-resistant databases. Fetches
        the public API directly (no proxy), then routes each result's full-text /
        download link through the gateway for institutional access."""
        client = await self._client()
        url = plan.target_url
        key = self.cfg.openalex_api_key.get_secret_value() if self.cfg.openalex_api_key else None
        if key:
            url += ("&" if "?" in url else "?") + "api_key=" + key
        parsed = []
        api_note: str | None = None
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                parsed = provider.parse(resp.text, "", limit)
            elif resp.status_code in (401, 403):
                api_note = (
                    f"OpenAlex rejected the request (HTTP {resp.status_code}); "
                    "set a valid REMOTE_LIB_OPENALEX_API_KEY."
                )
            elif resp.status_code == 409:
                api_note = (
                    "OpenAlex requires an API key (HTTP 409); set REMOTE_LIB_OPENALEX_API_KEY."
                )
            elif resp.status_code == 429:
                api_note = (
                    "OpenAlex rate limit hit (HTTP 429); retry later or set "
                    "REMOTE_LIB_OPENALEX_API_KEY for a higher quota."
                )
            else:
                api_note = f"OpenAlex returned HTTP {resp.status_code}."
            if api_note:
                log.warning("OpenAlex backend: %s", api_note)
        except Exception as exc:
            api_note = f"OpenAlex request failed: {type(exc).__name__}."
            log.warning("OpenAlex fetch error: %s", type(exc).__name__)
        seen: set[str] = set()
        results = []
        for res in parsed:
            # Route the publisher landing page (and OA PDF) through the gateway
            # so the link carries institutional access.
            if res.url.startswith(("http://", "https://")):
                res.url = proxy.build_login_url(self.cfg.base_url, res.url)
            res.pdf_url = (
                proxy.build_login_url(self.cfg.base_url, res.pdf_url) if res.pdf_url else res.url
            )
            if not res.url or res.url in seen:
                continue
            seen.add(res.url)
            results.append(res)
        scoped = getattr(provider, "_pub", None)
        note = (
            "Results from the OpenAlex scholarly index"
            + (
                f", scoped to {r.name}'s publisher"
                if scoped
                else " (general scholarly search; this resource is an aggregator/index)"
            )
            + ". Each result's url/pdf_url routes through the EZproxy gateway for "
            "institutional full text (or the open-access PDF when available)."
        )
        if api_note:
            note = api_note + " " + note
        return {
            "resource": r.name,
            "query": query,
            "backend": "openalex" + ("/publisher" if scoped else "/general"),
            "rendered_with": "metadata-api",
            "reliability": plan.reliability,
            "filters_applied": plan.applied,
            "filters_ignored": plan.ignored,
            "count": len(results),
            "results": [x.to_dict() for x in results],
            "note": note,
        }

    async def get_download_url(self, url: str) -> dict:
        """Resolve the full-text / PDF download URL for an article page."""
        target, _, proxied = self._target_of(url)
        if not proxied:
            return {"url": target, "proxied": False, "note": "Not a proxied resource."}

        if proxy.is_proxied(target, self.cfg.base_url):
            resp = await self._get_proxied(target)
            final_url, html, status = str(resp.url), resp.text, resp.status_code
        else:
            sess = await self.open_resource(target)
            final_url, html, status = sess.proxied_url, sess.html, sess.status_code

        used_browser = False
        if fetch.looks_blocked(html, status) or fetch.looks_like_js_shell(html):
            if self._browser.available:
                final_url, html, used_browser = await self._render_with_browser(final_url)

        download_url = fetch.find_download_url(html, final_url)
        meta = fetch.collect_pdf_meta(html, final_url)
        if not download_url and meta.get("pdf_url"):
            download_url = meta["pdf_url"]
        derived = False
        if not download_url:
            download_url = fetch.derive_pdf_url(final_url)
            derived = bool(download_url)

        out: dict[str, Any] = {
            "page_url": final_url,
            "rendered_with": "browser" if used_browser else "http",
            "download_url": download_url,
            "download_url_derived": derived or None,
            "metadata": meta or None,
        }
        if not download_url:
            if fetch.looks_blocked(html, status) and not used_browser:
                out["note"] = (
                    "The article page was blocked by a bot wall and no browser fallback ran; "
                    "enable the browser extra (uv sync --extra browser) and retry."
                )
            else:
                out["note"] = (
                    "No citation_pdf_url or PDF link found. The PDF may be gated behind JS; "
                    "try opening page_url in a browser, or the item may not be full-text accessible."
                )
        return out
