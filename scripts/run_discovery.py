"""Live graph expansion from seed accounts (requires GITHUB_TOKEN), plus
Semantic Scholar co-author and Devpost teammate enrichment for the discovery
cohort. Seeded discoveries are loaded by build_db.py, so this is additive.

Defaults are scoped small so a run finishes in minutes:
Run: GITHUB_TOKEN=... python scripts/run_discovery.py
     [--seed-limit 3] [--max-per-seed 20] [--scholar-limit 8] [--devpost-limit 8]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.container import Container
from backend.discovery.graph_expansion import GraphExpander
from backend.domain.graph_edge import GraphEdge
from backend.domain.person import Person
from backend.domain.signal import Signal
from backend.scrapers.devpost_scraper import DevpostScraper
from backend.scrapers.github_scraper import GithubClient, GithubScraper
from backend.scrapers.semantic_scholar import SemanticScholarScraper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scoped live discovery run")
    parser.add_argument("--seed-limit", type=int, default=3,
                        help="seed accounts to expand from (small = minutes, not hours)")
    parser.add_argument("--max-per-seed", type=int, default=20,
                        help="follow-edge candidates pulled per seed, each direction")
    parser.add_argument("--scholar-limit", type=int, default=8,
                        help="discovery people to look up on Semantic Scholar")
    parser.add_argument("--devpost-limit", type=int, default=8,
                        help="discovery people to look up on Devpost (via GitHub username)")
    return parser.parse_args()


def save_collected(container: Container, signals: list[Signal], edges: list[GraphEdge]) -> None:
    """Feed new signals/edges through the existing EntityResolver -> repos path."""
    if signals:
        container.resolver.resolve_signals(signals)
        container.signals.save_many(signals)
    if edges:
        container.resolver.resolve_edges(edges)
        container.edges.save_many(edges)


def has_source(container: Container, person: Person, source: str) -> bool:
    """Idempotency guard: skip people already enriched from this source."""
    return any(s.source == source for s in container.signals.for_person(person.id))


def main() -> None:
    args = parse_args()
    container = Container()
    token = container.settings.github_token
    if not token:
        print("GITHUB_TOKEN not set — live discovery skipped (seeded discoveries already loaded).")
        return

    seeds = json.loads(container.settings.seed_accounts_file.read_text())["github_seeds"]
    seeds = seeds[: args.seed_limit]
    scraper = GithubScraper(GithubClient(token), [])
    expander = GraphExpander(scraper, container.persons, container.edges)
    discovered = expander.expand(seeds, max_per_seed=args.max_per_seed)
    print(f"discovered {len(discovered)} new candidates from {len(seeds)} seeds")

    for person in discovered:
        signals = scraper.scrape_user(person.github_username)
        container.resolver.resolve_signals(signals)
        container.signals.save_many(signals)
        container.contact_enricher.enrich(person, signals)
        container.location_resolver.resolve(person, signals)
        container.persons.save(person)

    # New-source enrichment: discovery cohort ONLY (founders stay on curated
    # pre-breakout signals — protects the backtest). Fresh discoveries first.
    discovered_ids = {p.id for p in discovered}
    pool = discovered + [p for p in container.persons.all("discovery") if p.id not in discovered_ids]

    scholar = SemanticScholarScraper()
    checked = found = 0
    for person in pool:
        if checked >= args.scholar_limit:
            break
        if not scholar.has_real_name(person) or has_source(container, person, "semantic_scholar"):
            continue
        checked += 1
        signals, edges = scholar.collect(person)
        if signals:
            found += 1
        save_collected(container, signals, edges)
    print(f"semantic scholar: {found}/{checked} people with co-authored papers")

    devpost = DevpostScraper()
    checked = found = 0
    for person in pool:
        if checked >= args.devpost_limit:
            break
        if not person.github_username or has_source(container, person, "devpost"):
            continue
        checked += 1
        signals, edges = devpost.collect(person, person.github_username)
        if signals or edges:
            found += 1
        save_collected(container, signals, edges)
    print(f"devpost: {found}/{checked} people with hackathon footprint")

    container.candidate_service.rescore_all()
    print("scored — review discoveries in the dashboard, then manually verify contacts for digest picks")


if __name__ == "__main__":
    main()
