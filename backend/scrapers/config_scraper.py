"""Shared scrape-from-JSON-source-list logic for the free (non-API) discovery
scrapers (fellowship_scraper.py, competition_scraper.py). Each subclass only
sets `name` and points at its own sources file — same config-file convention
as openalex_labs.py's targets_file, so URLs can be corrected without a code
change. Fail-soft throughout, same convention as devpost_scraper.py.
"""

import json
import logging
from pathlib import Path

import requests

from backend.scrapers.lead_extraction import extract_leads
from backend.scrapers.resolve import RawLead

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) signal-scout/0.1"


class ConfigSourceScraper:
    name = "config_source"

    def __init__(self, sources_file: Path, session: requests.Session | None = None):
        self.sources_file = sources_file
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def scrape(self, source_id: str | None = None, max_leads_per_source: int = 50) -> list[RawLead]:
        """Leads from one source (`source_id`) or every configured source."""
        leads: list[RawLead] = []
        for source in self._sources():
            if source_id and source["id"] != source_id:
                continue
            html = self._get(source["url"])
            if not html:
                continue
            leads.extend(
                extract_leads(
                    html, source=source["id"], source_url=source["url"],
                    max_leads=max_leads_per_source,
                )
            )
        return leads

    def _get(self, url: str) -> str | None:
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("%s source %s -> %s", self.name, url, resp.status_code)
                return None
            return resp.text
        except requests.RequestException as exc:
            logger.warning("%s source request failed %s: %s", self.name, url, exc)
            return None

    def _sources(self) -> list[dict]:
        try:
            return json.loads(self.sources_file.read_text()).get("sources", [])
        except (OSError, ValueError) as exc:
            logger.warning("%s sources file unavailable (%s): %s", self.name, self.sources_file, exc)
            return []
