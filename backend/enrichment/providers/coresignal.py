"""Coresignal adapter — employee_base v2.

A known profile uses the documented collect-by-shorthand endpoint. Otherwise,
search-filter POST returns candidate ids and collect GET fetches the best match.
Name searches use Coresignal's documented ``full_name`` filter and include the
school when available to reduce false-positive merges.

Coresignal's `created_at` is when the record first entered THEIR database — a
first-seen proxy, not the true LinkedIn signup date. It maps to
`profile_created_at` and is treated as an upper bound on profile age.
"""

import json
import logging
from urllib.parse import quote, urlparse

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

API = "https://api.coresignal.com/cdapi/v2"

# Allowlisted search filters: our filter key -> Coresignal employee_base filter.
# Only these documented filters can reach the request body.
SEARCH_FILTERS = {
    "school": "education_institution_name",
    "title": "active_experience_title",
    "location": "location",
    "country": "location_country",
    "created_at_gte": "created_at_gte",  # first-seen lower bound (recent profiles)
    # Founder-recipe fields — verify exact keys against the live employee_base
    # schema before relying on them; unsupported keys are silently dropped.
    "previous_company": "experience_company_name",  # e.g. FAANG-to-startup match
    "company_size_lte": "active_experience_company_employees_count_lte",
    "company_founded_gte": "active_experience_company_founded_year_gte",
}
MAX_SEARCH_SIZE = 100

# company_base collection filters (company-first discovery step 1).
COMPANY_SEARCH_FILTERS = {
    "founded_gte": "founded_year_gte",
    "employees_count_lte": "employees_count_lte",
    "industry": "industry",
}


