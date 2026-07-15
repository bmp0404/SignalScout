"""Dump the top unknown discoveries to out/verify.md for a 10-minute manual pass.

For each candidate: name, GitHub, follower count, score, a short receipt of the
top signals, any email found, and a pre-built LinkedIn people-search URL so a
human can confirm the person is real and pre-breakout before they enter a digest.

Run: python scripts/verify_candidates.py [--top N]
"""

import argparse
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.container import Container


def linkedin_search_url(name: str, school: str | None) -> str:
    query = " ".join(filter(None, [name, school]))
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(query)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a hand-verification checklist for top unknowns.")
    parser.add_argument("--top", type=int, default=15, help="How many candidates to include.")
    args = parser.parse_args()

    container = Container()
    candidates = container.candidate_service.list_candidates("discovery")[: args.top]

    lines = [
        "# Verification checklist — top unknown discoveries",
        "",
        f"Auto-generated from the live cohort. Review the top {len(candidates)} before any outreach.",
        "Each is a candidate flagged by score; confirm they are a *real, pre-breakout* person.",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        gh = c.get("github_username")
        gh_url = f"https://github.com/{gh}" if gh else "—"
        followers = c.get("github_followers")
        followers_str = f"{followers:,}" if isinstance(followers, int) else "?"
        links = c.get("contact_links") or {}
        email = links.get("email", "").replace("mailto:", "") or "—"
        signal_bits = "; ".join(s.get("summary") or s["type"] for s in c.get("top_signals", [])) or "—"
        if links.get("linkedin"):
            linkedin_line = f"**LinkedIn (resolved):** {links['linkedin']}"
        else:
            linkedin_line = f"**LinkedIn search:** {linkedin_search_url(c['name'], c.get('school'))}"

        lines += [
            f"## {i}. {c['name']}  ·  score {round(c['score'])}",
            "",
            f"- [ ] Confirmed real & pre-breakout",
            f"- **GitHub:** {gh_url}  ({followers_str} followers)",
            f"- **School / area:** {c.get('school') or '—'} · {c.get('area') or '—'}",
            f"- **Location:** {c.get('current_location') or c.get('region') or '—'}",
            f"- **Top signals:** {signal_bits}",
            f"- **Orbit:** {c.get('connection_context') or '—'}",
            f"- **Email:** {email}",
            f"- {linkedin_line}",
            "",
        ]

    out_path = container.settings.out_dir / "verify.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    print(f"wrote {out_path} ({len(candidates)} candidates)")


if __name__ == "__main__":
    main()
