"""Generic, best-effort HTML lead extraction shared by fellowship_scraper.py
and competition_scraper.py. Real cohort/results pages vary in markup and many
are JS-rendered (unparseable here) — this extracts whatever a name is near:
a LinkedIn URL, a GitHub URL, or a personal site. Never raises; unparseable
pages simply yield no leads (fail-soft, same convention as devpost_scraper.py).
"""

import re

from backend.scrapers.resolve import RawLead

NAME_RE = re.compile(r"\b[A-Z][a-zA-Z'\-]+(?:\s[A-Z][a-zA-Z'\-]+){1,2}\b")
LINKEDIN_RE = re.compile(
    r'https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+', re.IGNORECASE
)
GITHUB_RE = re.compile(
    r'https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))', re.IGNORECASE
)
PERSONAL_SITE_RE = re.compile(
    r'https?://(?!(?:www\.)?(?:linkedin|github|twitter|x)\.com)'
    r'[A-Za-z0-9.\-]+\.[a-z]{2,}(?:/[^\s"\'<>]*)?',
    re.IGNORECASE,
)
WINDOW = 300  # characters to look around a matched name for nearby links


def extract_leads(
    html: str,
    source: str,
    source_url: str = "",
    school: str | None = None,
    year: int | None = None,
    max_leads: int = 50,
) -> list[RawLead]:
    """Find name-like text, then look in a bounded window around it (in the
    ORIGINAL markup — link URLs live in href attributes, so tags are never
    stripped before this search) for a LinkedIn/GitHub/personal-site URL. A
    name with no nearby link anywhere on the page is dropped — too ambiguous
    to be worth a paid lookup downstream."""
    if not html:
        return []
    leads: list[RawLead] = []
    seen_names: set[str] = set()
    for match in NAME_RE.finditer(html):
        if len(leads) >= max_leads:
            break
        name = match.group(0).strip()
        key = name.lower()
        if key in seen_names:
            continue
        start = max(0, match.start() - WINDOW)
        end = min(len(html), match.end() + WINDOW)
        window = html[start:end]
        linkedin = _first(LINKEDIN_RE, window)
        github_match = GITHUB_RE.search(window)
        github = github_match.group(1) if github_match else None
        personal = _first(PERSONAL_SITE_RE, window) if not linkedin and not github else None
        if not (linkedin or github or personal):
            continue
        seen_names.add(key)
        leads.append(RawLead(
            name=name, source=source, source_url=source_url,
            school=school, year=year,
            linkedin_url=linkedin, github_username=github, personal_site=personal,
        ))
    return leads


def _first(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None
