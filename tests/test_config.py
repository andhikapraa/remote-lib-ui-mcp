import pytest
from pydantic import ValidationError

from remote_lib_mcp.config import Config


def test_defaults():
    c = Config(_env_file=None)
    assert c.base_url == "https://remote-lib.ui.ac.id"
    assert c.has_credentials is False
    assert c.disabled_resources == ()
    assert c.http2 is True


def test_secretstr_is_hidden():
    c = Config(_env_file=None, username="u", password="topsecret")
    assert "topsecret" not in repr(c)
    assert "topsecret" not in str(c)
    assert c.password is not None and c.password.get_secret_value() == "topsecret"
    assert c.has_credentials is True


def test_rejects_non_http_url():
    with pytest.raises(ValidationError):
        Config(_env_file=None, base_url="ftp://example.com")


def test_rejects_nonpositive_timeout():
    with pytest.raises(ValidationError):
        Config(_env_file=None, http_timeout=0)


def test_strips_trailing_slash():
    c = Config(_env_file=None, base_url="https://x.test/")
    assert c.base_url == "https://x.test"


def test_disabled_csv_parsing():
    c = Config(_env_file=None, disabled="scopus, JSTOR ,")
    assert c.disabled_resources == ("scopus", "JSTOR")
