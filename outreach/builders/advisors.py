"""Pre-health advisor sourcing -- NAAHP (National Association of Advisors
for the Health Professions) member directory + university pre-health
advising pages.

This is genuinely the hardest builder to make reliable: NAAHP's directory is
an interactive member search behind a form (no stable public listing URL to
paginate), and pre-health advising pages are hand-built, wildly inconsistent
HTML across hundreds of universities -- no shared markup, no shared CMS.
Scraping "all of them" robustly can't be guaranteed. So this module is
built defensively in two complementary ways:

1. `scrape_advisor_page(url)` -- given a *specific* advising-office page URL
   (an admin/operator supplies the list of URLs; that's a one-time,
   low-effort curation step, not something this module tries to discover on
   its own), extracts contact-looking info (names near "advisor"/"director"
   job-title text, `mailto:` links, phone numbers) defensively: a
   short timeout, a real User-Agent, `robots.txt` respected, and any
   per-page failure (timeout, 404, unparseable HTML) caught and logged
   rather than raising -- so one broken university page can't kill a batch
   of 200 others.
2. `import_advisors_csv(path)` -- a manual CSV-import fallback for when a
   human already has (or hand-collects) a list of advisor contacts, which
   given (1)'s reliability ceiling is the recommended primary path for any
   list that needs to be trustworthy rather than best-effort.
"""

import csv
import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .common import Candidate

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
USER_AGENT = "SixthVitalOutreachBot/1.0 (+https://thesixthvital.com/about-our-outreach)"
TITLE_KEYWORDS = ("advisor", "advising", "director", "pre-health", "pre-med")


def _robots_allow(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        resp = requests.get(robots_url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            return True  # no robots.txt (or unreadable) -- nothing disallowing us
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        return True  # fail open on robots.txt fetch errors, not on the page fetch itself


def scrape_advisor_page(url, org_hint=None, state_hint=None):
    """Best-effort extraction of advisor contacts from one advising-office
    page. Returns a list of candidate dicts (often 0 or 1, occasionally a
    few for a page listing a whole advising team) -- never raises; any
    failure is logged and results in `[]` for this URL so the caller's loop
    (see `scrape_advisor_pages`) can continue.
    """
    if not _robots_allow(url):
        log.info("advisors: robots.txt disallows fetching %s, skipping", url)
        return []

    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("advisors: could not fetch %s: %s", url, exc)
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # BeautifulSoup/html.parser can be surprising on real-world markup
        log.warning("advisors: could not parse %s: %s", url, exc)
        return []

    org = org_hint or _guess_org(soup, url)
    candidates = []
    for mailto in soup.select('a[href^="mailto:"]'):
        email = mailto["href"].split("mailto:", 1)[1].split("?")[0].strip()
        if not email or "@" not in email:
            continue
        name = _nearby_name(mailto) or email.split("@")[0].replace(".", " ").title()
        if not _looks_like_advisor_context(mailto):
            continue  # a mailto on the page that isn't near advisor/advising language -- skip, too uncertain
        candidates.append(Candidate(
            name=name,
            track="applicant",
            source="advisors",
            email=email,
            org=org,
            role="Pre-Health Advisor",
            state=state_hint,
            external_id=None,
            tags=[f"source_url:{url}"],
        ))
    return candidates


def scrape_advisor_pages(urls, org_hints=None, state_hints=None):
    """Runs `scrape_advisor_page` over a list of URLs, isolating failures
    per-site so a handful of broken university pages don't stop the run.
    `org_hints`/`state_hints`, if given, are dicts keyed by URL."""
    org_hints, state_hints = org_hints or {}, state_hints or {}
    all_candidates = []
    for url in urls:
        try:
            all_candidates.extend(
                scrape_advisor_page(url, org_hint=org_hints.get(url), state_hint=state_hints.get(url))
            )
        except Exception as exc:  # belt-and-suspenders: scrape_advisor_page already catches its own errors
            log.error("advisors: unexpected error scraping %s: %s", url, exc)
    return all_candidates


def _guess_org(soup, url):
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:300]
    return urlparse(url).netloc


def _nearby_name(mailto_tag):
    text = mailto_tag.get_text(strip=True)
    if text and "@" not in text and len(text) < 100:
        return text
    parent = mailto_tag.find_parent(["li", "p", "div", "tr"])
    if parent:
        candidate_text = parent.get_text(" ", strip=True)
        if candidate_text and len(candidate_text) < 150:
            return candidate_text.split(",")[0].split("|")[0].strip()
    return None


def _looks_like_advisor_context(mailto_tag):
    parent = mailto_tag.find_parent(["li", "p", "div", "tr", "section"])
    context = (parent.get_text(" ", strip=True) if parent else mailto_tag.get_text(" ", strip=True)).lower()
    return any(kw in context for kw in TITLE_KEYWORDS)


# ── manual CSV-import fallback ──────────────────────────────────────────

_CSV_HEADER_ALIASES = {
    "name": "name",
    "advisor name": "name",
    "email": "email",
    "org": "org",
    "school": "org",
    "university": "org",
    "institution": "org",
    "state": "state",
    "url": "source_url",
    "source url": "source_url",
}


def import_advisors_csv(path):
    """Reads a hand-collected CSV of advisor contacts (columns: name, email,
    org/school/university, state, url -- case-insensitive, extras ignored).
    Skips rows missing a name+email pair rather than raising."""
    candidates = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            field_map = {
                raw: _CSV_HEADER_ALIASES[(raw or "").strip().lower()]
                for raw in (reader.fieldnames or [])
                if (raw or "").strip().lower() in _CSV_HEADER_ALIASES
            }
            for i, raw_row in enumerate(reader):
                row = {field_map[k]: v for k, v in raw_row.items() if k in field_map}
                name, email = (row.get("name") or "").strip(), (row.get("email") or "").strip()
                if not (name and email):
                    log.warning("advisors: skipping row %d in %s (missing name or email)", i, path)
                    continue
                tags = [f"source_url:{row['source_url']}"] if row.get("source_url") else []
                candidates.append(Candidate(
                    name=name, track="applicant", source="advisors_csv", email=email,
                    org=(row.get("org") or "").strip() or None, role="Pre-Health Advisor",
                    state=(row.get("state") or "").strip() or None, tags=tags,
                ))
    except OSError as exc:
        log.error("advisors: could not read %s: %s", path, exc)
        return []
    return candidates
