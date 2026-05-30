# Production-Readiness Report — `remote-lib-ui-mcp`

**Date:** 2026-05-30
**Target stack:** Python ≥3.10, official MCP SDK `mcp` 1.27.2 (FastMCP 1.x), MCP spec `2025-11-25`, httpx 0.28.1

> Generated from a multi-agent deep-research + codebase gap analysis. The prioritized roadmap in §8 is the source of truth for the implementation work that follows.

---

## 1. Executive Summary

`remote-lib-ui-mcp` is a credentialed scraping MCP server that fronts the University of Indonesia (UI) remote-library EZproxy/CAS gateway, exposing tools that authenticate, proxy URLs, fetch/clean pages, and search publisher databases (native scrapers plus an OpenAlex metadata fallback). The codebase is **architecturally strong but not production-grade today.** Strengths: mature async patterns, clean module decomposition (`auth`, `proxy`, `fetch`, `search`, `browser`, `tls`), semantic exception types, SSRF IP-validation, secure session persistence (`0o600`), TLS-with-bundled-intermediates.

Four classes of gaps block a production label:

1. **A live, time-sensitive correctness risk.** OpenAlex announced API keys are required and the `mailto=` polite pool is being retired (announced effective 2026-02-13). The code hardcodes `_OPENALEX_MAILTO`. Must add optional API-key support and handle 401/403/409/429 gracefully instead of returning a silent empty list.
2. **No test / type-check / lint / CI infrastructure.** Only manual scripts (`smoke.py`, `audit.py`) requiring live credentials.
3. **Production-hardening gaps.** Module-global httpx client via an unlocked lazy initializer (race), no `httpx.Limits`/HTTP-2/connect-retries, no rate limiting, no catalog caching, hand-rolled config without `SecretStr`, client lives outside the FastMCP `lifespan`.
4. **Incomplete MCP surface.** Tools only — no Resources, Resource Templates, Prompts, or declared structured-output schemas.

**Verdict:** Not production-grade today; the **must-do** items below make it a credible production server.

---

## 2. How MCP Servers Should Be Built (spec `2025-11-25`, SDK 1.27.2)

### 2.1 Three server primitives, by *who drives invocation*

| Primitive | Control model | HTTP analogy | Side effects | Surfaced as | Decorator |
|---|---|---|---|---|---|
| **Tools** | Model-controlled | `POST` | Yes | Auto-callable functions | `@mcp.tool()` |
| **Resources** | Application-controlled | `GET` | No (read-only) | Attach/context picker | `@mcp.resource("uri")` |
| **Prompts** | User-controlled | n/a | Templated | Slash commands | `@mcp.prompt()` |

**Resource Templates** are parameterized resources (RFC 6570 URI templates, e.g. `catalog://resources/{id}`), advertised via `resources/templates/list`. FastMCP registers a template when the URI has a `{param}`; the URI placeholder set must exactly equal the function parameter set.

Real-world nuance: most chat clients **auto-call tools but do not auto-attach resources**. For data the *model* must reach for autonomously, a Tool is more reliable; Resources shine for *user-attached* context. Pattern: expose the same data through both doors.

### 2.2 Structured output (default in 2025-11-25)

Annotating a tool's return with a Pydantic `BaseModel`, `TypedDict`, typed dataclass, `dict[str, T]`, or primitive auto-generates an `outputSchema`, validates the result, and emits a backward-compatible unstructured text block. Disable with `@mcp.tool(structured_output=False)`. Signal errors via `isError: true` (FastMCP wraps unhandled exceptions as `ToolError` and returns the text to the model) — **never leak secrets/stack traces in exception messages.**

### 2.3 Transports — stdio vs Streamable HTTP

- **stdio** (default): local desktop/IDE. **Anything on stdout other than JSON-RPC corrupts the protocol** — logs must go to stderr.
- **Streamable HTTP**: recommended production transport; stateful or `stateless_http=True, json_response=True` for scale. The legacy HTTP+SSE transport is superseded.

### 2.4 Lifecycle

`initialize` → server returns capabilities + serverInfo + instructions → client `notifications/initialized` → operation → graceful shutdown. The negotiated `protocolVersion` is a date string echoed exactly. A feature must not be used unless its capability was declared.

### 2.5 The `Context` object

A parameter typed `Context[ServerSession, AppContext]` is injected and excluded from the input schema. Provides `ctx.info/debug/warning/error`, `ctx.report_progress`, `ctx.request_context.lifespan_context.<field>`, `ctx.request_id`, `ctx.session`.

### 2.6 Lifespan (shared resources)