class CoresignalProvider(EnrichmentProvider):
    name = "coresignal"
    supported_search_filters = frozenset(SEARCH_FILTERS)
    search_credit_overhead = 1

    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"apikey": api_key, "Accept": "application/json"})

    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        self.last_error = None
        if query.linkedin_url:
            path = urlparse(query.linkedin_url).path.rstrip("/")
            shorthand = path.rsplit("/", 1)[-1]
            if not shorthand:
                return None
            record = self._collect(shorthand)
            return self._map_person(record) if record else None

        if not query.name:
            return None
        filters = {"full_name": query.name}
        if query.school:
            filters["education_institution_name"] = query.school

        ids = self._search(filters)
        if not ids:
            return None
        record = self._collect(ids[0])
        if not record:
            return None
        return self._map_person(record)

    def search_people(self, filters: dict, size: int = 10) -> list[EnrichmentResult]:
        return self.search_page(filters, size=size).results

    def search_page(
        self,
        filters: dict,
        size: int = 10,
        cursor: str | None = None,
    ) -> ProviderSearchPage:
        """Independent Coresignal search. `filters` keys are ALLOWLISTED
        (SEARCH_FILTERS); unknown keys are ignored so no arbitrary filter reaches
        the request. New checkpoints carry the returned ID list in an opaque
        cursor, avoiding a repeat filter request on resume. Numeric cursors from
        older checkpoints remain supported and safely re-fetch the ID list."""
        self.last_error = None
        allowed = self._build_filters(filters)
        if not allowed:
            return ProviderSearchPage()
        limit = max(1, min(size, MAX_SEARCH_SIZE))
        offset, ids = self._resume_state(cursor)
        search_requests = 0
        if ids is None:
            ids = self._search(allowed)
            search_requests = 1
            if self.last_error:
                return ProviderSearchPage(api_requests=1)
        selected = ids[offset:offset + limit]
        results = []
        for record_id in selected:
            record = self._collect(record_id)
            if record:
                results.append(self._map_person(record))
        has_more = offset + len(selected) < len(ids)
        return ProviderSearchPage(
            results=results,
            next_cursor=(
                json.dumps(
                    {"offset": offset + len(selected), "ids": ids},
                    separators=(",", ":"),
                )
                if has_more
                else None
            ),
            exhausted=not has_more,
            api_requests=search_requests + len(selected),
            returned_records=len(results),
            # This is an internal conservative request-unit ledger, not a claim
            # about Coresignal invoice semantics.
            credit_units=search_requests + len(selected),
            search_credits=search_requests,
            collect_credits=len(selected),
        )

    @staticmethod
    def _resume_state(cursor: str | None) -> tuple[int, list | None]:
        if not cursor:
            return 0, None
        try:
            payload = json.loads(cursor)
            if isinstance(payload, dict) and isinstance(payload.get("ids"), list):
                return max(0, int(payload.get("offset", 0))), payload["ids"]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        try:
            return max(0, int(cursor)), None
        except (TypeError, ValueError):
            return 0, None

    @staticmethod
    def _build_filters(filters: dict) -> dict:
        allowed = {}
        for key, value in (filters or {}).items():
            column = SEARCH_FILTERS.get(key)
            if not column:
                logger.warning("Coresignal search: ignoring unsupported filter %r", key)
                continue
            if value:
                allowed[column] = value
        return allowed

    def _search(self, filters: dict) -> list:
        try:
            resp = self.session.post(f"{API}/employee_base/search/filter", json=filters, timeout=20)
            if resp.status_code == 404:
                return []  # definitive no-match — cacheable
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"  # auth/credits/5xx: never cache
                logger.warning("Coresignal search -> %s: %s", resp.status_code, resp.text[:200])
                return []
            payload = resp.json()
            return payload if isinstance(payload, list) else []
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("Coresignal search request failed: %s", exc)
            return []

    def search_companies(self, filters: dict, size: int = 10) -> list[dict]:
        """Company-first discovery step 1: seed-stage companies via company_base
        search + collect. Returns raw company records (id, name, employee
        count) — not EnrichmentResult, since companies aren't people."""
        self.last_error = None
        allowed = {}
        for key, value in (filters or {}).items():
            column = COMPANY_SEARCH_FILTERS.get(key)
            if not column:
                logger.warning("Coresignal company search: ignoring unsupported filter %r", key)
                continue
            if value:
                allowed[column] = value
        if not allowed:
            return []
        try:
            resp = self.session.post(f"{API}/company_base/search/filter", json=allowed, timeout=20)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("Coresignal company search -> %s: %s", resp.status_code, resp.text[:200])
                return []
            payload = resp.json()
            ids = payload if isinstance(payload, list) else []
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("Coresignal company search request failed: %s", exc)
            return []
        companies = []
        for company_id in ids[:size]:
            record = self._collect_company(company_id)
            if record:
                companies.append(record)
        return companies

    def search_company_employees(
        self, company_id, title_filters: dict, size: int = 10
    ) -> list[EnrichmentResult]:
        """Company-first discovery step 2: employee_base search scoped to one
        company (an internal join key, not part of the allowlisted recipe
        filters) for founder/co-founder/CEO titles."""
        self.last_error = None
        allowed = self._build_filters(title_filters)
        allowed["active_experience_company_id"] = company_id
        ids = self._search(allowed)
        if not ids:
            return []
        results = []
        for record_id in ids[:size]:
            record = self._collect(record_id)
            if record:
                results.append(self._map_person(record))
        return results

    def _collect_company(self, company_id) -> dict | None:
        try:
            encoded_id = quote(str(company_id), safe="")
            resp = self.session.get(f"{API}/company_base/collect/{encoded_id}", timeout=20)
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("Coresignal company collect %s -> %s: %s", company_id, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("Coresignal company collect request failed: %s", exc)
            return None
        return {
            "id": data.get("id", company_id),
            "name": data.get("company_name") or data.get("name"),
            "employees_count": data.get("employees_count"),
        }

    def _collect(self, record_id) -> dict | None:
        try:
            encoded_id = quote(str(record_id), safe="")
            resp = self.session.get(f"{API}/employee_base/collect/{encoded_id}", timeout=20)
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("Coresignal collect %s -> %s: %s", record_id, resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except requests.RequestException as exc:
            self.last_error = str(exc)
            logger.warning("Coresignal collect request failed: %s", exc)
            return None

    def _map_person(self, data: dict) -> EnrichmentResult:
        education = []
        for edu in data.get("education") or data.get("member_education_collection") or []:
            school = edu.get("institution_name") or edu.get("title") or edu.get("school_name")
            if not school:
                continue
            education.append(
                Education(
                    school=school,
                    degree=edu.get("degree"),
                    field_of_study=edu.get("field_of_study") or edu.get("subtitle"),
                    start_date=normalize_date(edu.get("date_from") or edu.get("start_date")),
                    end_date=normalize_date(edu.get("date_to") or edu.get("end_date")),
                )
            )

        positions = []
        for exp in data.get("experience") or data.get("member_experience_collection") or []:
            end = normalize_date(exp.get("date_to") or exp.get("end_date"))
            positions.append(
                Position(
                    company=exp.get("company_name"),
                    title=exp.get("position_title") or exp.get("title"),
                    start_date=normalize_date(exp.get("date_from") or exp.get("start_date")),
                    end_date=end,
                    is_current=end is None,
                )
            )

        linkedin = data.get("linkedin_url") or data.get("url") or data.get("profile_url")
        if linkedin and not linkedin.startswith("http"):
            linkedin = f"https://{linkedin}"
        connections = data.get("connections_count") or data.get("connections")
        return EnrichmentResult(
            linkedin_url=linkedin,
            headline=data.get("headline") or data.get("title"),
            education=education,
            positions=positions,
            # First seen in Coresignal's DB — upper bound on profile age.
            profile_created_at=normalize_date(data.get("created_at") or data.get("created")),
            location=data.get("location") or data.get("location_full"),
            connections=connections if isinstance(connections, int) else None,
            provider=self.name,
            provider_person_id=str(data["id"]) if data.get("id") is not None else None,
            full_name=data.get("full_name") or data.get("name"),
            raw={
                "provider": self.name,
                "id": data.get("id"),
                "full_name": data.get("full_name") or data.get("name"),
                "linkedin_url": linkedin,
                "headline": data.get("headline") or data.get("title"),
                "location": data.get("location"),
                "created_at": data.get("created_at") or data.get("created"),
                "last_updated": data.get("last_updated") or data.get("last_updated_at"),
            },
        )
