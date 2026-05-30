"""Exception hierarchy for the remote-lib MCP server."""

from __future__ import annotations


class RemoteLibError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(RemoteLibError):
    """Missing or invalid configuration (e.g. no credentials)."""


class AuthError(RemoteLibError):
    """CAS login failed (bad credentials, changed login form, SSO down)."""


class SessionExpiredError(RemoteLibError):
    """The EZproxy session was rejected and re-authentication did not recover it."""


class ResourceNotFoundError(RemoteLibError):
    """The requested resource name is not in the catalog."""


class InvalidTargetError(RemoteLibError):
    """A raw target URL was rejected (bad scheme or non-public/internal host)."""


class ResourceDisabledError(RemoteLibError):
    """The requested resource exists but is switched off."""


class BlockedError(RemoteLibError):
    """The upstream database blocked the request (e.g. Cloudflare bot wall)."""


class BrowserUnavailableError(RemoteLibError):
    """The cloakbrowser stealth engine is required but not installed/usable."""
