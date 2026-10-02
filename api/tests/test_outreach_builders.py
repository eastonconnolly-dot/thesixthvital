import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root, for outreach.*

from extensions import db
from models import Lead, SuppressedEmail
from outreach.builders import acgme, advisors, common, enrich, hcahps, nppes, places


# ── common ────────────────────────────────────────────────────────────────

def test_dedupe_by_key_removes_case_insensitive_duplicates():
    items = [{"email": "A@x.com"}, {"email": "a@x.com"}, {"email": "b@x.com"}]
    deduped = common.dedupe_by_key(items, lambda c: c["email"])
    assert [i["email"] for i in deduped] == ["A@x.com", "b@x.com"]


def test_dedupe_by_key_passes_through_falsy_keys():
    items = [{"email": None}, {"email": None}, {"email": "x@x.com"}]
    deduped = common.dedupe_by_key(items, lambda c: c["email"])
    assert len(deduped) == 3  # both None-key items pass through unmerged


# ── nppes ────────────────────────────────────────────────────────────────

_NPPES_PAGE = {
    "result_count": 1,
    "results": [
        {
            "number": "1730573296",
            "basic": {"first_name": "SHEIDA", "last_name": "AALAMI", "credential": "M.D."},
            "addresses": [
                {"address_purpose": "LOCATION", "state": "WA", "telephone_number": "206-520-5000"},
            ],
            "taxonomies": [{"desc": "Internal Medicine", "primary": True}],
        },
        {
            # organizational record -- no first/last name -- must be skipped
            "number": "1548219215",
            "basic": {"organization_name": "24 ON PHYSICIANS, PC"},
            "addresses": [],
            "taxonomies": [],
        },
    ],
}


def test_nppes_search_physicians_parses_and_skips_org_records():
    with patch("outreach.builders.nppes.requests.get") as mock_get:
        mock_get.return_value = Mock(status_code=200, json=lambda: _NPPES_PAGE, raise_for_status=lambda: None)
        results = nppes.search_physicians(state="WA", max_results=200)

    assert len(results) == 1  # the org record was skipped
    candidate = results[0]
    assert candidate["name"] == "SHEIDA AALAMI, M.D."
    assert candidate["track"] == "physician"
    assert candidate["source"] == "nppes"
    assert candidate["state"] == "WA"
    assert candidate["phone"] == "206-520-5000"
    assert candidate["role"] == "Internal Medicine"
    assert candidate["external_id"] == "1730573296"


def test_nppes_search_physicians_stops_pagination_on_short_page():
    with patch("outreach.builders.nppes.requests.get") as mock_get:
        mock_get.return_value = Mock(status_code=200, json=lambda: _NPPES_PAGE, raise_for_status=lambda: None)
        nppes.search_physicians(max_results=500)
    assert mock_get.call_count == 1  # a page shorter than the limit means no more pages


def test_nppes_search_physicians_never_raises_on_request_failure():
    import requests
    with patch("outreach.builders.nppes.requests.get", side_effect=requests.ConnectionError("boom")):
        results = nppes.search_physicians(state="WA")
    assert results == []


_NPPES_MIXED_CREDENTIALS_PAGE = {
    "result_count": 5,
    "results": [
        {
            "number": "1", "basic": {"first_name": "A", "last_name": "DOC", "credential": "M.D."},
            "addresses": [], "taxonomies": [],
        },
        {
            "number": "2", "basic": {"first_name": "B", "last_name": "DOC", "credential": "MD, FACS"},
            "addresses": [], "taxonomies": [],
        },
        {
            "number": "3", "basic": {"first_name": "C", "last_name": "NURSE", "credential": "ARNP"},
            "addresses": [], "taxonomies": [],
        },
        {
            # real NPPES trap: "PharmD" contains the substring "MD"
            "number": "4", "basic": {"first_name": "D", "last_name": "PHARM", "credential": "PharmD"},
            "addresses": [], "taxonomies": [],
        },
        {
            "number": "5", "basic": {"first_name": "E", "last_name": "UNKNOWN", "credential": ""},
            "addresses": [], "taxonomies": [],
        },
    ],
}


