"""EZproxy URL handling.

The portal grants access by redirecting ``/login?url=<TARGET>`` to a
port-rewritten host, e.g. ``https://remote-lib.ui.ac.id:2054/...`` for
ScienceDirect. The assigned port is stable per upstream host, so once we have
opened a resource we cache ``host -> proxied base`` and can rewrite sibling deep
links without another round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx


@dataclass
class ProxySession:
    """Result of opening a resource through the EZproxy gateway."""

    target_url: str
    proxied_url: str  # final, port-rewritten URL on the remote-lib host
    base: str  # scheme://host:port of the proxied resource
    status_code: int
    html: str


def build_login_url(base_url: str, target_url: str) -> str:
    """Construct the portal gateway URL.

    This EZproxy install treats the bare ``url=`` param literally (a
    percent-encoded value is rejected and bounces to ``/menu``), but it accepts
    the standard ``qurl=`` param carrying a fully percent-encoded target. We use
    ``qurl=`` so multi-parameter search URLs survive intact without depending on
    the HTTP client leaving a raw URL untouched.
    """
    return f"{base_url}/login?qurl={quote(target_url, safe='')}"


def proxied_base(url: str | httpx.URL) -> str:
    """Return scheme://host[:port] for a proxied URL."""
    p = urlparse(str(url))
    return f"{p.scheme}://{p.netloc}"


def is_proxied(url: str, base_url: str) -> bool:
    """True if ``url`` already lives on the remote-lib proxy host."""
    host = urlparse(url).hostname or ""
    proxy_host = urlparse(base_url).hostname or "remote-lib.ui.ac.id"
    return host == proxy_host
