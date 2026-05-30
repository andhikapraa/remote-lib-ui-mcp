import json

from remote_lib_mcp import search


def test_filters_from_dict_coercion():
    f = search.SearchFilters.from_dict(
        {"year_from": "2020", "open_access": "false", "sort": "date"}
    )
    assert f.year_from == 2020
    assert f.open_access is False  # string "false" must not be truthy
    assert f.sort == "date"


def test_sciencedirect_build_maps_filters():
    p = search.ScienceDirectProvider().build(
        "machine learning",
        search.SearchFilters(year_from=2020, year_to=2024, open_access=True, content_type="review"),
    )
    assert "qs=machine+learning" in p.target_url
    assert "date=2020-2024" in p.target_url
    assert "accessTypes=openAccess" in p.target_url
    assert "articleTypes=REV" in p.target_url  # mapped from free text
    assert {"year_from", "year_to", "open_access", "content_type"} <= set(p.applied)


def test_openalex_denylist_and_scoping():
    assert search.openalex_for("hukum-online", "Hukum Online") is None
    assert search.openalex_for("scival", "SciVal") is None
    emerald = search.openalex_for("emerald-insight", "Emerald")
    assert emerald is not None and emerald._pub is not None
    jstor = search.openalex_for("jstor", "JSTOR")
    assert jstor is not None and jstor._pub is None  # general (aggregator)


def test_openalex_build_year_and_key_filters():
    p = search.OpenAlexProvider("scopus", "Scopus").build(
        "graphene", search.SearchFilters(year_from=2021, year_to=2023, open_access=True)
    )
    assert "from_publication_date:2021-01-01" in p.target_url
    assert "to_publication_date:2023-12-31" in p.target_url
    assert "is_oa:true" in p.target_url
    assert {"year_from", "year_to", "open_access"} <= set(p.applied)


def test_openalex_parse_extracts_fields():
    data = {
        "results": [
            {
                "title": "Graphene study",
                "doi": "https://doi.org/10.1/g",
                "publication_year": 2023,
                "authorships": [{"author": {"display_name": "A. One"}}],
                "primary_location": {
                    "landing_page_url": "https://pub.test/g",
                    "source": {"display_name": "J. Mat"},
                },
                "best_oa_location": {"pdf_url": "https://pub.test/g.pdf"},
                "open_access": {"is_oa": True},
            }
        ]
    }
    res = search.OpenAlexProvider("scopus", "Scopus").parse(json.dumps(data), "", 5)
    assert len(res) == 1
    r = res[0]
    assert r.title == "Graphene study"
    assert r.doi == "10.1/g"
    assert r.year == "2023"
    assert r.url == "https://pub.test/g"
    assert r.pdf_url == "https://pub.test/g.pdf"