def test_nppes_search_physicians_filters_to_md_do_only():
    with patch("outreach.builders.nppes.requests.get") as mock_get:
        mock_get.return_value = Mock(
            status_code=200, json=lambda: _NPPES_MIXED_CREDENTIALS_PAGE, raise_for_status=lambda: None,
        )
        results = nppes.search_physicians(state="WA", max_results=200)

    names = {r["name"] for r in results}
    assert names == {"A DOC, M.D.", "B DOC, MD, FACS"}  # NP/PharmD/blank-credential all excluded


# ── hcahps ───────────────────────────────────────────────────────────────

_HCAHPS_ROWS = {
    "results": [
        {
            "facility_id": "500002", "facility_name": "PROVIDENCE ST MARY MEDICAL CENTER",
            "state": "WA", "telephone_number": "(509) 522-5900",
            "hcahps_answer_percent": "78",
        },
        {
            # duplicate facility_id -- must be collapsed to one candidate
            "facility_id": "500002", "facility_name": "PROVIDENCE ST MARY MEDICAL CENTER",
            "state": "WA", "telephone_number": "(509) 522-5900",
            "hcahps_answer_percent": "78",
        },
        {
            "facility_id": "500003", "facility_name": "ANOTHER HOSPITAL",
            "state": "WA", "telephone_number": "(509) 000-0000",
            "hcahps_answer_percent": "65",
        },
    ],
}


def test_hcahps_search_hospitals_dedupes_by_facility_id():
    with patch("outreach.builders.hcahps.requests.get") as mock_get:
        mock_get.return_value = Mock(status_code=200, json=lambda: _HCAHPS_ROWS, raise_for_status=lambda: None)
        results = hcahps.search_hospitals(state="WA")

    assert len(results) == 2
    facility_ids = {c["external_id"] for c in results}
    assert facility_ids == {"500002", "500003"}
    assert all(c["track"] == "program" for c in results)
    assert any("hcahps_H_COMP_1_A_P_pct:78" in c["tags"] for c in results)


def test_hcahps_search_hospitals_never_raises_on_request_failure():
    import requests
    with patch("outreach.builders.hcahps.requests.get", side_effect=requests.Timeout("slow")):
        results = hcahps.search_hospitals(state="WA")
    assert results == []


# ── acgme (CSV import fallback) ─────────────────────────────────────────

def test_import_acgme_csv_parses_rows_and_skips_blank(tmp_path):
    csv_path = tmp_path / "acgme.csv"
    csv_path.write_text(
        "Program Name,Sponsoring Institution,Specialty,City,State,Program Director Name,Program Director Email\n"
        "Internal Medicine Residency,Example Health System,Internal Medicine,Spokane,WA,Jane Doe,jane@example.org\n"
        ",,,,,,\n"  # fully blank row -- must be skipped, not raise
    )
    candidates = acgme.import_acgme_csv(str(csv_path))
    assert len(candidates) == 1
    c = candidates[0]
    assert c["name"] == "Jane Doe"
    assert c["email"] == "jane@example.org"
    assert c["org"] == "Example Health System"
    assert c["state"] == "WA"
    assert c["track"] == "physician"
    assert "specialty:Internal Medicine" in c["tags"]


def test_import_acgme_csv_missing_file_returns_empty():
    assert acgme.import_acgme_csv("/nonexistent/path.csv") == []


# ── places ───────────────────────────────────────────────────────────────

def test_search_practices_noops_without_api_key(app):
    with app.app_context():
        app.config["GOOGLE_PLACES_API_KEY"] = ""
        with patch("outreach.builders.places.requests.get") as mock_get:
            results = places.search_practices("family medicine Spokane WA")
    assert results == []
    mock_get.assert_not_called()


