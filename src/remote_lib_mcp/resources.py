"""Catalog of e-resources exposed by remote-lib.ui.ac.id.

The default catalog below was scraped from the portal's ``/menu`` page. It can
be refreshed at runtime with :func:`scrape_catalog`, which re-reads ``/menu`` so
the list stays in sync if the library adds or removes databases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Resource:
    """A single subscribed database / e-resource."""

    name: str
    target_url: str
    # Whether the menu link routes through the EZproxy ``/login?url=`` gateway.
    # A few entries (e.g. TRINKA AI) are plain external links.
    proxied: bool = True
    note: str = ""

    @property
    def slug(self) -> str:
        return _slugify(self.name)


def _slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\(.*?\)", "", s)  # drop parenthetical notes
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# Slugs excluded from the active catalog. Royal Society of Chemistry's portal
# link (rsc.org/Publishing/index.asp) is a dead 404; deprecated until/unless a
# working target is provided. Applied to both the default and re-scraped lists.
DEPRECATED_SLUGS = {"royal-society-of-chemistry"}


def _active(resources: list[Resource]) -> list[Resource]:
    return [r for r in resources if r.slug not in DEPRECATED_SLUGS]


# Default catalog (snapshot from /menu). Order matches the portal.
DEFAULT_CATALOG: list[Resource] = [
    Resource("Access Pharmacy", "https://accesspharmacy.mhmedical.com"),
    Resource("APA PsycArticles", "https://proquest.com/psycarticles/index?accountid=17242"),
    Resource("ACM Digital Library", "https://portal.acm.org"),
    Resource("Alexander Street Press", "https://search.alexanderstreet.com/"),
    Resource("American Library Association (ALA) - Ebooks", "https://portal.igpublish.com"),
    Resource("Annual Reviews", "https://annualreviews.org/"),
    Resource("Cambridge Core (eBooks)", "https://www.cambridge.org/core"),
    Resource("ClinicalKey", "https://www.clinicalkey.com"),
    Resource("ClinicalKey Nursing", "https://www.clinicalkey.com/nursing"),
    Resource("Cochrane", "https://www.cochranelibrary.com"),
    Resource("Ebrary", "https://ebookcentral.proquest.com/auth/lib/indonesiau-ebooks/"),
    Resource("EBSCOhost", "https://search.ebscohost.com/login.aspx"),
    Resource("Emerald Insight", "https://www.emerald.com/insight"),
    Resource("HeinOnline (FH-UI)", "https://heinonline.org/HOL/Welcome"),
    Resource("Hukum Online", "https://hukumonline.com"),
    Resource("IEEE Xplore", "https://ieeexplore.ieee.org/Xplore/home.jsp"),
    Resource("JSTOR", "https://www.jstor.org"),
    Resource(
        "Oxford Journals",
        "https://academic.oup.com/",
        note="2024 kebawah dapat diakses (2024 and earlier accessible)",
    ),
    Resource("Oxford Ebook", "https://www.universitypressscholarship.com/"),
    Resource("ProQuest", "https://www.proquest.com/"),
    # DEPRECATED (see DEPRECATED_SLUGS): portal target is a dead 404.
    Resource(
        "Royal Society of Chemistry",
        "https://rsc.org/Publishing/index.asp",
        note="Deprecated: portal upstream returns 404",
    ),
    Resource("Sage Journals", "https://journals.sagepub.com"),
    Resource("Sage Knowledge (eBooks)", "https://sk.sagepub.com/books"),
    Resource("ScienceDirect", "https://www.sciencedirect.com"),
    Resource("SciVal (DRPM)", "https://www.scival.com"),
    Resource(
        "Scopus",
        "https://www.scopus.com/home.url",
        note="Cloudflare-protected; needs browser fallback",
    ),
    Resource("SpringerLink (eBooks)", "https://link.springer.com"),
    Resource("Taylor & Francis", "https://tandfonline.com"),
    Resource(
        "TRINKA AI",
        "https://www.trinka.ai/",
        proxied=False,
        note="Direct external link, not proxied",
    ),
    Resource("Taylor & Francis eBooks", "https://www.taylorfrancis.com"),
    Resource("Wiley Journal of Finance", "https://onlinelibrary.wiley.com/journal/15406261"),
    Resource(
        "Wiley Strategic Management Journal", "https://sms.onlinelibrary.wiley.com/loi/10970266"
    ),
]


def scrape_catalog(menu_html: str, base_url: str) -> list[Resource]:
    """Parse the e-resource list out of a fetched ``/menu`` page.

    Links look like ``/login?url=<TARGET>``; we extract the visible text as the
    resource name and the decoded ``url`` query param as the target. Plain
    external links (no ``/login?url=``) are kept with ``proxied=False``.
    """
    soup = BeautifulSoup(menu_html, "lxml")
    out: list[Resource] = []
    seen: set[str] = set()
    skip_text = {"home", "e-resources", "contact", "logout", "^"}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        name = " ".join(a.get_text().split()).strip()
        if not name or name.lower() in skip_text:
            continue
        absolute = urljoin(base_url + "/", href)
        parsed = urlparse(absolute)
        # Only consider portal links and obvious database links.
        if "/login" in parsed.path and parsed.query:
            qs = parse_qs(parsed.query)
            target = qs.get("url", [None])[0]
            if not target:
                continue
            key = target
            if key in seen:
                continue
            seen.add(key)
            out.append(Resource(name, target, proxied=True))
    return out


class Catalog:
    """In-memory catalog with name/slug lookup and runtime refresh."""

    def __init__(
        self, resources: list[Resource] | None = None, disabled: list[str] | None = None
    ) -> None:
        self._resources = _active(list(resources if resources is not None else DEFAULT_CATALOG))
        # switched-off slugs (separate from hard-deprecated; user-toggleable)
        self._disabled: set[str] = {_slugify(s) for s in (disabled or [])}

    def all(self) -> list[Resource]:
        """All non-deprecated resources (including switched-off ones; check
        :meth:`is_enabled` for status)."""
        return list(self._resources)

    def is_enabled(self, slug: str) -> bool:
        return _slugify(slug) not in self._disabled

    def set_enabled(self, resource: str, enabled: bool) -> Resource | None:
        """Toggle a resource on/off by name or slug. Returns the Resource, or
        None if it is not in the catalog."""
        r = self.find(resource)
        if r is None:
            return None
        if enabled:
            self._disabled.discard(r.slug)
        else:
            self._disabled.add(r.slug)
        return r

    def replace(self, resources: list[Resource]) -> None:
        if resources:
            self._resources = _active(list(resources))

    def find(self, query: str) -> Resource | None:
        """Resolve a resource by exact name, slug, or case-insensitive substring."""
        q = query.strip()
        ql = q.lower()
        qslug = _slugify(q)
        # exact name
        for r in self._resources:
            if r.name.lower() == ql:
                return r
        # exact slug
        for r in self._resources:
            if r.slug == qslug:
                return r
        # substring on name
        matches = [r for r in self._resources if ql in r.name.lower()]
        if len(matches) == 1:
            return matches[0]
        # substring on slug
        smatches = [r for r in self._resources if qslug and qslug in r.slug]
        if len(smatches) == 1:
            return smatches[0]
        # if multiple, prefer the shortest name match (most specific exact-ish)
        if matches:
            return min(matches, key=lambda r: len(r.name))
        return None
