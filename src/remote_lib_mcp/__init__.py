"""MCP server for UI's remote library gateway (remote-lib.ui.ac.id).

The portal is an OCLC EZproxy gateway fronted by UI's CAS single sign-on. This
package logs in over CAS, mints authenticated EZproxy URLs, fetches content
through the proxy (with a cloakbrowser stealth fallback for Cloudflare-walled or
JS-rendered databases), and runs best-effort searches against the major
subscribed databases.
"""

__version__ = "0.1.0"