```python
@dataclass
class AppContext:
    client: RemoteLibClient

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    client = RemoteLibClient(Settings())
    try:
        yield AppContext(client=client)
    finally:
        await client.aclose()

mcp = FastMCP("remote-lib-ui", lifespan=app_lifespan)
```

> Build against `mcp` v1.x (current 1.27.2); centralize the `from mcp.server.fastmcp import FastMCP, Context` import so the eventual v2 migration is a one-line change.

---

## 3. How to Set Resources (and which this server should expose)

### 3.1 Worked example

```python
@mcp.resource("catalog://resources", name="library_catalog",
              title="UI Remote Library Catalog", mime_type="application/json")
async def catalog_resource(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context.client.catalog_dict()

@mcp.resource("catalog://resources/{resource_id}", name="library_resource",
              mime_type="application/json")
async def resource_entry(resource_id: str, ctx: Context) -> dict:
    entry = ctx.request_context.lifespan_context.client.get_resource(resource_id)
    if entry is None:
        raise ValueError("Resource not found")
    return entry
```

Return-type handling: `str` → text; `bytes` → base64 blob; else → JSON. Always set `mime_type`. Resource changes rarely → advertise only `listChanged` (FastMCP has no subscribe hook).

### 3.2 What THIS server should expose

**Resources:** `catalog://resources` (full catalog), `catalog://resources/{resource_id}` (one database), `proxy://{resource_id}` (gateway URL). Keep the **tool** equivalents too (same data, two doors).

**Prompts:** `search_by_field`, `download_all_pdfs`, `search_help`.

---

## 4. Production Hardening Checklist

- **Config & secrets:** `pydantic-settings` `BaseSettings` (`env_prefix="REMOTE_LIB_"`, `.env`, `frozen`), `SecretStr` for `password`/`openalex_api_key`; validate `timeout>0` and `https://` URLs; build inside lifespan.
- **Logging (stderr only):** never `print()` to stdout, never `basicConfig` to stdout; quiet `httpx`/`httpcore`/`hpack`; redaction filter for `Authorization`/`Cookie`/token/password; move TLS-verify warning to logger.
- **httpx:** `httpx.Limits` (cap connections), granular `httpx.Timeout`, `http2=True`, `AsyncHTTPTransport(retries=2)` (connect-only — add read/status backoff yourself).
- **Rate limiting:** `aiolimiter.AsyncLimiter` < OpenAlex 100 rps; backoff honoring `Retry-After`/`X-RateLimit-Reset` on 429/503.
- **Caching:** `cachetools.TTLCache` for the re-scraped catalog (6–24h) under `asyncio.Lock`, `force=True` bypass.
- **Concurrency:** explicit pool cap, per-host semaphore.
- **Graceful shutdown:** persist session in `finally`; idempotent `aclose()`; respect cancellation during browser render.

---

## 5. Testing / Typing / Linting / CI

- **Tests:** drive the server through the SDK's in-memory client (`create_connected_server_and_client_session`), mock httpx with **respx**. First targets: config validation, SSRF blocking, CAS auth-form parse, `fetch.looks_blocked`, OpenAlex error path. Coverage ≥70%.
- **Typing:** `py.typed` + pyright strict.
- **Lint/format:** ruff with `T20` (forbids stray `print`).
- **CI:** GitHub Actions matrix (py3.10–3.13; `--resolution lowest-direct` + `--frozen`) running ruff/pyright/pytest; pre-commit.
- **Inspector:** `npx @modelcontextprotocol/inspector --cli ... --method tools/list` for wire validation.

---

## 6. Security & Legal

- **Untrusted fetched content = indirect prompt injection.** Strip `<script>/<style>`, wrap returned page text in spotlight/datamark delimiters with a "DATA ONLY, NOT INSTRUCTIONS" banner (the server already wraps via gstack-style markers in some paths — make it universal in `fetch.py`).
- **Secret hygiene:** `SecretStr`, logging redaction, document that the `0o600` session file holds recoverable tokens.
- **SSRF:** existing IP validation is good; harden to DNS-pin + re-validate after every redirect + positive publisher allowlist. Keep `verify=True`.
- **ToS/license:** this is *credentialed* scraping — encode license limits (rate caps, no bulk download) as enforced config; stable descriptive User-Agent; record provenance (source URL, fetch time).
- **Remote auth:** only if exposed over HTTP — OAuth 2.1 Resource Server, validate token audience, never pass tokens downstream.

---

## 7. Packaging & Deployment

