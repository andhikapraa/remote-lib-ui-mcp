"""Shared test fixtures. Network is never hit: OpenAlex is mocked with respx and
the EZproxy/CAS paths are exercised only up to the SSRF guard."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate every test from the host's REMOTE_LIB_* environment."""
    for k in list(os.environ):
        if k.startswith("REMOTE_LIB_"):
            monkeypatch.delenv(k, raising=False)
