"""People Data Labs adapter — single GET to /v5/person/enrich per person.

Notes that shaped the mapping (verified live):
- Auth is the X-Api-Key header; a miss is HTTP 404 (not an error).
- `min_likelihood` guards against wrong-person merges; below it PDL 404s.
- PDL never exposes when a LinkedIn profile was created, so
  `profile_created_at` is always None here; `linkedin_connections` is the
  new-profile proxy the enricher uses instead.
- Free-tier plans obscure contact fields by replacing values with booleans
  (e.g. "work_email": true) — anything non-string is dropped, never merged.
"""

import logging

import requests

from backend.enrichment.providers.base import (
    Education,
    EnrichmentProvider,
    EnrichmentQuery,
    EnrichmentResult,
    Position,
    normalize_date,
)

logger = logging.getLogger(__name__)

API = "https://api.peopledatalabs.com/v5"
DEFAULT_MIN_LIKELIHOOD = 6  # PDL scale 0-10; >=6 is a confident single-person match


def _clean(value) -> str | None:
    """Free-tier PDL replaces obscured field values with booleans; keep strings only."""
    return value if isinstance(value, str) and value.strip() else None


class PdlProvider(EnrichmentProvider):
    name = "pdl"

    def __init__(self, api_key: str, min_likelihood: int = DEFAULT_MIN_LIKELIHOOD, session: requests.Session | None = None):
        self.min_likelihood = min_likelihood
        self.session = session or requests.Session()
        self.session.headers.update({"X-Api-Key": api_key, "Accept": "application/json"})

    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        params: dict = {"min_likelihood": self.min_likelihood, "titlecase": "true"}
        profiles = []
        if query.linkedin_url:
            profiles.append(query.linkedin_url)
        if query.github_username:
            profiles.append(f"github.com/{query.github_username}")
        if query.twitter_handle:
            profiles.append(f"twitter.com/{query.twitter_handle.lstrip('@')}")
        if profiles:
            params["profile"] = profiles
        if query.name:
            params["name"] = query.name
        if query.school:
            params["school"] = query.school
        if not profiles and not (query.name and query.school):
            # Name alone can't clear min_likelihood; don't burn a credit on it.
            return None

        data = self._get("/person/enrich", params)
        if not data:
            return None
        return self._map_person(data)

    def search_people(self, filters: dict) -> list[EnrichmentResult]:
        """Person Search via SQL syntax; `filters` maps column -> exact value."""
        if not filters:
            return []
        where = " AND ".join(f"{col} = '{val}'" for col, val in filters.items())
        body = {"sql": f"SELECT * FROM person WHERE {where}", "size": 10}
        try:
            resp = self.session.post(f"{API}/person/search", json=body, timeout=20)
            if resp.status_code != 200:
                logger.warning("PDL search -> %s: %s", resp.status_code, resp.text[:200])
                return []
            return [self._map_person(rec) for rec in resp.json().get("data", [])]
        except requests.RequestException as exc:
            logger.warning("PDL search request failed: %s", exc)
            return []

    def _get(self, path: str, params: dict) -> dict | None:
        try:
            resp = self.session.get(f"{API}{path}", params=params, timeout=20)
            if resp.status_code == 404:
                return None  # no confident match — normal, not an error
            if resp.status_code == 402:
                logger.warning("PDL out of credits (402) — enrichment skipped")
                return None
            if resp.status_code != 200:
                logger.warning("PDL %s -> %s: %s", path, resp.status_code, resp.text[:200])
                return None
            payload = resp.json()
            likelihood = payload.get("likelihood", 0)
            if likelihood < self.min_likelihood:
                return None
            return payload.get("data")
        except requests.RequestException as exc:
            logger.warning("PDL request failed %s: %s", path, exc)
            return None

    def _map_person(self, data: dict) -> EnrichmentResult:
        education = []
        for edu in data.get("education") or []:
            school = (edu.get("school") or {}).get("name") if isinstance(edu.get("school"), dict) else None
            if not _clean(school):
                continue
            degrees = edu.get("degrees") or []
            majors = edu.get("majors") or []
            education.append(
                Education(
                    school=school,
                    degree=_clean(degrees[0]) if degrees else None,
                    field_of_study=_clean(majors[0]) if majors else None,
                    start_date=normalize_date(edu.get("start_date")),
                    end_date=normalize_date(edu.get("end_date")),
                )
            )

        positions = []
        for exp in data.get("experience") or []:
            company = (exp.get("company") or {}).get("name") if isinstance(exp.get("company"), dict) else None
            title = (exp.get("title") or {}).get("name") if isinstance(exp.get("title"), dict) else None
            positions.append(
                Position(
                    company=_clean(company),
                    title=_clean(title),
                    start_date=normalize_date(exp.get("start_date")),
                    end_date=normalize_date(exp.get("end_date")),
                    is_current=bool(exp.get("is_primary")) or exp.get("end_date") is None,
                )
            )

        linkedin = _clean(data.get("linkedin_url"))
        if linkedin and not linkedin.startswith("http"):
            linkedin = f"https://{linkedin}"
        connections = data.get("linkedin_connections")
        return EnrichmentResult(
            linkedin_url=linkedin,
            headline=_clean(data.get("headline")) or _clean(data.get("job_title_name")) or _clean(data.get("job_title")),
            education=education,
            positions=positions,
            profile_created_at=None,  # PDL doesn't expose profile age
            location=_clean(data.get("location_name")),
            connections=connections if isinstance(connections, int) else None,
            raw={
                "provider": self.name,
                "full_name": _clean(data.get("full_name")),
                "linkedin_url": linkedin,
                "job_title": _clean(data.get("job_title")),
                "job_company_name": _clean(data.get("job_company_name")),
                "location_name": _clean(data.get("location_name")),
                "linkedin_connections": connections if isinstance(connections, int) else None,
            },
        )
