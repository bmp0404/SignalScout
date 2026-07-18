# security

The `security` module provides tamper-proof, expiring action tokens used to authenticate one-click links (feedback votes, unsubscribe) embedded in outbound digest emails, sitting between the digest and api stages of the pipeline (digest generation issues tokens; `backend/api/routes.py` verifies them on click), and is wired into the app as `Container.email_action_signer` in `backend/container.py`.

## backend/security/email_actions.py
Implements `EmailActionSigner`, an HMAC-signed, base64-encoded, JSON-payload token scheme with an expiry and action/argument binding, so links can't be replayed for a different action or forged without the server secret.

- `_encode(value: bytes) -> str` — base64url-encodes bytes and strips padding, for compact URL-safe tokens.
- `_decode(value: str) -> bytes` — reverses `_encode`, restoring base64 padding before decoding.
- `EmailActionSigner` — issues and verifies short-lived, action-scoped tokens embedded in digest email links (e.g. feedback vote and unsubscribe URLs).
  - `EmailActionSigner.__init__(secret, ttl_seconds=30*24*60*60)` — stores the signing secret and token lifetime (defaults to 30 days).
  - `EmailActionSigner.issue(subscriber_id, action, person_id="", vote="") -> str` — builds a JSON payload (subscriber id, action, person id, vote, expiry timestamp), base64-encodes it, and appends an HMAC-SHA256 signature; returns `""` if no secret is configured.
  - `EmailActionSigner.verify(token, action, person_id="", vote="") -> str | None` — splits the token into payload and signature, checks the signature with `hmac.compare_digest`, decodes the JSON payload, and confirms the action/person/vote match and the token hasn't expired, returning the subscriber id if valid (or `None`/swallowing malformed input on any failure).
  - `EmailActionSigner._sign(payload: str) -> str` — computes the base64url-encoded HMAC-SHA256 signature of a payload string using the instance secret.
