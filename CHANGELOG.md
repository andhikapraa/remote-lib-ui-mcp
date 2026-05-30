# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.2.1] — 2026-05-30

### Fixed / Docs
- **Honest prerequisites.** The Claude Desktop (`.mcpb`) path requires `uv` installed once
  (Claude Desktop bundles Node, not Python/uv, and runs the server via the user's `uv`); `uv`
  then provisions Python automatically. README and the extension's install description now say so,
  with one-line `uv` install steps for macOS (Homebrew) and Windows, plus a `spawn uv ENOENT` note.

## [0.2.0] — 2026-05-30

Production-hardening release.

### Added
- **MCP Resources & Prompts** alongside the existing tools:
  - Resource `catalog://resources` (full database catalog).
  - Resource templates `catalog://resources/{resource_id}` and `proxy://{resource_id}`.
  - Prompts `search_by_field`, `download_all_pdfs`, `search_help`.
- `set_resource_enabled` tool + `REMOTE_LIB_DISABLED` env var to switch databases on/off.
- **OpenAlex** metadata backend for scrape-resistant databases (publisher-scoped or
  general), with optional `REMOTE_LIB_OPENALEX_API_KEY` and graceful 401/403/409/429 handling.
- Per-publisher PDF download patterns (JSTOR, IEEE, Atypon `/doi/pdf`, ScienceDirect `/pdfft`).
- Untrusted-content wrapping (spotlight/datamark) on fetched page text.
- Catalog TTL cache; httpx `Limits`, granular `Timeout`, HTTP/2, connect retries.
- pydantic-settings config with `SecretStr` secrets and validation.
- FastMCP `lifespan` managing a single shared client (removes the global-client race).
- Centralized stderr logging with secret redaction.
- Test suite (pytest + respx, in-memory MCP client), ruff, pyright, GitHub Actions CI.
- Dockerfile (uv multi-stage), `py.typed`, `docs/PRODUCTION_READINESS.md`.

### Changed
- EZproxy access now uses the `qurl=` (percent-encoded) gateway parameter.
- Deprecated **Royal Society of Chemistry** (dead upstream link) — excluded from the catalog.
- Dependency floors raised (`mcp>=1.27`, `httpx[http2]>=0.28`).

### Security
- Bundled the Sectigo intermediate the gateway omits (TLS verification stays on).
- SSRF guard rejects internal/loopback/non-public targets and non-http(s) schemes.
- Session file written `0o600`; secrets never logged (redaction filter + `SecretStr`).

## [0.1.0]

- Initial MCP server: CAS SSO login, EZproxy access, content fetch, and per-database
  search with a cloakbrowser stealth fallback.
