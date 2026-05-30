"""Stealth-browser fallback powered by cloakbrowser.

cloakbrowser is a drop-in Playwright replacement built on a patched Chromium
that passes Cloudflare and other bot walls. We use it only when the plain HTTP
path is blocked or returns a JS-only shell (Scopus, IEEE search, etc.).

Important: we do NOT route cloakbrowser through a residential proxy. The
institutional (campus) IP is supplied upstream by EZproxy itself; cloakbrowser
just has to reach ``remote-lib.ui.ac.id:<port>`` carrying the ``ezproxy``
session cookies and present a believable fingerprint.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .config import Config
from .exceptions import BrowserUnavailableError


class BrowserEngine:
    """Lazily-launched cloakbrowser, reused across calls."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._browser: Any = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        try:
            import cloakbrowser  # noqa: F401
        except Exception:
            return False
        return True

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                import cloakbrowser
            except Exception as exc:  # pragma: no cover - import guard
                raise BrowserUnavailableError(
                    "cloakbrowser is not installed. Install the browser extra:\n"
                    "  uv sync --extra browser\n"
                    "The patched Chromium downloads automatically on first launch."
                ) from exc

            launch_async = getattr(cloakbrowser, "launch_async", None)
            if launch_async is None:
                raise BrowserUnavailableError(
                    "cloakbrowser.launch_async not found; please upgrade cloakbrowser."
                )
            try:
                self._browser = await launch_async(
                    headless=self._cfg.browser_headless,
                    humanize=self._cfg.browser_humanize,
                )
            except Exception as exc:
                raise BrowserUnavailableError(f"Failed to launch cloakbrowser: {exc}") from exc
            return self._browser

    async def render(
        self,
        url: str,
        cookies: list[dict[str, Any]],
        *,
        wait_until: str = "domcontentloaded",
        wait_ms: int | None = None,
        wait_selector: str | None = None,
    ) -> tuple[str, str]:
        """Navigate to ``url`` with ``cookies`` pre-seeded; return (final_url, html).

        Each call runs in its own browser context so concurrent renders cannot
        interleave cookie/navigation state on a shared default context.
        """
        if wait_ms is None:
            wait_ms = self._cfg.browser_wait_ms
        browser = await self._ensure_browser()
        try:
            context = await browser.new_context()
        except Exception:
            # Build without new_context support: fall back to a bare page on the
            # default context (still isolated enough for the single-session model).
            context = None
        page = await (context.new_page() if context is not None else browser.new_page())
        try:
            if cookies:
                try:
                    await page.context.add_cookies(cookies)
                except Exception:
                    pass
            timeout_ms = int(self._cfg.http_timeout * 1000)
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=15000)
                    await asyncio.sleep(1.0)  # settle for XHR-injected rows
                except Exception:
                    # Likely a Cloudflare interstitial captured before it cleared;
                    # give it a beat, reload once, and wait again.
                    await asyncio.sleep(3.0)
                    try:
                        await page.reload(wait_until=wait_until, timeout=timeout_ms)
                        await page.wait_for_selector(wait_selector, timeout=15000)
                        await asyncio.sleep(1.0)
                    except Exception:
                        await asyncio.sleep(wait_ms / 1000)
            elif wait_ms:
                await asyncio.sleep(wait_ms / 1000)
            html = await page.content()
            final_url = page.url
            return final_url, html
        finally:
            try:
                await page.close()
            except Exception:
                pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None


def httpx_cookies_to_playwright(jar, proxy_host: str) -> list[dict[str, Any]]:
    """Convert an httpx cookie jar into Playwright ``add_cookies`` dicts.

    Only cookies belonging to the EZproxy host are exported. The shared httpx
    jar also holds the CAS/SSO session cookies (incl. the high-value
    ticket-granting cookie); those are deliberately withheld from the
    third-party cloakbrowser process. Cookies without an explicit domain are
    skipped rather than coerced onto the proxy host.
    """
    proxy_host = proxy_host.lower()
    out: list[dict[str, Any]] = []
    for c in jar.jar:
        if c.value is None or not c.domain:
            continue
        domain = c.domain.lstrip(".").lower()
        if domain != proxy_host and not domain.endswith("." + proxy_host):
            continue  # drop SSO/CAS and any non-proxy-host cookies
        out.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path or "/",
                "secure": bool(c.secure),
            }
        )
    return out
