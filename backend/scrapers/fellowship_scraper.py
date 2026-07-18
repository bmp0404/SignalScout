"""Fellowship-page scraper: Z Fellows, Thiel Fellowship, Neo Scholars,
1517 Fund, Contrary Talent, Interact Fellowship. Source list lives in
data/fellowship_sources.json.
"""

from backend.scrapers.config_scraper import ConfigSourceScraper


class FellowshipScraper(ConfigSourceScraper):
    name = "fellowship"
