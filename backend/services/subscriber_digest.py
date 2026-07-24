"""Build and deliver personalized, never-repeat subscriber digests."""

import html
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from backend.db.repositories.subscriptions import (
    DigestSendRepository,
    SubscriberRepository,
)
from backend.digest.sender import EmailMessage, EmailSender
from backend.domain.subscriber import Subscriber
from backend.security.email_actions import EmailActionSigner
from backend.services.candidate_service import CandidateService

# How long to wait between digests for each cadence. A subscriber is "due" only
# when their most recent successful send is older than this window, which makes
# run_due idempotent: re-ticking (or a redundant Railway cron) never double-sends.
FREQUENCY_INTERVALS = {
    "daily": timedelta(days=1),
    "every_3_days": timedelta(days=3),
    "weekly": timedelta(days=7),
}
DEFAULT_FREQUENCY = "every_3_days"
DEFAULT_INTERVAL = FREQUENCY_INTERVALS[DEFAULT_FREQUENCY]


class SubscriberDigestService:
    def __init__(
        self,
        subscribers: SubscriberRepository,
        sends: DigestSendRepository,
        candidates: CandidateService,
        sender: EmailSender,
        public_base_url: str,
        action_signer: EmailActionSigner,
        size: int = 10,
    ):
        self.subscribers = subscribers
        self.sends = sends
        self.candidates = candidates
        self.sender = sender
        self.public_base_url = public_base_url.rstrip("/")
        self.action_signer = action_signer
        self.size = size

    def build(
        self,
        subscriber: Subscriber,
        all_candidates: list[dict] | None = None,
    ) -> tuple[EmailMessage, list[str]]:
        # The discovery candidate pool is identical for every subscriber, so a
        # caller sending to many subscribers computes it once and passes it in;
        # only the per-subscriber sent-ledger and preference sort differ.
        candidates = (
            all_candidates
            if all_candidates is not None
            else self.candidates.list_candidates("discovery")
        )
        sent_ids = self.sends.sent_person_ids(subscriber.id)
        picks, provisional_ids = self._select_picks(candidates, sent_ids, subscriber=subscriber)
        today = datetime.now(timezone.utc).date().isoformat()
        subject = f"Signal Scout — {len(picks)} people to know ({today})"
        return (
            EmailMessage(
                subject=subject,
                html=self._render_html(subscriber, picks, today, provisional_ids),
                text=self._render_text(subscriber, picks, today, provisional_ids),
            ),
            [candidate["id"] for candidate in picks],
        )

    def _select_picks(
        self,
        candidates: list[dict],
        exclude_ids: set[str],
        subscriber: Subscriber | None = None,
    ) -> tuple[list[dict], set[str]]:
        """Pick up to `size` people not in `exclude_ids`.

        Primary pool is operator-approved + contactable, newest approvals first
        (then subscriber preference/score). If that runs short, backfill with the
        top-scored verified-tier contactable candidates so a digest never goes
        empty between review sessions; those backfilled ids are returned so the
        renderer can mark them as provisional (awaiting operator review)."""
        approved = [
            candidate
            for candidate in candidates
            if candidate["id"] not in exclude_ids
            and candidate.get("approval_state") == "approved"
            and candidate.get("contactable")
        ]
        approved.sort(
            key=lambda candidate: (
                candidate.get("approved_at") or "",
                self._preference_rank(candidate, subscriber)
                if subscriber is not None
                else (0, float(candidate.get("score") or 0)),
            ),
            reverse=True,
        )
        picks = approved[: self.size]
        provisional_ids: set[str] = set()
        if len(picks) < self.size:
            chosen = {candidate["id"] for candidate in picks}
            # Backfill candidates are not yet operator-approved, so they won't carry
            # the review `contactable` flag; gate them on real contact links instead.
            fallback = [
                candidate
                for candidate in candidates
                if candidate["id"] not in exclude_ids
                and candidate["id"] not in chosen
                and candidate.get("evidence_tier") == "verified"
                and self._has_contacts(candidate)
            ]
            fallback.sort(key=lambda candidate: float(candidate.get("score") or 0), reverse=True)
            for candidate in fallback[: self.size - len(picks)]:
                picks.append(candidate)
                provisional_ids.add(candidate["id"])
        return picks, provisional_ids

    @staticmethod
    def _has_contacts(candidate: dict) -> bool:
        links = candidate.get("contact_links") or {}
        return sum(bool(links.get(key)) for key in ("github", "linkedin", "x", "email", "site")) >= 2

    def upcoming(self) -> dict:
        """Operator/Cory-facing preview of the digest lineup. Shows up to `size`
        approved + contactable people, ordering not-yet-featured people first so
        the preview rotates forward as automated sends fire — but, unlike a
        per-subscriber email (which never repeats), it keeps filling with
        already-featured approved people so the tab stays full instead of
        emptying out once most of the pool has gone out. Verified-tier candidates
        backfill any remaining slots, flagged provisional."""
        self.candidates.rescore_all()
        candidates = self.candidates.list_candidates("discovery")
        featured = self.sends.all_sent_person_ids()
        approved = [
            candidate
            for candidate in candidates
            if candidate.get("approval_state") == "approved" and candidate.get("contactable")
        ]
        approved.sort(
            key=lambda candidate: (
                candidate["id"] not in featured,  # unfeatured first (rotation front)
                candidate.get("approved_at") or "",
                float(candidate.get("score") or 0),
            ),
            reverse=True,
        )
        picks = approved[: self.size]
        provisional_ids: set[str] = set()
        if len(picks) < self.size:
            chosen = {candidate["id"] for candidate in picks}
            fallback = [
                candidate
                for candidate in candidates
                if candidate["id"] not in chosen
                and candidate.get("evidence_tier") == "verified"
                and self._has_contacts(candidate)
            ]
            fallback.sort(key=lambda candidate: float(candidate.get("score") or 0), reverse=True)
            for candidate in fallback[: self.size - len(picks)]:
                picks.append(candidate)
                provisional_ids.add(candidate["id"])
        entries = [
            self._entry(candidate, candidate["id"] in provisional_ids)
            for candidate in picks
        ]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": entries,
            "auto_send": self._auto_send_status(),
            "featured_count": len(featured),
        }

    def _auto_send_status(self) -> dict:
        active = self.subscribers.active()
        last = self.sends.last_sent_at()
        cadence_counts: dict[str, int] = {}
        for subscriber in active:
            cadence_counts[subscriber.frequency] = cadence_counts.get(subscriber.frequency, 0) + 1
        return {
            "active_subscribers": len(active),
            "last_sent_at": last.isoformat(timespec="seconds") if last else None,
            "default_cadence": DEFAULT_FREQUENCY,
            "cadence_counts": cadence_counts,
        }

    @staticmethod
    def _entry(candidate: dict, provisional: bool) -> dict:
        school = candidate.get("school") or ""
        year = candidate.get("graduation_year")
        school_line = f"{school} '{str(year)[2:]}" if school and year else school
        area = candidate.get("area")
        if area:
            school_line = " • ".join(part for part in [school_line, area] if part)
        origin = candidate.get("origin_location")
        current = candidate.get("current_location")
        if origin and current and origin != current:
            location_line = f"From {origin} — now in {current}"
        elif current or origin:
            location_line = f"Based in {current or origin}"
        else:
            location_line = ""
        return {
            "person_id": candidate["id"],
            "name": candidate["name"],
            "score": candidate.get("score") or 0,
            "school_line": school_line,
            "location_line": location_line,
            "thesis": candidate.get("thesis") or "",
            "top_signals": [
                signal.get("summary") or signal.get("type") or "Signal recorded"
                for signal in candidate.get("top_signals") or []
            ],
            "connection_context": candidate.get("connection_context") or "",
            "warm_intro": candidate.get("warm_intro") or "",
            "why_now": candidate.get("reviewed_why_now") or candidate.get("why_now") or "",
            "contact_links": candidate.get("contact_links") or {},
            "provisional": provisional,
        }

    def preview(self, subscriber: Subscriber) -> dict:
        all_candidates = self.candidates.list_candidates("discovery")
        message, person_ids = self.build(subscriber, all_candidates)
        candidates_by_id = {candidate["id"]: candidate for candidate in all_candidates}
        picks = [candidates_by_id[person_id] for person_id in person_ids]
        source_mix: dict[str, int] = {}
        for candidate in picks:
            bucket = candidate.get("source_bucket") or "unclassified"
            source_mix[bucket] = source_mix.get(bucket, 0) + 1
        return {
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
            "candidate_count": len(picks),
            "candidates": picks,
            "source_mix": dict(sorted(source_mix.items())),
        }

    def deliver(
        self,
        subscriber: Subscriber,
        dry_run: bool = False,
        all_candidates: list[dict] | None = None,
    ) -> dict:
        message, person_ids = self.build(subscriber, all_candidates)
        if not person_ids:
            return {
                "email": subscriber.email,
                "status": "empty",
                "candidate_count": 0,
            }
        if dry_run:
            return {
                "email": subscriber.email,
                "status": "preview",
                "candidate_count": len(person_ids),
                "subject": message.subject,
                "html": message.html,
                "text": message.text,
            }
        receipt = self.sender.send(message, subscriber.email)
        if receipt.get("sent"):
            self.sends.record_many(subscriber.id, person_ids, receipt.get("id"))
            status = "sent"
        elif receipt.get("preview_only"):
            status = "preview"
        else:
            status = "failed"
        return {
            "email": subscriber.email,
            "status": status,
            "candidate_count": len(person_ids),
            "receipt": receipt,
        }

    def run_due(
        self,
        dry_run: bool = False,
        recipient: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Deliver to every active subscriber whose cadence window has elapsed.

        Cadence is interval-based (see FREQUENCY_INTERVALS): a subscriber is due
        only when their last successful send is older than their interval, so
        ticking this repeatedly — or running it from both the in-process
        scheduler and a Railway cron — never double-sends. A `recipient` bypasses
        the cadence check (used for targeted preview/test sends)."""
        run_at = now or datetime.now(timezone.utc)
        self.candidates.rescore_all()
        active = self.subscribers.active(email=recipient)
        due = (
            active
            if recipient is not None
            else [subscriber for subscriber in active if self._is_due(subscriber, run_at)]
        )
        all_candidates = self.candidates.list_candidates("discovery")
        results = [
            self.deliver(subscriber, dry_run=dry_run, all_candidates=all_candidates)
            for subscriber in due
        ]
        return {
            "dry_run": dry_run,
            "run_at": run_at.isoformat(timespec="seconds"),
            "subscriber_count": len(results),
            "sent_count": sum(result["status"] == "sent" for result in results),
            "results": results,
        }

    def _is_due(self, subscriber: Subscriber, run_at: datetime) -> bool:
        interval = FREQUENCY_INTERVALS.get(subscriber.frequency, DEFAULT_INTERVAL)
        return not self.sends.sent_since(subscriber.id, run_at - interval)

    def send_to_active(self, dry_run: bool = False, now: datetime | None = None) -> dict:
        """Operator "send now": deliver to every active subscriber immediately,
        ignoring the daily/weekly cadence that `run_due` enforces. Reuses the
        same per-subscriber build + Resend sender + dedup ledger, so a person is
        never emailed twice. Preview-only when Resend is unconfigured."""
        run_at = now or datetime.now(timezone.utc)
        self.candidates.rescore_all()
        subscribers = self.subscribers.active()
        all_candidates = self.candidates.list_candidates("discovery")
        results = [
            self.deliver(subscriber, dry_run=dry_run, all_candidates=all_candidates)
            for subscriber in subscribers
        ]
        return {
            "dry_run": dry_run,
            "run_at": run_at.isoformat(timespec="seconds"),
            "subscriber_count": len(results),
            "sent_count": sum(result["status"] == "sent" for result in results),
            "empty_count": sum(result["status"] == "empty" for result in results),
            "results": results,
        }

    @staticmethod
    def _preference_rank(candidate: dict, subscriber: Subscriber) -> tuple[int, float]:
        interests = str(subscriber.preferences.get("signal_interests", "")).lower().split()
        haystack = " ".join(
            [
                str(candidate.get("area") or ""),
                str(candidate.get("reviewed_why_now") or candidate.get("why_now") or ""),
                *[
                    f"{signal.get('type', '')} {signal.get('summary', '')}"
                    for signal in candidate.get("top_signals", [])
                ],
            ]
        ).lower()
        matches = sum(term.strip(",.;") in haystack for term in interests if len(term) > 2)
        return matches, float(candidate.get("score") or 0)

    def _feedback_url(self, subscriber: Subscriber, person_id: str, vote: str) -> str:
        token = self.action_signer.issue(subscriber.id, "feedback", person_id, vote)
        return (
            f"{self.public_base_url}/api/digest/feedback"
            f"?token={quote(token, safe='')}"
            f"&person_id={quote(person_id, safe='')}&vote={vote}"
        )

    def _unsubscribe_url(self, subscriber: Subscriber) -> str:
        token = self.action_signer.issue(subscriber.id, "unsubscribe")
        return (
            f"{self.public_base_url}/api/digest/unsubscribe"
            f"?token={quote(token, safe='')}"
        )

    def _render_html(
        self,
        subscriber: Subscriber,
        picks: list[dict],
        today: str,
        provisional_ids: set[str] | None = None,
    ) -> str:
        esc = html.escape
        provisional_ids = provisional_ids or set()
        blocks: list[str] = []
        for candidate in picks:
            person_id = candidate["id"]
            provisional_note = (
                '<div style="color:#8a6d1f;font:11px ui-monospace,monospace;margin-top:8px">'
                "Provisional — surfaced by evidence, pending operator review</div>"
                if person_id in provisional_ids
                else ""
            )
            signals = candidate.get("top_signals") or []
            signal_items = "".join(
                f"<li>{esc(signal.get('summary') or signal.get('type') or 'Signal recorded')}</li>"
                for signal in signals
            )
            links = "".join(
                f'<a href="{esc(url, quote=True)}" style="color:#60652b;margin-right:14px">{esc(label.title())}</a>'
                for label, url in (candidate.get("contact_links") or {}).items()
                if label in {"linkedin", "x", "github", "email", "site"} and url
            )
            context = " · ".join(
                part
                for part in [
                    candidate.get("school"),
                    candidate.get("current_location") or candidate.get("origin_location"),
                ]
                if part
            )
            description = (
                candidate.get("reviewed_why_now")
                or candidate.get("why_now")
                or candidate.get("area")
                or "Showing multiple early signals worth a closer look."
            )
            provenance = candidate.get("primary_evidence_url") or ""
            provenance_link = (
                f'<a href="{esc(provenance, quote=True)}" style="color:#60652b">'
                "Primary public evidence</a>"
                if provenance
                else ""
            )
            up_url = self._feedback_url(subscriber, person_id, "up")
            down_url = self._feedback_url(subscriber, person_id, "down")
            blocks.append(
                f"""
                <section style="background:#fffdf7;border:1px solid #d8d4c4;border-radius:6px;padding:18px;margin:0 0 16px">
                  <div style="float:right;color:#60652b;font:700 20px ui-monospace,monospace">{float(candidate.get("score") or 0):.0f}</div>
                  <h2 style="font-size:21px;margin:0 36px 4px 0">{esc(candidate["name"])}</h2>
                  <div style="color:#716d5e;font:12px ui-monospace,monospace">{esc(context)}</div>
                  <p style="font-size:15px;line-height:1.45;margin:12px 0">{esc(description)}</p>
                  <div style="font-size:13px;line-height:1.5"><strong>Triggering signals</strong><ul style="padding-left:20px;margin:6px 0 12px">{signal_items}</ul></div>
                  <div style="font:12px ui-monospace,monospace">{links}{provenance_link}</div>
                  {provisional_note}
                  <div style="border-top:1px solid #e4e0d2;margin-top:14px;padding-top:10px;font-size:13px">
                    Useful?
                    <a href="{esc(up_url, quote=True)}" style="text-decoration:none;margin-left:8px">👍 Yes</a>
                    <a href="{esc(down_url, quote=True)}" style="text-decoration:none;margin-left:12px">👎 No</a>
                  </div>
                </section>"""
            )
        unsubscribe = esc(self._unsubscribe_url(subscriber), quote=True)
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f5f3ec;color:#1c1b16;font-family:Georgia,'Times New Roman',serif">
  <main style="max-width:620px;margin:0 auto;padding:24px 14px">
    <h1 style="font-size:27px;margin:0">Signal Scout</h1>
    <p style="color:#60652b;font:11px ui-monospace,monospace;text-transform:uppercase;letter-spacing:1px;margin:6px 0 22px">{len(picks)} people · {today}</p>
    {''.join(blocks)}
    <footer style="text-align:center;color:#817d6e;font:11px ui-monospace,monospace;padding:12px">
      You receive this {esc(subscriber.frequency)} digest.
      <a href="{unsubscribe}" style="color:#60652b">Unsubscribe</a>
    </footer>
  </main>
</body></html>"""

    def _render_text(
        self,
        subscriber: Subscriber,
        picks: list[dict],
        today: str,
        provisional_ids: set[str] | None = None,
    ) -> str:
        provisional_ids = provisional_ids or set()
        lines = [f"SIGNAL SCOUT — {len(picks)} people — {today}", ""]
        for index, candidate in enumerate(picks, 1):
            context = " · ".join(
                part
                for part in [
                    candidate.get("school"),
                    candidate.get("current_location") or candidate.get("origin_location"),
                ]
                if part
            )
            description = (
                candidate.get("reviewed_why_now")
                or candidate.get("why_now")
                or candidate.get("area")
                or "Showing multiple early signals worth a closer look."
            )
            lines.extend([f"{index}. {candidate['name']} ({float(candidate.get('score') or 0):.0f})"])
            if context:
                lines.append(context)
            if candidate["id"] in provisional_ids:
                lines.append("[Provisional — surfaced by evidence, pending operator review]")
            lines.append(description)
            lines.append("Triggering signals:")
            for signal in candidate.get("top_signals") or []:
                lines.append(f"- {signal.get('summary') or signal.get('type') or 'Signal recorded'}")
            for label, url in (candidate.get("contact_links") or {}).items():
                if label in {"linkedin", "x", "github", "email", "site"} and url:
                    lines.append(f"{label.title()}: {url}")
            if candidate.get("primary_evidence_url"):
                lines.append(f"Primary evidence: {candidate['primary_evidence_url']}")
            lines.append(f"Useful: {self._feedback_url(subscriber, candidate['id'], 'up')}")
            lines.append(f"Not useful: {self._feedback_url(subscriber, candidate['id'], 'down')}")
            lines.append("")
        lines.append(f"Unsubscribe: {self._unsubscribe_url(subscriber)}")
        return "\n".join(lines)
