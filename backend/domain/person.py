"""Person domain model (spec §6 plus contact/location extensions)."""

import uuid
from dataclasses import dataclass, field


@dataclass
class Person:
    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    aliases: list[str] = field(default_factory=list)
    cohort: str = "unknown"  # founder | control | discovery | seed | demo | unknown

    github_username: str | None = None
    twitter_handle: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    personal_site: str | None = None
    contact_info: dict = field(default_factory=dict)  # catch-all: search URLs, secondary emails, etc.

    school: str | None = None
    graduation_year: int | None = None
    origin_location: str | None = None
    current_location: str | None = None
    region: str | None = None

    fellowship: str | None = None
    breakout_date: str | None = None  # ISO date; only set for ground-truth founders
    area: str | None = None  # e.g. "AI Research", "Crypto", "Dev Tools"
    thesis: str | None = None  # one-line summary for cards/digest

    score: float | None = None
    needs_review: bool = False
    discovery_origin: str | None = None
    discovery_source: str | None = None  # e.g. "pdl_discovery", "coresignal_discovery"
    evidence_tier: str | None = None
    review_required: bool = False
    enrichment_status: str | None = None
    enrichment_provider: str | None = None
    enrichment_updated_at: str | None = None
    notes: str | None = None

    def display_contacts(self) -> dict[str, str]:
        contacts: dict[str, str] = {}
        if self.github_username:
            contacts["github"] = f"https://github.com/{self.github_username}"
        if self.linkedin_url:
            contacts["linkedin"] = self.linkedin_url
        if self.twitter_handle:
            contacts["x"] = f"https://x.com/{self.twitter_handle.lstrip('@')}"
        if self.email:
            contacts["email"] = f"mailto:{self.email}"
        if self.personal_site:
            contacts["site"] = self.personal_site
        return contacts
