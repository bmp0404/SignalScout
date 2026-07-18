"""Competition-results scraper: USACO, IMO, IOI, Putnam, Regeneron STS.
Devpost is intentionally excluded — it already has a dedicated teammate-graph
scraper (devpost_scraper.py). Source list lives in
data/competition_sources.json.
"""

from backend.scrapers.config_scraper import ConfigSourceScraper


class CompetitionScraper(ConfigSourceScraper):
    name = "competition"
