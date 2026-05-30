<div align="center">

<img src="assets/icon.png" alt="UI Remote Library MCP" width="120" height="120" />

# UI Remote Library for Claude

**Search your university library's 25+ databases — ScienceDirect, IEEE, JSTOR, Scopus, SpringerLink, and more — straight from a Claude chat, using your own UI login, all on your own computer.**

![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP server](https://img.shields.io/badge/MCP-server-7C3AED)
![Built with FastMCP](https://img.shields.io/badge/built%20with-FastMCP-1E2A78)

</div>

You log into [remote-lib.ui.ac.id](https://remote-lib.ui.ac.id/) by hand to read papers off-campus. This connects that same library to **Claude**, so you can just ask:

> *"Find me 5 recent open-access papers on graph neural networks for drug discovery, and give me the PDF links."*

…and Claude searches the databases, filters by year, and hands back titles, authors, and downloadable PDFs — through your institutional access.

---

## ✨ What you can ask it

- *"Search ScienceDirect for transformer architectures since 2022, open access only."*
- *"Find systematic reviews about CBT for anxiety on Cochrane."*
- *"Look up this DOI on Scopus and get me the PDF."*
- *"Search IEEE for federated learning on edge devices, newest first."*
- *"What databases can I search?"* → it lists everything available to you.

It covers Computer Science, ML/AI, medicine, psychology and mental health, and more — see [Databases & coverage](#-databases--coverage).

---

## 🚀 Quick start — Claude Desktop (recommended)

**No Python and no config-file editing needed** — but you do need two things: the free **[Claude Desktop](https://claude.ai/download)** app, and **[`uv`](https://docs.astral.sh/uv/)** installed once. Claude Desktop uses `uv` to run the server, and `uv` then downloads the right Python for you automatically.

**Step 0 — install `uv` once** (official [installer](https://docs.astral.sh/uv/getting-started/installation/)):
- **macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Then **fully quit and reopen Claude Desktop** so it picks up `uv`.

1. **Install Claude Desktop** (macOS or Windows) and update it to the latest version.
2. **Download `remote-lib-ui.mcpb`** from this project's [**Releases**](https://github.com/andhikapraa/remote-lib-ui-mcp/releases) page.
3. **Double-click the file.** Claude opens an install dialog. *(Or: Claude → Settings → Extensions → Advanced settings → Install Extension…)*
4. **Type two things** in the form:
   - **Library username** — your UI SSO username (the NetID you log in with, *not* your email)
   - **Library password** — your UI SSO password *(it's masked and saved to your computer's keychain — never typed into a file)*
   - *(Optional OpenAlex key — leave blank.)*
5. Click **Install**. If the tools don't show up right away, fully **Quit and reopen** Claude Desktop.
6. In a chat, click **➕ → Connectors** to confirm the library tools are there, then ask your first question.

> 💡 To change your password later: **Settings → Extensions →** this extension → edit the fields.

> 🔒 **This runs locally on your computer.** Your password stays on your machine (in the macOS Keychain / Windows Credential Manager) and is only ever sent to the UI login page. The “Add custom connector” flow on **claude.ai (web)** and the connector flow in **ChatGPT** are for *remote* servers and **do not apply** to this tool — use Claude Desktop.

---

## Other ways to install

<details>
<summary><b>Claude Code (one terminal command)</b></summary>

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and a local clone of this repo.

```bash
claude mcp add --scope user \
  --env REMOTE_LIB_USERNAME=your.sso.username \
  --env REMOTE_LIB_PASSWORD=your.sso.password \
  remote-lib-ui -- uv run --directory /ABSOLUTE/PATH/TO/remote-lib-ui-mcp remote-lib-ui-mcp
```
</details>

<details>
<summary><b>Manual config (Claude Desktop, fallback)</b></summary>

Edit your Claude Desktop config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "remote-lib-ui": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/remote-lib-ui-mcp", "remote-lib-ui-mcp"],
      "env": {
        "REMOTE_LIB_USERNAME": "your.sso.username",
        "REMOTE_LIB_PASSWORD": "your.sso.password"
      }
    }
  }
}
```
Notes: this stores your password in **plaintext** in that file (the `.mcpb` install above does not). If Claude can't find `uv`, use its **absolute path** (e.g. `/Users/you/.local/bin/uv`).
</details>

<details>
<summary><b>ChatGPT (advanced — not recommended for this tool)</b></summary>

ChatGPT only connects to **remote** MCP servers over a public HTTPS URL and **cannot send a username/password** to a connector. This server is *local* and uses your *personal* login, so making it work in ChatGPT means hosting it yourself behind a public tunnel with **no authentication** — which would expose your live library session to anyone who finds the URL, and likely breaks library/publisher terms. **Use Claude instead.** (The server *can* run `--transport http` if you really know what you're doing, but it isn't supported as an easy path.)
</details>

---

## 🔍 What's inside

**Tools** Claude can call:

| Tool | What it does |
|------|------|
| `search` | Search a database with filters (year, author, open-access, sort) → rows of results |
| `get_download_url` | Resolve the PDF / full-text link for an article |
| `fetch_url` | Read a page through the proxy (markdown / text) |
| `get_proxy_url` | Get an authenticated link to open in a browser |
| `list_resources` | List every database available to you |
| `set_resource_enabled` | Turn a database on/off |
| `refresh_catalog` | Refresh the database list |

It also exposes the catalog as an MCP **Resource** (`catalog://resources`) and a few **Prompts** (`search_by_field`, `download_all_pdfs`, `search_help`) for guided workflows.

### How it works (short version)

It logs into the EZproxy gateway with your SSO credentials, then searches each database two ways: a **scraper** reads the site's own results where possible; for sites that block scraping (JavaScript apps, bot walls), it falls back to the free **[OpenAlex](https://openalex.org)** scholarly index and routes downloads back through the gateway for institutional access. A stealth-browser fallback ([cloakbrowser](https://github.com/CloakHQ/CloakBrowser)) handles Cloudflare-protected pages. Full architecture: [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md).

---

## 📚 Databases & coverage

Tuned live across every database. **Rows** = parsed results in the chat; **PDF** = a resolvable download (✅ direct PDF · 🔗 institutional full-text link).

| Tier | Databases | Rows | PDF |
|------|-----------|:----:|:---:|
| **Native (real catalog + publisher PDFs)** | ScienceDirect, SpringerLink, Sage Journals, Taylor & Francis, Wiley (×2), ACM, IEEE Xplore, Oxford Journals/Ebook, Access Pharmacy, T&F eBooks | ✅ | ✅ |
| Native | Cambridge Core, Cochrane, Ebrary | ✅ | 🔗 |
| **OpenAlex (publisher-scoped, accurate)** | Emerald, Annual Reviews, APA PsycArticles, Sage Knowledge, ClinicalKey (+Nursing) | ✅ | ✅/🔗 |
| **OpenAlex (general scholarly index)** | Scopus, JSTOR, ProQuest, EBSCOhost, HeinOnline | ✅ | ✅ |
| **Entry-only** (returns a search link, no parsed rows) | Hukum Online, Alexander Street, ALA Ebooks | — | — |

*Not databases:* SciVal (analytics tool) and TRINKA AI (writing assistant). *Deprecated:* Royal Society of Chemistry (dead upstream link).

> **Pick your fields:** you can hide databases you don't use. For example, a CS/ML/Health student might hide law/finance/management databases via `REMOTE_LIB_DISABLED=heinonline,hukum-online,wiley-journal-of-finance,wiley-strategic-management-journal,american-library-association-ebooks,scival,alexander-street-press,emerald-insight` — or just ask Claude to `set_resource_enabled`.

---

## ⚙️ Configuration

For the `.mcpb` install you set everything in the form. For manual/dev installs, all settings are `REMOTE_LIB_*` environment variables (read from the env or a `.env` file):

| Variable | Default | Purpose |
|---|---|---|
| `USERNAME` / `PASSWORD` | — (required) | Your UI SSO login |
| `OPENALEX_API_KEY` | — (optional) | Higher OpenAlex search quota |
| `DISABLED` | — | Comma-separated databases to hide |
| `SESSION_FILE` | — | Persist your login between restarts (cookie file, `0o600`) |
| `HTTP_TIMEOUT` / `HTTP2` / `MAX_CONNECTIONS` | 45 / true / 20 | Network tuning |
| `VERIFY_SSL` | true | TLS verification (leave on) |
| `LOG_LEVEL` | INFO | Log verbosity (logs go to stderr) |

---

## 🔒 Security & privacy

- **Your password stays on your computer.** It's sent only to the UI SSO login page over HTTPS, and (with the `.mcpb` install) stored in your OS keychain — never in a chat or a cloud service.
- **No secrets in shared files.** Don't commit your password to `.mcp.json` or a committed config; use the `.mcpb` form, your OS keychain, or a gitignored `.env`.
- **Content from the web is treated as untrusted** — page text is wrapped so the model won't follow instructions hidden inside scraped pages.
- **Use it within the rules.** This is an unofficial tool; use only your own credentials and within your library's and publishers' terms (no bulk downloading / redistribution).

---

## 🩹 Troubleshooting

- **Tools don't appear** → fully Quit and reopen Claude Desktop (not just close the window).
- **"Login rejected"** → re-check your username (NetID, not email) and password in Settings → Extensions.
- **Scopus / JSTOR / Emerald give odd results** → these use the OpenAlex scholarly index, not the database's own engine; results are real papers but not the native catalog.
- **A search returns no rows but a link** → that database is "entry-only" (or JS-heavy); open the returned `proxied_search_url` in your browser.
- **`spawn uv ENOENT`** → `uv` isn't on the PATH the app can see. Run the Step 0 installer, then **fully quit and reopen Claude Desktop**. If it persists, the installer placed `uv` at `~/.local/bin/uv` (macOS) or `%USERPROFILE%\.local\bin\uv.exe` (Windows) — in the manual config, use that **absolute path** as the `command`.

---

## 🧑‍💻 For developers / maintainers

```bash
uv sync --extra browser                       # install everything incl. dev tools + stealth browser
uv run ruff check . && uv run ruff format --check .   # lint + format
uv run pyright src/remote_lib_mcp             # type check
uv run pytest                                 # tests (no network; in-memory MCP client + respx)
uv run python tests/smoke.py                  # optional LIVE check (needs credentials)
```

**Build the Claude Desktop extension** (`.mcpb`):
```bash
npx -y @anthropic-ai/mcpb pack .              # -> remote-lib-ui.mcpb  (attach to a GitHub Release)
```

**Run as a container** (stdio):
```bash
docker build -t remote-lib-ui-mcp . && docker run --rm -i \
  -e REMOTE_LIB_USERNAME=... -e REMOTE_LIB_PASSWORD=... remote-lib-ui-mcp
```

CI (GitHub Actions) runs ruff + pyright + pytest on Python 3.10–3.13; tagging `vX.Y.Z` builds and
publishes the `.mcpb`. `uv.lock` is committed. Full design + hardening notes:
[`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md).

---

<div align="center">
<sub>Unofficial. Built with the <a href="https://modelcontextprotocol.io">Model Context Protocol</a> · MIT licensed</sub>
</div>
