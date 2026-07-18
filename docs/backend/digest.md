# Digest

Builds and sends the investor-facing digest email: selects eligible candidates from the scoring/candidate layer, renders them into an HTML digest, persists it, and delivers it via Resend (or a no-op preview sender when unconfigured).

## backend/digest/__init__.py
Empty package marker file with no exported symbols.

## backend/digest/generator.py
Turns the top unscored/discovery candidates into an evidence-backed, contactable digest, enforcing the hard rule that nobody enters the digest without at least two distinct reachable contact channels.

- `DigestGenerator` — orchestrates digest assembly: pulls candidates via a `CandidateService`, filters for contactability, renders HTML from a template, and persists the result via a `DigestRepository`; constructed with `candidates`, `digests`, `out_dir`, and `size` (default 8, the number of entries per digest).
  - `DigestGenerator.generate() -> Digest` — rescoring is triggered first (`candidates.rescore_all()`), then pulls the `"discovery"` candidate pool, filters to `_contactable` candidates, takes the top `size` picks (list is presumed pre-sorted by score), builds a `DigestEntry` per pick (school line, location line, top-3 signal summaries as tags, connection context, warm intro, why-now, evidence links, contact links), wraps them in a `Digest` with a generated subject line (`"Signal Scout — {N} people you should know ({date})"`), renders the HTML body, saves the digest to the repository, and writes the rendered HTML to `out_dir/digest-{date}.html`.
  - `DigestGenerator._contactable(candidate) -> bool` (static) — eligibility gate requiring at least two of the five reachable channels (`github`, `linkedin`, `x`, `email`, `site`) to be present in `contact_links`; note the code comment that a *verified* LinkedIn is a separate manual step done later in `scripts/verify_candidates.py`, not enforced here.
  - `DigestGenerator._school_line(candidate) -> str` (static) — formats a school + graduation-year string like `"MIT '26"` (last two digits of the year), or just the school name if no year, or empty string if no school.
  - `DigestGenerator._location_line(candidate) -> str` (static) — formats `"From {origin} — now in {current}"` when origin and current locations differ, `"Based in {current or origin}"` when only one is known, or empty string if neither is known.
  - `DigestGenerator._render(digest) -> str` — loads `template.html`, HTML-escapes and builds a block of markup per `DigestEntry` (rank, score, name, subline, thesis, signal tags, connection/intro/why-now context paragraphs, contact links), then substitutes `{{COUNT}}`, `{{DATE}}`, and `{{ENTRIES}}` placeholders in the template with the computed values.

## backend/digest/sender.py
Defines the email transport abstraction used to deliver digests, with a keyless-safe Resend implementation that degrades to preview-only when no API key is configured.

- `EmailMessage` — frozen dataclass representing an outbound email payload; fields: `subject`, `html`, `text`.
- `EmailSender` — abstract base class defining the sender interface.
  - `EmailSender.send(message, to) -> dict` — abstract method; implementations must return a delivery receipt dict.
- `NoopSender` — `EmailSender` implementation used when no real provider is configured (or for local preview).
  - `NoopSender.send(message, to) -> dict` — does not send anything; returns a receipt dict with `sent: False`, `preview_only: True`, and a note that only a preview was generated.
- `ResendSender` — `EmailSender` implementation that delivers via the Resend HTTP API (`https://api.resend.com/emails`); constructed with `api_key`, `from_email`, `timeout_seconds` (default 15), and an optional injectable `requests.Session`.
  - `ResendSender.configured` (property) — `bool`, true only when both `api_key` and `from_email` are non-empty.
  - `ResendSender.send(message, to) -> dict` — if not `configured`, delegates to `NoopSender` for a preview-only receipt; otherwise POSTs the message (from/to/subject/html/text) to the Resend API with bearer-token auth, and on `RequestException` or invalid JSON response returns a receipt with `sent: False` and an error note; on success returns `sent: True`, `preview_only: False`, `provider: "resend"`, and the provider's returned message `id`. No dedupe or rate-limit logic (e.g. a "one test digest per 24h" rule) is present in this file.
