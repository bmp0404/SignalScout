"""Coresignal adapter — employee_base v2: search-filter POST for candidate ids,
then a collect GET for the best match. Two credits per enrich (search + collect),
so the caller's cache/budget guardrails matter here.

Coresignal's `created_at` is when the record first entered THEIR database — a
first-seen proxy, not the true LinkedIn signup date. It maps to
`profile_created_at` and is treated as an upper bound on profile age.
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

API = "https://api.coresignal.com/cdapi/v2"


class CoresignalProvider(EnrichmentProvider):
    name = "coresignal"

    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"apikey": api_key, "Accept": "application/json"})

    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        filters: dict = {}
        if query.linkedin_url:
            # Shorthand (slug) match is the precise employee_base filter for a known profile.
            slug = query.linkedin_url.rstrip("/").rsplit("/", 1)[-1]
            filters["shorthand_name"] = slug
        elif query.name:
            filters["name"] = query.name
        else:
            return None

        ids = self._search(filters)
        if not ids:
            return None
        record = self._collect(ids[0])
        if not record:
            return None
        return self._map_person(record)

    def search_people(self, filters: dict) -> list[EnrichmentResult]:
        results = []
        for record_id in self._search(filters)[:10]:
            record = self._collect(record_id)
            if record:
                results.append(self._map_person(record))
        return results

    def _search(self, filters: dict) -> list:
        try:
            resp = self.session.post(f"{API}/employee_base/search/filter", json=filters, timeout=20)
            if resp.status_code != 200:
                logger.warning("Coresignal search -> %s: %s", resp.status_code, resp.text[:200])
                return []
            payload = resp.json()
            return payload if isinstance(payload, list) else []
        except requests.RequestException as exc:
            logger.warning("Coresignal search request failed: %s", exc)
            return []

    def _collect(self, record_id) -> dict | None:
        try:
            resp = self.session.get(f"{API}/employee_base/collect/{record_id}", timeout=20)
            if resp.status_code != 200:
                logger.warning("Coresignal collect %s -> %s: %s", record_id, resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except requests.RequestException as exc:
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