def test_search_practices_parses_results_with_key_configured(app):
    text_search_resp = Mock(status_code=200, raise_for_status=lambda: None, json=lambda: {
        "status": "OK",
        "results": [{"place_id": "abc123", "name": "Spokane Family Medicine", "formatted_address": "123 Main St, Spokane, WA 99201, USA"}],
    })
    details_resp = Mock(status_code=200, raise_for_status=lambda: None, json=lambda: {
        "status": "OK",
        "result": {"name": "Spokane Family Medicine", "formatted_phone_number": "(509) 555-0100", "website": "https://spokanefm.example.com"},
    })
    with app.app_context():
        app.config["GOOGLE_PLACES_API_KEY"] = "test-key"
        with patch("outreach.builders.places.requests.get", side_effect=[text_search_resp, details_resp]):
            results = places.search_practices("family medicine Spokane WA")

    assert len(results) == 1
    c = results[0]
    assert c["name"] == "Spokane Family Medicine"
    assert c["phone"] == "(509) 555-0100"
    assert c["state"] == "WA"
    assert c["external_id"] == "abc123"
    assert "website:https://spokanefm.example.com" in c["tags"]


# ── advisors ─────────────────────────────────────────────────────────────

_ADVISOR_HTML = """
<html><head><title>State University Pre-Health Advising</title></head>
<body>
  <div class="staff">
    <p>Jordan Rivera, Pre-Health Advisor — <a href="mailto:jordan.rivera@stateu.edu">jordan.rivera@stateu.edu</a></p>
  </div>
  <div class="footer">
    Webmaster: <a href="mailto:webmaster@stateu.edu">webmaster@stateu.edu</a>
  </div>
</body></html>
"""


def _mock_get_for_advisors(allow_robots=True):
    def _side_effect(url, **kwargs):
        if url.endswith("/robots.txt"):
            if allow_robots:
                return Mock(status_code=200, text="User-agent: *\nAllow: /")
            return Mock(status_code=200, text="User-agent: *\nDisallow: /")
        resp = Mock(status_code=200, text=_ADVISOR_HTML)
        resp.raise_for_status = lambda: None
        return resp
    return _side_effect


def test_scrape_advisor_page_extracts_advisor_mailto_only():
    with patch("outreach.builders.advisors.requests.get", side_effect=_mock_get_for_advisors()):
        results = advisors.scrape_advisor_page("https://stateu.edu/prehealth/advising", state_hint="WA")

    assert len(results) == 1  # the webmaster mailto is skipped -- not near advisor-context text
    c = results[0]
    assert c["email"] == "jordan.rivera@stateu.edu"
    assert c["track"] == "applicant"
    assert c["state"] == "WA"


def test_scrape_advisor_page_respects_robots_disallow():
    with patch("outreach.builders.advisors.requests.get", side_effect=_mock_get_for_advisors(allow_robots=False)):
        results = advisors.scrape_advisor_page("https://stateu.edu/prehealth/advising")
    assert results == []


def test_scrape_advisor_pages_isolates_per_site_failures():
    def _side_effect(url, **kwargs):
        if "robots.txt" in url:
            return Mock(status_code=200, text="User-agent: *\nAllow: /")
        if "broken.example.com" in url:
            raise Exception("connection reset")
        resp = Mock(status_code=200, text=_ADVISOR_HTML)
        resp.raise_for_status = lambda: None
        return resp

    with patch("outreach.builders.advisors.requests.get", side_effect=_side_effect):
        results = advisors.scrape_advisor_pages([
            "https://broken.example.com/advising",
            "https://stateu.edu/prehealth/advising",
        ])
    assert len(results) == 1  # the broken site contributed nothing but didn't kill the batch


def test_import_advisors_csv_skips_incomplete_rows(tmp_path):
    csv_path = tmp_path / "advisors.csv"
    csv_path.write_text(
        "Name,Email,School,State\n"
        "Sam Lee,sam.lee@college.edu,Example College,OR\n"
        ",missing-name@college.edu,Example College,OR\n"
    )
    candidates = advisors.import_advisors_csv(str(csv_path))
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Sam Lee"
    assert candidates[0]["org"] == "Example College"


# ── enrich: email finding ───────────────────────────────────────────────