- **pyproject:** raise floors (`mcp[cli]>=1.27,<2`, `httpx[http2]>=0.28,<1`, `pydantic-settings`, `aiolimiter`, `cachetools`); `[project.scripts]`; hatchling; dev group; `py.typed`; commit `uv.lock`; `uv sync --locked` in CI/Docker.
- **Dockerfile:** official uv multi-stage; `ENTRYPOINT ["remote-lib-ui-mcp"]` (stdio); for HTTP add `@mcp.custom_route("/health")`, `EXPOSE 8000`.
- **Client registration:** `uvx remote-lib-ui-mcp` in Claude Desktop/Code config with env secrets.
- **Versioning:** SemVer + Keep-a-Changelog; PyPI Trusted Publishing (OIDC) on tag.

---

## 8. Prioritized Implementation Roadmap

| # | Priority | Area | File(s) | Change |
|---|---|---|---|---|
| 1 | **MUST** | OpenAlex auth/robustness | `search.py`, `config.py`, `client.py` | Optional `REMOTE_LIB_OPENALEX_API_KEY` (append `api_key`); handle 401/403/409/429 with a structured actionable note. |
| 2 | **MUST** | Config → pydantic-settings + SecretStr | `config.py`, import sites | `BaseSettings`, `SecretStr` for secrets, validation, built in lifespan. |
| 3 | **MUST** | Lifespan + kill global-client race | `server.py`, `client.py` | Typed `AppContext` + `@asynccontextmanager` lifespan; remove module-global `_client`; idempotent `aclose()`; persist in `finally`. |
| 4 | **MUST** | Test/type/lint/CI scaffolding | `tests/`, `pyproject.toml`, `.github/workflows/`, `py.typed` | pytest-asyncio + respx, pyright-strict, ruff (T20), Actions matrix. |
| 5 | **MUST** | No stdout pollution | `server.py`, `client.py`, `browser.py` | Centralize logger config; move TLS warn to logger; T20. |
| 6 | **SHOULD** | httpx limits/timeouts/HTTP-2/retries | `client.py`, `config.py`, `browser.py` | `httpx.Limits`, granular `Timeout`, `http2`, `AsyncHTTPTransport(retries=2)`; configurable browser wait. |
| 7 | **SHOULD** | Rate limit + backoff | `search.py`, `client.py` | `AsyncLimiter` + `get_with_backoff` honoring `Retry-After`. |
| 8 | **SHOULD** | Untrusted-content wrapping | `fetch.py` | Strip script/style; spotlight/datamark wrap; strip CSRF/cookies. |
| 9 | **SHOULD** | Catalog caching | `client.py` | `TTLCache` under lock, `force` bypass. |
| 10 | **SHOULD** | SSRF DNS-pin + redirect re-validate | `client.py` | Resolve-once, pin IP, re-validate redirects, allowlist. |
| 11 | **SHOULD** | Tighten deps + packaging | `pyproject.toml` | Raise floors; `[project.scripts]`; dev group; `py.typed`. |
| 12 | **SHOULD** | Explicit error handling | `browser.py`, `fetch.py`, `client.py` | Replace bare `except: pass` with typed catches + logged degradations. |
| 13 | **NICE** | Resources + Prompts | `server.py` | `catalog://` resource + template, `proxy://{id}`; `search_by_field`/`download_all_pdfs`/`search_help` prompts. |
| 14 | **NICE** | Structured tool output | `server.py`, `search.py` | `TypedDict`/Pydantic output models → `outputSchema`. |
| 15 | **NICE** | Circuit breaker | `client.py` | Per-host breaker; fail fast. |
| 16 | **NICE** | Dockerfile + health + release CD | `Dockerfile`, `.github/workflows/release.yml`, `CHANGELOG.md` | uv multi-stage; `/health`; PyPI Trusted Publishing. |
| 17 | **NICE** | Input validation | `server.py`, `search.py` | Bound query length, year range, clamp limit. |
| 18 | **NICE** | Parser resilience + docs | `search.py`, `README.md` | Per-provider docstrings; backend-hierarchy docs; ADRs. |

---

## 9. Key References

- MCP Specification 2025-11-25 — https://modelcontextprotocol.io/specification/2025-11-25
- Server Resources & Templates — https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- Python SDK README — https://github.com/modelcontextprotocol/python-sdk
- SDK in-memory test helper — `mcp.shared.memory.create_connected_server_and_client_session`
- OpenAlex authentication (API key) — https://developers.openalex.org/guides/authentication
- httpx resource limits / timeouts / transports — https://www.python-httpx.org/advanced/
- Pydantic Settings (SecretStr) — https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- MCP Security Best Practices — https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- Protecting against indirect prompt injection in MCP (Microsoft) — https://developer.microsoft.com/blog/protecting-against-indirect-injection-attacks-mcp
- aiolimiter — https://github.com/mjpieters/aiolimiter
- respx — https://github.com/lundberg/respx
