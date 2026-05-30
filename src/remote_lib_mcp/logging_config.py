"""Logging setup — stderr only (never pollute the stdio JSON-RPC channel),
with a redaction filter so secrets/tickets/cookies never reach the logs."""

from __future__ import annotations

import logging
import os
import re
import sys

_REDACT: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(password=)[^&\s]+", re.I), r"\1***"),
    (re.compile(r"(ticket=ST-)[^&\s]+", re.I), r"\1***"),
    (re.compile(r"(CASTGC=)[^;\s]+", re.I), r"\1***"),
    (re.compile(r"(api_key=)[^&\s]+", re.I), r"\1***"),
    (re.compile(r"(ezproxy[ln]?=)[^;&\s]+", re.I), r"\1***"),
]


class RedactionFilter(logging.Filter):
    """Scrub secret-shaped substrings from any log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = msg
        for pat, repl in _REDACT:
            red = pat.sub(repl, red)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Idempotently route the package logger to stderr and quiet noisy libs."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    lvl = (level or os.environ.get("REMOTE_LIB_LOG_LEVEL", "INFO")).upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactionFilter())
    pkg = logging.getLogger("remote_lib_mcp")
    pkg.handlers[:] = [handler]
    pkg.setLevel(getattr(logging, lvl, logging.INFO))
    pkg.propagate = False
    # These can emit request URLs (tickets/qurl) or DEBUG noise at INFO.
    for noisy in ("httpx", "httpcore", "hpack", "h2", "cloakbrowser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"remote_lib_mcp.{name}")
