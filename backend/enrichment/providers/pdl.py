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
    ProviderSearchPage,
    normalize_date,
)

logger = logging.getLogger(__name__)

API = "https://api.peopledatalabs.com/v5"
DEFAULT_MIN_LIKELIHOOD = 6  # PDL scale 0-10; >=6 is a confident single-person match

# Allowlisted search filters: our filter key -> PDL person-schema column. Only
# these columns can ever reach the query, so filter values are never interpolated
# into an arbitrary column reference (values are still escaped, see _escape).
SEARCH_COLUMNS = {
    "school": "education.school.name",
    "major": "education.majors",
    "degree": "education.degrees",
    "location": "location_locality",
    "region": "location_region",
    "country": "location_country",
    "title_role": "job_title_role",
    "title_level": "job_title_levels",
    "industry": "industry",
    "title": "job_title",  # free-text title match, e.g. founder/co-founder/ceo
    "company_size": "job_company_size",  # PDL bucket, e.g. "1-10"
    "skill": "skills",
}
SEARCH_RANGE_COLUMNS = {
    "education_end_date_gte": ("education.end_date", ">="),
    "birth_year_gte": ("birth_year", ">="),
    "job_start_date_gte": ("job_start_date", ">="),
}
MAX_SEARCH_SIZE = 100


def _clean(value) -> str | None:
    """Free-tier PDL replaces obscured field values with booleans; keep strings only."""
    return value if isinstance(value, str) and value.strip() else None


class PdlProvider(EnrichmentProvider):
    name = "pdl"
    supported_search_filters = frozenset((*SEARCH_COLUMNS, *SEARCH_RANGE_COLUMNS))

    def __init__(self, api_key: str, min_likelihood: int = DEFAULT_MIN_LIKELIHOOD, session: requests.Session | None = None):
        self.min_likelihood = min_likelihood
        self.session = session or requests.Session()
        self.session.headers.update({"X-Api-Key": api_key, "Accept": "application/json"})

    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        self.last_error = None
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

    def search_people(self, filters: dict, size: int = 10) -> list[EnrichmentResult]:
        return self.search_page(filters, size=size).results

    def search_page(
        self,
        filters: dict,
        size: int = 10,
        cursor: str | None = None,
    ) -> ProviderSearchPage:
        """Person Search via SQL. `filters` keys are ALLOWLISTED (SEARCH_COLUMNS);
        unknown keys are ignored and values are escaped — never interpolated as
        arbitrary column references. PDL v5 deprecated the ``from`` offset in
        favor of scroll_token-based pagination: the first request omits it, the
        response's `scroll_token` is echoed back verbatim as `cursor` to resume."""
        self.last_error = None
        where = self._build_where(filters)
        if not where:
            return ProviderSearchPage()
        limit = max(1, min(size, MAX_SEARCH_SIZE))
        body = {
            "sql": f"SELECT * FROM person WHERE {where}",
            "size": limit,
        }
        if cursor:
            body["scroll_token"] = cursor
        try:
            resp = self.session.post(f"{API}/person/search", json=body, timeout=20)
            if resp.status_code == 404:
                return ProviderSearchPage(api_requests=1)  # no matches — definitive
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("PDL search -> %s: %s", resp.status_code, resp.text[:200])
                return ProviderSearchPage(api_requests=1)
            payload = resp.json()
            records = payload.get("data", [])
            results = [self._map_person(rec) for rec in records]
            scroll_token = payload.get("scroll_token")
            has_more = bool(scroll_token) and len(records) == limit
            return ProviderSearchPage(
                results=results,
                next_cursor=scroll_token if has_more else None,
                exhausted=not has_more,
                api_requests=1,
                returned_records=len(records),
                # PDL search usage is tracked by records returned, separately
                # from the single HTTP request.
                credit_units=len(records),
            )
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("PDL search request failed: %s", exc)
            return ProviderSearchPage(api_requests=1)

    def _build_where(self, filters: dict) -> str:
        clauses: list[str] = []
        for key, value in (filters or {}).items():
            column = SEARCH_COLUMNS.get(key)
            range_column = SEARCH_RANGE_COLUMNS.get(key)
            if range_column and value:
                column, operator = range_column
                clauses.append(f"{column} {operator} '{self._escape(value)}'")
                continue
            if not column:
                logger.warning("PDL search: ignoring unsupported filter %r", key)
                continue
            if isinstance(value, (list, tuple)):
                escaped = [f"'{self._escape(v)}'" for v in value if v]
                if escaped:
                    clauses.append(f"{column} IN ({', '.join(escaped)})")
            elif value:
                clauses.append(f"{column} = '{self._escape(value)}'")
        return " AND ".join(clauses)

    @staticmethod
    def _escape(value) -> str:
        """SQL-escape a filter value: strip control chars, double single quotes."""
        text = "".join(ch for ch in str(value) if ch >= " " and ch != "\x7f")
        return text.replace("'", "''")

    def _get(self, path: str, params: dict) -> dict | None:
        try:
            resp = self.session.get(f"{API}{path}", params=params, timeout=20)
            if resp.status_code == 404:
                return None  # no confident match — a definitive, cacheable miss
            if resp.status_code != 200:
                # 401 bad key / 402 out of credits / 429 / 5xx: transient or
                # account-level — surface via last_error so it is never cached.
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("PDL %s -> %s: %s", path, resp.status_code, resp.text[:200])
                return None
            payload = resp.json()
            likelihood = payload.get("likelihood", 0)
            if likelihood < self.min_likelihood:
                return None
            return payload.get("data")
        except requests.RequestException as exc:
            self.last_error = str(exc)
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
            provider=self.name,
            provider_person_id=_clean(data.get("id")) or _clean(data.get("pdl_id")),
            full_name=_clean(data.get("full_name")),
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
