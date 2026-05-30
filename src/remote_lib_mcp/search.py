"""Per-database search adapters with normalized filters.

Each adapter knows how to (1) turn a normalized query+filters into the upstream
database's search URL and (2) parse result rows out of the returned (proxied)
HTML. Coverage is best-effort: even when parsing yields nothing, the adapter
still returns a working, authenticated proxied search URL the caller can open in
a browser, so every resource is at minimum *searchable with filters*.

Many publishers (Emerald, Sage, Taylor & Francis, Wiley, Annual Reviews) run on
Atypon/Literatum and share the ``/action/doSearch`` parameter scheme, so they
reuse a single adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass
class SearchFilters:
    """Normalized, provider-agnostic search filters."""

    year_from: int | None = None
    year_to: int | None = None
    content_type: str | None = None  # provider vocabulary, e.g. "review", "book"
    open_access: bool | None = None
    author: str | None = None
    sort: str | None = None  # "relevance" | "date"

    @classmethod
    def from_dict(cls, d: dict | None) -> SearchFilters:
        d = d or {}

        def _int(v):
            try:
                return int(v) if v is not None and str(v).strip() != "" else None
            except (TypeError, ValueError):
                return None

        def _bool(v):
            if v is None:
                return None
            if isinstance(v, str):
                s = v.strip().lower()
                if s == "":
                    return None
                return s in ("1", "true", "yes", "on")
            return bool(v)

        return cls(
            year_from=_int(d.get("year_from")),
            year_to=_int(d.get("year_to")),
            content_type=(d.get("content_type") or None),
            open_access=_bool(d.get("open_access")),
            author=(d.get("author") or None),
            sort=(d.get("sort") or None),
        )


@dataclass
class SearchResult:
    title: str
    url: str  # proxied landing page
    source: str = ""
    authors: str = ""
    snippet: str = ""
    year: str = ""
    doi: str = ""
    pdf_url: str = ""  # proxied direct PDF, when derivable from the listing
    open_access: bool | None = None

    def to_dict(self) -> dict:
        d = {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "authors": self.authors,
            "snippet": self.snippet,
            "year": self.year,
            "doi": self.doi,
            "pdf_url": self.pdf_url,
        }
        if self.open_access is not None:
            d["open_access"] = self.open_access
        return {k: v for k, v in d.items() if v not in ("", None)}


@dataclass
class SearchPlan:
    target_url: str  # upstream search URL (pre-proxy)
    render: str = "auto"  # "auto" | "http" | "browser"
    reliability: str = "medium"  # informational: high | medium | low
    applied: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    # CSS selector that signals "results have rendered" — used to wait out the
    # XHR that injects results after initial DOM load when rendering in-browser.
    ready_selector: str | None = None


def _abs(base: str, href: str) -> str:
    return urljoin(base + "/", href) if href else ""


# --------------------------------------------------------------------------- #
# Provider adapters
# --------------------------------------------------------------------------- #
class Provider:
    key = "generic"
    reliability = "low"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:  # pragma: no cover
        raise NotImplementedError

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        return _generic_parse(html, base, limit)


def _track(f: SearchFilters, used: set[str]) -> tuple[list[str], list[str]]:
    present = [
        k
        for k, v in (
            ("year_from", f.year_from),
            ("year_to", f.year_to),
            ("content_type", f.content_type),
            ("open_access", f.open_access),
            ("author", f.author),
            ("sort", f.sort),
        )
        if v not in (None, "")
    ]
    applied = [k for k in present if k in used]
    ignored = [k for k in present if k not in used]
    return applied, ignored


# ScienceDirect articleTypes facet expects short codes, not free text.
_SD_TYPES = {
    "review": "REV",
    "review-article": "REV",
    "article": "FLA",
    "research-article": "FLA",
    "research": "FLA",
    "full-length-article": "FLA",
    "short-communication": "SCO",
    "short": "SCO",
    "book-review": "BRV",
    "editorial": "EDI",
    "correspondence": "CRP",
    "case-report": "CRP",
    "data-article": "DTA",
}
_SD_CODES = {"FLA", "REV", "SCO", "BRV", "EDI", "CRP", "ABS", "DTA", "PRP", "ERR"}


class ScienceDirectProvider(Provider):
    key = "sciencedirect"
    reliability = "medium"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"qs={quote_plus(query)}"]
        if f.year_from or f.year_to:
            lo = f.year_from or 1900
            hi = f.year_to or 2100
            params.append(f"date={lo}-{hi}")
            used |= {"year_from", "year_to"}
        if f.author:
            params.append(f"authors={quote_plus(f.author)}")
            used.add("author")
        if f.open_access:
            params.append("accessTypes=openAccess")
            used.add("open_access")
        if f.content_type:
            raw = f.content_type.strip()
            code = _SD_TYPES.get(raw.lower()) or (raw.upper() if raw.upper() in _SD_CODES else None)
            if code:
                params.append(f"articleTypes={code}")
                used.add("content_type")  # unmapped values stay in filters_ignored
        if f.sort == "date":
            params.append("sortBy=date")
            used.add("sort")
        elif f.sort == "relevance":
            params.append("sortBy=relevance")
            used.add("sort")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://www.sciencedirect.com/search?" + "&".join(params),
            render="browser",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="a.result-list-title-link",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        items = soup.select("li.ResultItem") or soup.select(".result-item-content")
        for li in items:
            a = li.select_one("a.result-list-title-link")
            if not a or not a.get("href"):
                continue
            title = " ".join(a.get_text().split())
            pdf = li.select_one("a.download-link[href], a[href*='pdfft']")
            authors = li.select_one(".Authors, ol.Authors, .authors")
            out.append(
                SearchResult(
                    title=title,
                    url=_abs(base, a["href"]),
                    source="ScienceDirect",
                    authors=" ".join(authors.get_text().split()) if authors else "",
                    pdf_url=_abs(base, pdf["href"]) if pdf and pdf.get("href") else "",
                )
            )
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/science/article/")


class SpringerProvider(Provider):
    key = "springerlink"
    reliability = "medium"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"query={quote_plus(query)}"]
        if f.year_from or f.year_to:
            params.append("date=custom")  # current Springer Nature Link scheme
            if f.year_from:
                params.append(f"dateFrom={f.year_from}")
                used.add("year_from")
            if f.year_to:
                params.append(f"dateTo={f.year_to}")
                used.add("year_to")
        if f.content_type:
            params.append(f"facet-content-type={quote_plus(f.content_type.title())}")
            used.add("content_type")
        if f.sort == "date":
            params.append("sortBy=newestFirst")
            used.add("sort")
        elif f.sort == "relevance":
            params.append("sortBy=relevance")
            used.add("sort")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://link.springer.com/search?" + "&".join(params),
            render="auto",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="ol.content-item-list li, [data-test='title-link'], .c-card",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        for li in soup.select("ol.content-item-list li, li.app-card-open, .c-card"):
            a = li.select_one(
                "a[data-test='title-link'], a.title, a[data-track-action='view'], h3 a, a.app-card-open__link"
            )
            if not a:
                continue
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if not title or not href:
                continue
            oa = bool(li.select_one(".open-access, [data-test='open-access']"))
            r = SearchResult(
                title=title, url=_abs(base, href), source="SpringerLink", open_access=oa or None
            )
            out.append(r)
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/article/")


class IEEEProvider(Provider):
    key = "ieee-xplore"
    reliability = "low"  # Angular SPA + REST API; needs browser, fragile

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"queryText={quote_plus(query)}"]
        if f.year_from and f.year_to:
            params.append(f"ranges={f.year_from}_{f.year_to}_Year")
            used |= {"year_from", "year_to"}
        if f.open_access:
            params.append("openAccess=true")
            used.add("open_access")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://ieeexplore.ieee.org/search/searchresult.jsp?" + "&".join(params),
            render="browser",
            reliability="low",
            applied=applied,
            ignored=ignored,
            ready_selector="a[href*='/document/'], .List-results-items",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        for a in soup.select(
            ".List-results-items h3 a, a.fw-bold[href*='/document/'], h2 a[href*='/document/']"
        ):
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if title and href:
                out.append(SearchResult(title=title, url=_abs(base, href), source="IEEE Xplore"))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/document/")


class JSTORProvider(Provider):
    key = "jstor"
    reliability = "low"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"Query={quote_plus(query)}"]
        if f.year_from:
            params.append(f"sd={f.year_from}")
            used.add("year_from")
        if f.year_to:
            params.append(f"ed={f.year_to}")
            used.add("year_to")
        if f.sort == "date":
            params.append("so=new")
            used.add("sort")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://www.jstor.org/action/doBasicSearch?" + "&".join(params),
            render="browser",
            reliability="low",
            applied=applied,
            ignored=ignored,
            ready_selector="a[href*='/stable/']",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        return _generic_parse(html, base, limit, host_hint="/stable/")


class AtyponProvider(Provider):
    """Atypon/Literatum platforms: Emerald, Sage, Taylor & Francis, Wiley, etc.
    All expose ``/action/doSearch`` with a shared parameter scheme."""

    def __init__(self, key: str, search_host: str, source_name: str, reliability: str = "medium"):
        self.key = key
        self._host = search_host.rstrip("/")
        self._source = source_name
        self.reliability = reliability

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"AllField={quote_plus(query)}"]
        if f.author:
            params.append(f"ContribAuthorStored={quote_plus(f.author)}")
            used.add("author")
        if f.year_from:
            params.append(f"AfterYear={f.year_from}")
            used.add("year_from")
        if f.year_to:
            params.append(f"BeforeYear={f.year_to}")
            used.add("year_to")
        if f.open_access:
            params.append("ConceptID=&access=open")  # honored on most Literatum sites
            used.add("open_access")
        if f.sort == "date":
            params.append("sortBy=Earliest_desc")
            used.add("sort")
        applied, ignored = _track(f, used)
        return SearchPlan(
            f"{self._host}/action/doSearch?" + "&".join(params),
            render="auto",
            reliability=self.reliability,
            applied=applied,
            ignored=ignored,
            ready_selector=".search__item, .issue-item, a[href*='/doi/']",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        sels = (
            ".search__item .hlFld-Title a, .issue-item__title a, "
            "a.ref.nowrap, .search-result__body a.hlFld-Title, h2.issue-item__title a"
        )
        for a in soup.select(sels):
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if title and href:
                out.append(SearchResult(title=title, url=_abs(base, href), source=self._source))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/doi/")


class ScopusProvider(Provider):
    key = "scopus"
    reliability = "low"  # Cloudflare-walled + heavy SPA

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"st1={quote_plus(query)}", "sot=b", "sdt=b"]
        if f.year_from:
            params.append(f"yearFrom={f.year_from}")
            used.add("year_from")
        if f.year_to:
            params.append(f"yearTo={f.year_to}")
            used.add("year_to")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://www.scopus.com/results/results.uri?" + "&".join(params),
            render="browser",
            reliability="low",
            applied=applied,
            ignored=ignored,
            ready_selector="a[href*='/record/display'], [data-testid='results-list'], .searchArea",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        for a in soup.select("a[href*='/record/display'], h3 a[href*='eid=']"):
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if title and href:
                out.append(SearchResult(title=title, url=_abs(base, href), source="Scopus"))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/record/display")


class SilverchairProvider(Provider):
    """Silverchair platform (Oxford Academic journals & books)."""

    reliability = "medium"

    def __init__(self, key: str, host: str, section: str, source: str):
        self.key = key
        self._host = host.rstrip("/")
        self._section = section  # "journals" or "books"
        self._source = source

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"q={quote_plus(query)}"]
        # Silverchair date facet (best-effort; honored on Oxford Academic).
        if f.year_from or f.year_to:
            lo, hi = f.year_from or 1900, f.year_to or 2100
            params.append(f"rg_ArticleDate=01/01/{lo} TO 12/31/{hi}")
            if f.year_from:
                used.add("year_from")
            if f.year_to:
                used.add("year_to")
        applied, ignored = _track(f, used)
        return SearchPlan(
            f"{self._host}/{self._section}/search-results?" + "&".join(params),
            render="browser",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="a.at-sr-article-title-link, .al-article-box, a.article-link",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        seen: set[str] = set()
        for a in soup.select("a.at-sr-article-title-link, a.article-link"):
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if not title or not href or "supplementary" in href.lower() or href in seen:
                continue
            seen.add(href)
            out.append(SearchResult(title=title, url=_abs(base, href), source=self._source))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/article")


class CambridgeProvider(Provider):
    key = "cambridge-core"
    reliability = "medium"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        params = [f"q={quote_plus(query)}"]
        if f.year_from:
            params.append(f"filters%5BdateYearRange%5D%5Bfrom%5D={f.year_from}")
            used.add("year_from")
        if f.year_to:
            params.append(f"filters%5BdateYearRange%5D%5Bto%5D={f.year_to}")
            used.add("year_to")
        applied, ignored = _track(f, used)
        return SearchPlan(
            "https://www.cambridge.org/core/search?" + "&".join(params),
            render="browser",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="a.part-link, li.title",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        seen: set[str] = set()
        for a in soup.select("li.title a.part-link, a.part-link, a.url.productParent"):
            title = " ".join(a.get_text().split())
            href = a.get("href", "")
            if not title or not href or href in seen:
                continue
            seen.add(href)
            out.append(SearchResult(title=title, url=_abs(base, href), source="Cambridge Core"))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/core/")


class McGrawHillProvider(Provider):
    """McGraw Hill AccessMedicine network (AccessPharmacy, etc.)."""

    reliability = "medium"

    def __init__(self, key: str, host: str, source: str):
        self.key = key
        self._host = host.rstrip("/")
        self._source = source

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        # Reference content (book chapters); no year facet.
        applied, ignored = _track(f, set())
        return SearchPlan(
            f"{self._host}/searchresults.aspx?q={quote_plus(query)}",
            render="browser",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="a.chapter_link, .result-title",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        seen: set[str] = set()
        for div in soup.select(".result-title"):
            a = div.find("a", href=True)
            if not a:
                continue
            title = " ".join(a.get_text().split())
            href = a["href"]
            if not title or href in seen:
                continue
            seen.add(href)
            out.append(SearchResult(title=title, url=_abs(base, href), source=self._source))
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/content.aspx")


class TaylorFrancisEbooksProvider(Provider):
    key = "taylor-francis-ebooks"
    reliability = "medium"

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        applied, ignored = _track(f, set())
        return SearchPlan(
            f"https://www.taylorfrancis.com/search?key={quote_plus(query)}",
            render="browser",
            reliability="medium",
            applied=applied,
            ignored=ignored,
            ready_selector="div.search-results-product, a.search-flex-container",
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        seen: set[str] = set()
        for div in soup.select("div.search-results-product"):
            a = div.select_one("a.search-flex-container, a[href*='/books/']")
            if not a or not a.get("href"):
                continue
            title = re.sub(r"^(book|chapter)\s+", "", " ".join(a.get_text().split()), flags=re.I)
            href = a["href"]
            if not title or href in seen:
                continue
            seen.add(href)
            out.append(
                SearchResult(title=title, url=_abs(base, href), source="Taylor & Francis eBooks")
            )
            if len(out) >= limit:
                break
        return out or _generic_parse(html, base, limit, host_hint="/books/")


# --------------------------------------------------------------------------- #
# Generic fallback
# --------------------------------------------------------------------------- #
def _generic_parse(
    html: str, base: str, limit: int, host_hint: str | None = None
) -> list[SearchResult]:
    """Harvest plausible result links when no tailored selector matches."""
    soup = BeautifulSoup(html, "lxml")
    out: list[SearchResult] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = " ".join(a.get_text().split())
        if len(title) < 12:
            continue
        low = href.lower()
        looks_articleish = any(
            seg in low
            for seg in (
                "/article",
                "/doi/",
                "/document/",
                "/stable/",
                "/chapter/",
                "/book/",
                "/abs/",
                "/full/",
            )
        )
        if host_hint and host_hint not in low:
            looks_articleish = looks_articleish or False
        if not looks_articleish:
            continue
        full = _abs(base, href)
        if full in seen:
            continue
        seen.add(full)
        out.append(SearchResult(title=title, url=full, source=""))
        if len(out) >= limit:
            break
    return out


class GenericProvider(Provider):
    """Best-effort search for resources without a tailored adapter.

    We don't know the database's search endpoint or filter params, so we hit the
    widely-used ``/search?q=`` convention on the resource's host and harvest
    article-ish links. Structured filters cannot be mapped generically, so they
    are reported in ``filters_ignored``. The caller always also returns a
    working authenticated ``proxied_search_url`` for the full UI.
    """

    reliability = "low"

    def __init__(self, target_url: str, source_name: str) -> None:
        self.key = "generic"
        self._target = target_url
        self._source = source_name

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        p = urlparse(self._target)
        base = f"{p.scheme}://{p.netloc}"
        applied, ignored = _track(f, set())  # nothing structured is applied
        return SearchPlan(
            f"{base}/search?q={quote_plus(query)}",
            render="auto",
            reliability="low",
            applied=applied,
            ignored=ignored,
        )

    def parse(self, html: str, base: str, limit: int) -> list[SearchResult]:
        out = _generic_parse(html, base, limit)
        for r in out:
            r.source = r.source or self._source
        return out


_OPENALEX = "https://api.openalex.org/works"
_OPENALEX_MAILTO = "remote-lib-ui-mcp@ui.ac.id"


class OpenAlexProvider(Provider):
    """Metadata-backed search via the key-less OpenAlex scholarly index, for
    databases whose native search resists URL/DOM scraping (JS SPAs, bot walls,
    session gates). Scoped to the database's publisher when it maps to one;
    otherwise a general scholarly search (for aggregators/indexes like Scopus,
    JSTOR, EBSCOhost, ProQuest). Returns DOIs; downloads route through the
    EZproxy gateway for institutional full text (or the open-access PDF)."""

    external = True  # fetched directly from OpenAlex, not via the proxy
    reliability = "medium"

    def __init__(self, key: str, source_name: str, publisher_id: str | None = None):
        self.key = key
        self._source = source_name
        self._pub = publisher_id

    def build(self, query: str, f: SearchFilters) -> SearchPlan:
        used: set[str] = set()
        filters: list[str] = []
        if self._pub:
            filters.append(f"locations.source.host_organization_lineage:{self._pub}")
        if f.year_from:
            filters.append(f"from_publication_date:{int(f.year_from)}-01-01")
            used.add("year_from")
        if f.year_to:
            filters.append(f"to_publication_date:{int(f.year_to)}-12-31")
            used.add("year_to")
        if f.open_access:
            filters.append("is_oa:true")
            used.add("open_access")
        url = f"{_OPENALEX}?search={quote_plus(query)}"
        if filters:
            url += "&filter=" + ",".join(filters)
        if f.sort == "date":
            url += "&sort=publication_date:desc"
            used.add("sort")
        url += f"&per-page=50&mailto={_OPENALEX_MAILTO}"
        applied, ignored = _track(f, used)
        return SearchPlan(
            url, render="http", reliability="medium", applied=applied, ignored=ignored
        )

    def parse(self, text: str, base: str, limit: int) -> list[SearchResult]:
        try:
            data = json.loads(text)
        except Exception:
            return []
        out: list[SearchResult] = []
        for it in data.get("results", [])[:limit]:
            doi = (it.get("doi") or "").replace("https://doi.org/", "")
            authors = ", ".join(
                a.get("author", {}).get("display_name", "") for a in it.get("authorships", [])[:5]
            )
            loc = it.get("primary_location") or {}
            src = (loc.get("source") or {}).get("display_name", "")
            oa = (it.get("best_oa_location") or {}).get("pdf_url") or ""
            # Prefer the publisher landing page (a host EZproxy proxies) over
            # doi.org (which the gateway may not be configured to proxy).
            landing = loc.get("landing_page_url") or (
                "https://doi.org/" + doi if doi else (it.get("id") or "")
            )
            out.append(
                SearchResult(
                    title=it.get("title") or it.get("display_name") or "(untitled)",
                    url=landing,
                    source=self._source,
                    authors=authors,
                    snippet=src,
                    year=str(it.get("publication_year") or ""),
                    doi=doi,
                    pdf_url=oa,  # proxy-wrapped by the client; falls back to proxied DOI
                    open_access=bool((it.get("open_access") or {}).get("is_oa")),
                )
            )
        return out


# OpenAlex publisher IDs for single-publisher databases (host_organization_lineage).
_OPENALEX_PUBLISHER = {
    "emerald-insight": ("Emerald Insight", "P4310319811"),
    "annual-reviews": ("Annual Reviews", "P4310320373"),
    "apa-psycarticles": ("APA PsycArticles", "P4310320262"),
    "sage-knowledge": ("Sage Knowledge", "P4310320017"),
    "clinicalkey": ("ClinicalKey (Elsevier)", "P4310320990"),
    "clinicalkey-nursing": ("ClinicalKey Nursing (Elsevier)", "P4310320990"),
}
# Aggregators/indexes -> general scholarly search (no publisher scope).
# These index scholarly literature, so OpenAlex is a reasonable proxy.
_OPENALEX_GENERAL = {
    "jstor": "JSTOR",
    "scopus": "Scopus",
    "proquest": "ProQuest",
    "ebscohost": "EBSCOhost",
    "heinonline": "HeinOnline",
}

# Resources where OpenAlex is NOT a faithful stand-in (legal/media/ebook niches
# it doesn't index, or non-database tools). We refuse the OpenAlex backend and
# fallback for these so the tool never returns mismatched rows — they stay
# honestly entry-only (native search + proxied URL).
_NO_OPENALEX = {
    "scival",
    "hukum-online",
    "alexander-street-press",
    "american-library-association-ebooks",
}


_ATYPON = {
    "acm-digital-library": ("https://dl.acm.org", "ACM Digital Library"),
    "emerald-insight": ("https://www.emerald.com", "Emerald Insight"),
    "sage-journals": ("https://journals.sagepub.com", "Sage Journals"),
    "taylor-francis": ("https://www.tandfonline.com", "Taylor & Francis"),
    "wiley-journal-of-finance": ("https://onlinelibrary.wiley.com", "Wiley Online Library"),
    "wiley-strategic-management-journal": (
        "https://sms.onlinelibrary.wiley.com",
        "Wiley Online Library",
    ),
    "annual-reviews": ("https://www.annualreviews.org", "Annual Reviews"),
}

_REGISTRY: dict[str, Provider] = {
    "sciencedirect": ScienceDirectProvider(),
    "springerlink": SpringerProvider(),
    "ieee-xplore": IEEEProvider(),
    "jstor": JSTORProvider(),
    "scopus": ScopusProvider(),
    "oxford-journals": SilverchairProvider(
        "oxford-journals", "https://academic.oup.com", "journals", "Oxford Academic"
    ),
    "oxford-ebook": SilverchairProvider(
        "oxford-ebook", "https://academic.oup.com", "journals", "Oxford Academic"
    ),
    "cambridge-core": CambridgeProvider(),
    "access-pharmacy": McGrawHillProvider(
        "access-pharmacy", "https://accesspharmacy.mhmedical.com", "AccessPharmacy"
    ),
    "taylor-francis-ebooks": TaylorFrancisEbooksProvider(),
    **{k: AtyponProvider(k, host, name) for k, (host, name) in _ATYPON.items()},
    # OpenAlex metadata backend for scrape-resistant databases (overrides any
    # scraper entry above for these slugs — later keys win).
    **{k: OpenAlexProvider(k, name, pid) for k, (name, pid) in _OPENALEX_PUBLISHER.items()},
    **{k: OpenAlexProvider(k, name) for k, name in _OPENALEX_GENERAL.items()},
}

# Publisher IDs for scraper-backed resources, used for an accurate OpenAlex
# fallback when their native scrape returns no rows.
_SCRAPER_PUBLISHER = {
    "sciencedirect": "P4310320990",  # Elsevier
    "springerlink": "P4310319900",  # Springer
    "sage-journals": "P4310320017",  # SAGE
    "taylor-francis": "P4310320547",  # Taylor & Francis
    "taylor-francis-ebooks": "P4310320547",
    "wiley-journal-of-finance": "P4310320595",
    "wiley-strategic-management-journal": "P4310320595",
    "access-pharmacy": "P4310320788",  # McGraw Hill
}


def get_provider(slug: str) -> Provider | None:
    """Return the tailored adapter for a slug, or None (use GenericProvider)."""
    return _REGISTRY.get(slug)


def openalex_for(slug: str, name: str) -> OpenAlexProvider | None:
    """An OpenAlex provider for a resource: publisher-scoped when the publisher
    is known, otherwise a general scholarly search. Returns None for resources
    where OpenAlex is not a faithful stand-in (see ``_NO_OPENALEX``) so the tool
    never returns mismatched rows for them."""
    if slug in _NO_OPENALEX:
        return None
    if slug in _OPENALEX_PUBLISHER:
        _, pid = _OPENALEX_PUBLISHER[slug]
        return OpenAlexProvider(slug, name, pid)
    return OpenAlexProvider(slug, name, _SCRAPER_PUBLISHER.get(slug))


def has_adapter(slug: str) -> bool:
    return slug in _REGISTRY


def supported_slugs() -> list[str]:
    return sorted(_REGISTRY.keys())
