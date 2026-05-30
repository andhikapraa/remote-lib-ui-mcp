"""TLS trust configuration.

remote-lib.ui.ac.id serves a leaf certificate without its issuing intermediate,
so OpenSSL-based clients (httpx) cannot build a chain to a trusted root even
though the root is in certifi. Browsers and the macOS/Windows trust stores work
around this by fetching the intermediate via the certificate's AIA extension;
OpenSSL does not. We ship the missing intermediate(s) under ``certs/`` and trust
them alongside certifi, so verification stays enabled and portable.
"""

from __future__ import annotations

import glob
import os
import ssl

import certifi

_CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")


def build_ssl_context() -> ssl.SSLContext:
    # SECURITY NOTE: loading the bundled intermediate via load_verify_locations
    # adds it as a trust anchor, so any leaf legitimately issued by that public
    # Sectigo DV intermediate would validate (for any hostname). The practical
    # risk is bounded — an attacker would still need a Sectigo-DV cert for the
    # exact target hostname (remote-lib/sso.ui.ac.id), which they cannot obtain
    # without controlling those domains — and httpx keeps hostname checking on.
    # This is the minimal portable fix for the gateway's missing-intermediate
    # handshake; verification stays enabled.
    ctx = ssl.create_default_context(cafile=certifi.where())
    for pem in sorted(glob.glob(os.path.join(_CERTS_DIR, "*.pem"))):
        try:
            ctx.load_verify_locations(cafile=pem)
        except ssl.SSLError:
            # Skip a malformed bundled cert rather than failing all TLS.
            continue
    return ctx
