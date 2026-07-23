"""Exa AI adapter — semantic web people-search as an independent LEAD lane.

Exa (https://exa.ai) runs a neural search over 1B+ public professional profiles.
Unlike PDL/Coresignal this is a SEARCH-ONLY source: `enrich_person` is a no-op,
so PDL remains the one-person contact enricher. `search_page` issues one
`POST /search` with `category="people"` and maps each person entity into an
`EnrichmentResult` the discovery lane can dedupe + admit (at review tier).

Fail-soft like every other provider: auth/network/HTTP errors set `last_error`
and return an empty page; nothing here raises into the pipeline. No linkedin.com
scraping — Exa returns its own licensed/derived index.
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

API_URL = "https://api.exa.ai/search"
# Exa bills per request with up to 10 results; >25 results costs more, so cap
# conservatively. numResults 1-25 keeps each search on the base tier.
MAX_SEARCH_SIZE = 25


def _clean(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class ExaProvider(EnrichmentProvider):
    name = "exa"
    # The recipe's natural-language prompt is the only "filter" Exa needs; the
    # rest are optional passthroughs. Only these keys survive _effective_filters.
    supported_search_filters = frozenset({"query", "category", "num_results"})

    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.session.headers.update(
            {"x-api-key": api_key, "Content-Type": "application/json"}
        )

    def enrich_person(self, query: EnrichmentQuery) -> EnrichmentResult | None:
        """Exa is a discovery/search source only — never a one-person enricher."""
        self.last_error = None
        return None

    def search_people(self, filters: dict, size: int = 10) -> list[EnrichmentResult]:
        return self.search_page(filters, size=size).results

    def search_page(
        self,
        filters: dict,
        size: int = 10,
        cursor: str | None = None,
    ) -> ProviderSearchPage:
        """One `POST /search` with `category="people"`. Exa search returns a
        single ranked page (no resumable cursor here), so every page is
        `exhausted=True`; `credit_units` counts returned records like PDL."""
        self.last_error = None
        query = _clean((filters or {}).get("query"))
        if not query:
            return ProviderSearchPage()
        num = max(1, min(int(size), MAX_SEARCH_SIZE))
        body = {
            "query": query,
            "category": (filters or {}).get("category", "people"),
            "type": "auto",
            "numResults": num,
        }
        try:
            resp = self.session.post(API_URL, json=body, timeout=30)
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}"
                logger.warning("Exa search -> %s: %s", resp.status_code, resp.text[:200])
                return ProviderSearchPage(api_requests=1)
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            self.last_error = str(exc)
            logger.warning("Exa search request failed: %s", exc)
            return ProviderSearchPage(api_requests=1)

        records = payload.get("results") or []
        results = [self._map_result(record) for record in records]
        results = [record for record in results if record is not None]
        return ProviderSearchPage(
            results=results,
            next_cursor=None,
            exhausted=True,
            api_requests=1,
            returned_records=len(records),
            credit_units=len(records),
        )

    def _map_result(self, record: dict) -> EnrichmentResult | None:
        entity = self._person_entity(record)
        properties = entity.get("properties", {}) if entity else {}
        name = (
            _clean(properties.get("name"))
            or self._joined_name(properties)
            or _clean(record.get("author"))
            or _clean(record.get("title"))
        )
        if not name:
            return None
        url = _clean(record.get("url"))
        linkedin = url if url and "linkedin.com" in url.lower() else None
        provider_person_id = (
            _clean(entity.get("id") if entity else None)
            or _clean(record.get("id"))
            or url
        )
        positions = self._positions(properties.get("workHistory") or [])
        education = self._education(properties.get("educationHistory") or [])
        headline = self._headline(positions) or _clean(record.get("title"))
        summary = _clean(record.get("summary"))
        highlights = record.get("highlights") or []
        if not summary and highlights:
            summary = _clean(highlights[0])
        return EnrichmentResult(
            linkedin_url=linkedin,
            headline=headline,
            education=education,
            positions=positions,
            profile_created_at=None,
            location=_clean(properties.get("location")),
            provider=self.name,
            provider_person_id=provider_person_id,
            full_name=name,
            raw={
                "source": "exa",
                "url": url,
                "title": _clean(record.get("title")),
                "summary": summary,
                "headline": headline,
            },
        )

    @staticmethod
    def _person_entity(record: dict) -> dict | None:
        for entity in record.get("entities") or []:
            if isinstance(entity, dict) and entity.get("type") == "person":
                return entity
        return None

    @staticmethod
    def _joined_name(properties: dict) -> str | None:
        parts = [
            _clean(properties.get("firstName")),
            _clean(properties.get("lastName")),
        ]
        joined = " ".join(part for part in parts if part)
        return joined or None

    @staticmethod
    def _positions(work_history: list) -> list[Position]:
        positions: list[Position] = []
        for role in work_history:
            if not isinstance(role, dict):
                continue
            company = role.get("company") or {}
            dates = role.get("dates") or {}
            positions.append(
                Position(
                    company=_clean(company.get("name")) if isinstance(company, dict) else None,
                    title=_clean(role.get("title")),
                    start_date=normalize_date((dates or {}).get("from")),
                    end_date=normalize_date((dates or {}).get("to")),
                    is_current=bool(dates) and (dates.get("to") in (None, "")),
                )
            )
        return positions

    @staticmethod
    def _education(education_history: list) -> list[Education]:
        education: list[Education] = []
        for item in education_history:
            if not isinstance(item, dict):
                continue
            institution = item.get("institution") or {}
            school = _clean(institution.get("name")) if isinstance(institution, dict) else None
            if not school:
                continue
            dates = item.get("dates") or {}
            education.append(
                Education(
                    school=school,
                    degree=_clean(item.get("degree")),
                    field_of_study=None,
                    start_date=normalize_date((dates or {}).get("from")),
                    end_date=normalize_date((dates or {}).get("to")),
                )
            )
        return education

    @staticmethod
    def _headline(positions: list[Position]) -> str | None:
        for position in positions:
            if position.is_current and (position.title or position.company):
                bits = [b for b in (position.title, "at" if position.company else None, position.company) if b]
                return " ".join(bits) or None
        if positions:
            first = positions[0]
            bits = [b for b in (first.title, "at" if first.company else None, first.company) if b]
            return " ".join(bits) or None
        return None
