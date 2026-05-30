"""CAS single sign-on against sso.ui.ac.id.

The portal redirects anonymous requests to ``sso.ui.ac.id/cas/login``. That page
is a classic CAS webflow form with hidden ``lt`` (login ticket), ``execution``,
and ``_eventId`` fields. Posting the form with valid credentials yields a CAS
service ticket, which the portal exchanges for the ``ezproxy`` session cookies.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import Config
from .exceptions import AuthError, ConfigError


def is_sso_url(url: str | httpx.URL, cfg: Config) -> bool:
    """True when ``url`` points at the CAS SSO host (i.e. we are logged out)."""
    host = urlparse(str(url)).hostname or ""
    sso_host = urlparse(cfg.sso_url).hostname or "sso.ui.ac.id"
    return host == sso_host


def _looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return ('name="password"' in lowered and 'name="lt"' in lowered) or "_eventid" in lowered


def _parse_login_form(html: str, form_url: str) -> tuple[str, dict[str, str]]:
    """Return (post_url, hidden_fields) for the CAS login form."""
    soup = BeautifulSoup(html, "lxml")
    form = None
    for f in soup.find_all("form"):
        if f.find("input", attrs={"name": "password"}):
            form = f
            break
    if form is None:
        raise AuthError("CAS login form not found; the SSO page layout may have changed.")

    action = form.get("action") or form_url
    post_url = urljoin(form_url, action)

    fields: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name or name in ("username", "password"):
            continue
        fields[name] = inp.get("value", "")
    # CAS legacy webflow expects these even if absent from the HTML.
    fields.setdefault("_eventId", "submit")
    fields.setdefault("execution", "e1s1")
    return post_url, fields


async def login(client: httpx.AsyncClient, cfg: Config) -> None:
    """Perform a full CAS login on ``client`` (in place). Idempotent-ish: if the
    session is already valid this completes quickly via the portal menu.

    Raises :class:`ConfigError` if credentials are missing, :class:`AuthError`
    if the login is rejected.
    """
    if not cfg.has_credentials:
        raise ConfigError("Missing credentials. Set REMOTE_LIB_USERNAME and REMOTE_LIB_PASSWORD.")

    # Hitting the portal root bounces us to the CAS login form (or straight to
    # the menu if an existing session is still valid).
    resp = await client.get(cfg.base_url + "/", follow_redirects=True)

    if not is_sso_url(resp.url, cfg):
        # Already authenticated (landed on the portal, not SSO).
        return

    post_url, fields = _parse_login_form(resp.text, str(resp.url))
    fields["username"] = cfg.username or ""
    fields["password"] = cfg.password.get_secret_value() if cfg.password else ""

    resp = await client.post(post_url, data=fields, follow_redirects=True)

    # Success: we are back on the portal and not on a login form.
    if is_sso_url(resp.url, cfg) or _looks_like_login_page(resp.text):
        raise AuthError("CAS login rejected. Check REMOTE_LIB_USERNAME / REMOTE_LIB_PASSWORD.")
