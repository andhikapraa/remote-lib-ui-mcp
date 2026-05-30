from remote_lib_mcp import fetch


def test_looks_blocked_cloudflare():
    wall = "Attention Required! ... checking your browser ... Cloudflare Ray ID"
    assert fetch.looks_blocked(wall, 403) is True
    assert fetch.looks_blocked("<html><body>real content here</body></html>", 200) is False


def test_find_download_url_citation_meta():
    html = '<html><head><meta name="citation_pdf_url" content="https://x.test/p.pdf"></head></html>'
    assert fetch.find_download_url(html, "https://x.test/article/1") == "https://x.test/p.pdf"


def test_derive_pdf_url_jstor():
    out = fetch.derive_pdf_url("https://remote-lib.ui.ac.id:2065/stable/123456")
    assert out is not None and out.endswith("/stable/pdf/123456.pdf")


def test_derive_pdf_url_atypon_doi():
    out = fetch.derive_pdf_url("https://remote-lib.ui.ac.id:2190/doi/full/10.1177/abc123")
    assert out is not None and "/doi/pdf/10.1177/abc123" in out


def test_derive_pdf_url_sciencedirect():
    out = fetch.derive_pdf_url("https://remote-lib.ui.ac.id:2054/science/article/pii/S123ABC")
    assert out is not None and out.endswith("/science/article/pii/S123ABC/pdfft")


def test_derive_pdf_url_none_for_unknown():
    assert fetch.derive_pdf_url("https://x.test/some/random/page") is None