def test_find_and_verify_email_uses_hunter_when_configured(app):
    candidate = {"name": "Jane Doe", "org": "Example Health System", "tags": []}
    with app.app_context():
        app.config["HUNTER_API_KEY"] = "test-hunter-key"
        domain_resp = Mock(status_code=200, raise_for_status=lambda: None,
                            json=lambda: {"data": {"domain": "example.org"}})
        finder_resp = Mock(status_code=200, raise_for_status=lambda: None,
                            json=lambda: {"data": {"email": "jane.doe@example.org", "score": 90}})
        with patch("outreach.builders.enrich.requests.get", side_effect=[domain_resp, finder_resp]):
            result = enrich.find_and_verify_email(candidate)
    assert result["email"] == "jane.doe@example.org"
    assert any("email_confidence" in t for t in result["tags"])


def test_find_and_verify_email_falls_back_to_apollo(app):
    candidate = {"name": "Jane Doe", "org": "Example Health System", "tags": []}
    with app.app_context():
        app.config["HUNTER_API_KEY"] = ""
        app.config["APOLLO_API_KEY"] = "test-apollo-key"
        apollo_resp = Mock(status_code=200, raise_for_status=lambda: None,
                            json=lambda: {"person": {"email": "jane@example.org"}})
        with patch("outreach.builders.enrich.requests.post", return_value=apollo_resp):
            result = enrich.find_and_verify_email(candidate)
    assert result["email"] == "jane@example.org"


def test_find_and_verify_email_noop_when_unconfigured(app):
    candidate = {"name": "Jane Doe", "org": "Example Health System", "tags": []}
    with app.app_context():
        app.config["HUNTER_API_KEY"] = ""
        app.config["APOLLO_API_KEY"] = ""
        result = enrich.find_and_verify_email(candidate)
    assert result["email"] is None


def test_find_and_verify_email_leaves_existing_email_untouched(app):
    candidate = {"name": "Jane Doe", "email": "already@known.com", "tags": []}
    with app.app_context():
        app.config["HUNTER_API_KEY"] = "key"
        with patch("outreach.builders.enrich.requests.get") as mock_get:
            result = enrich.find_and_verify_email(candidate)
    assert result["email"] == "already@known.com"
    mock_get.assert_not_called()


# ── enrich: dedup + suppression + ingestion ─────────────────────────────

def test_dedupe_candidates_by_email_and_name_org_fallback():
    candidates = [
        {"name": "A", "email": "a@x.com", "org": "Org1"},
        {"name": "A", "email": "A@X.com", "org": "Org1"},  # dup email, different case
        {"name": "B", "email": None, "org": "Org2"},
        {"name": "B", "email": None, "org": "Org2"},  # dup on (name, org) since no email
        {"name": "C", "email": None, "org": "Org3"},
    ]
    deduped = enrich.dedupe_candidates(candidates)
    assert len(deduped) == 3


def test_suppress_email_is_idempotent(app, db):
    with app.app_context():
        first = enrich.suppress_email("Bounced@Example.com", reason="bounced")
        second = enrich.suppress_email("bounced@example.com")
        assert first.id == second.id
        assert SuppressedEmail.query.count() == 1
        assert enrich.is_suppressed("BOUNCED@example.com") is True
        assert enrich.is_suppressed("someone-else@example.com") is False


def test_ingest_candidates_creates_leads_and_reports_summary(app, db):
    with app.app_context():
        db.session.add(SuppressedEmail(email="blocked@example.com", reason="bounced"))
        db.session.add(Lead(name="Existing", email="existing@example.com", track="physician"))
        db.session.commit()

        candidates = [
            {"name": "New Lead", "email": "new@example.com", "track": "physician", "source": "nppes",
             "org": None, "role": None, "state": "WA", "tags": []},
            {"name": "Blocked Lead", "email": "blocked@example.com", "track": "physician", "source": "nppes",
             "tags": []},
            {"name": "Existing", "email": "existing@example.com", "track": "physician", "source": "nppes",
             "tags": []},
            {"name": "No Email", "email": None, "track": "physician", "source": "places", "tags": []},
        ]
        summary = enrich.ingest_candidates(candidates, enrich_emails=False)

        assert summary == {"created": 1, "skipped_duplicate": 1, "skipped_suppressed": 1, "skipped_no_email": 1}
        assert Lead.query.filter_by(email="new@example.com").count() == 1
