import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import build_router
from backend.config import Settings
from backend.container import Container
from backend.digest.sender import EmailMessage, EmailSender, ResendSender
from backend.domain.person import Person
from backend.domain.signal import Signal


class StubSender(EmailSender):
    def __init__(self):
        self.messages: list[tuple[EmailMessage, str]] = []

    def send(self, message: EmailMessage, to: str) -> dict:
        self.messages.append((message, to))
        return {"sent": True, "id": f"stub-{len(self.messages)}"}


class StubResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"id": "resend-test-id"}


class StubSession:
    def __init__(self):
        self.request = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return StubResponse()


class Phase4DigestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = Settings(
            db_path=root / "test.db",
            database_url="",
            out_dir=root / "out",
            public_base_url="https://signals.example",
            cron_secret="test-secret",
            resend_api_key="",
            digest_from_email="",
        )
        self.container = Container(settings)
        self.container.db.init_schema()
        self.person = Person(
            name="Guillermo Rauch",
            cohort="discovery",
            score=88.0,
            current_location="San Francisco, CA",
            thesis="Building public developer infrastructure.",
            github_username="rauchg",
        )
        self.container.persons.save(self.person)
        self.container.signals.save_many(
            [
                Signal(
                    person_id=self.person.id,
                    person_name=self.person.name,
                    signal_type="github_star_project",
                    signal_category="code",
                    signal_date="2026-07-01",
                    signal_strength=0.9,
                    source="github",
                    source_url="https://github.com/vercel/next.js",
                    summary="Next.js passed 100,000 public GitHub stars.",
                )
            ]
        )

    def tearDown(self):
        self.container.db.close()
        self.temp_dir.cleanup()

    def signup_for_test_digest(self, client: TestClient) -> dict:
        response = client.post(
            "/api/subscribers",
            json={
                "email": "investor@example.com",
                "frequency": "daily",
                "signal_interests": "open source",
                "seed_accounts": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_subscribe_upserts_without_changing_token(self):
        first = self.container.subscribers.subscribe(
            "Investor@Example.com",
            "daily",
            {"signal_interests": "developer tools", "seed_accounts": []},
        )
        second = self.container.subscribers.subscribe(
            "investor@example.com",
            "weekly",
            {"signal_interests": "AI", "seed_accounts": ["https://x.com/example"]},
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.unsubscribe_token, second.unsubscribe_token)
        self.assertEqual(second.frequency, "weekly")
        self.assertEqual(second.preferences["signal_interests"], "AI")

    def test_successful_delivery_records_and_never_repeats(self):
        subscriber = self.container.subscribers.subscribe(
            "investor@example.com",
            "daily",
            {"signal_interests": "developer", "seed_accounts": []},
        )
        sender = StubSender()
        self.container.subscriber_digest.sender = sender

        first = self.container.subscriber_digest.deliver(subscriber)
        second = self.container.subscriber_digest.deliver(subscriber)

        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "empty")
        self.assertEqual(len(sender.messages), 1)
        message = sender.messages[0][0]
        self.assertIn("Next.js passed 100,000 public GitHub stars.", message.html)
        self.assertIn("/api/digest/feedback", message.text)
        self.assertIn("/api/digest/unsubscribe", message.text)

    def test_dry_run_does_not_consume_candidate(self):
        subscriber = self.container.subscribers.subscribe(
            "investor@example.com",
            "daily",
            {"signal_interests": "", "seed_accounts": []},
        )
        preview = self.container.subscriber_digest.deliver(subscriber, dry_run=True)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(
            self.container.digest_sends.sent_person_ids(subscriber.id),
            set(),
        )

    def test_resend_sender_uses_html_and_plain_text_transport(self):
        session = StubSession()
        sender = ResendSender(
            "test-api-key",
            "Signal Scout <digest@signals.example>",
            session=session,
        )
        receipt = sender.send(
            EmailMessage(subject="Subject", html="<p>HTML</p>", text="Plain text"),
            "investor@example.com",
        )
        self.assertTrue(receipt["sent"])
        self.assertEqual(receipt["id"], "resend-test-id")
        self.assertEqual(session.request["json"]["html"], "<p>HTML</p>")
        self.assertEqual(session.request["json"]["text"], "Plain text")
        self.assertEqual(session.request["timeout"], 15)

    def test_test_digest_endpoint_sends_to_verified_subscriber(self):
        app = FastAPI()
        app.include_router(build_router(self.container))
        client = TestClient(app)
        signup = self.signup_for_test_digest(client)
        sender = StubSender()
        self.container.subscriber_digest.sender = sender

        response = client.post(
            "/api/digest/test",
            json={
                "email": signup["email"],
                "token": signup["subscriber_token"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        self.assertEqual(response.json()["candidate_count"], 1)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(sender.messages[0][1], "investor@example.com")

    def test_test_digest_endpoint_reports_unconfigured_sender(self):
        app = FastAPI()
        app.include_router(build_router(self.container))
        client = TestClient(app)
        signup = self.signup_for_test_digest(client)

        response = client.post(
            "/api/digest/test",
            json={
                "email": signup["email"],
                "token": signup["subscriber_token"],
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Email delivery isn't configured yet.")
        subscriber = self.container.subscribers.get_by_email(signup["email"])
        self.assertEqual(self.container.digest_sends.sent_person_ids(subscriber.id), set())

    def test_test_digest_endpoint_rejects_invalid_token(self):
        app = FastAPI()
        app.include_router(build_router(self.container))
        client = TestClient(app)
        self.signup_for_test_digest(client)
        sender = StubSender()
        self.container.subscriber_digest.sender = sender

        response = client.post(
            "/api/digest/test",
            json={
                "email": "investor@example.com",
                "token": "not-the-subscriber-token",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(sender.messages), 0)

    def test_test_digest_endpoint_rate_limits_for_24_hours(self):
        app = FastAPI()
        app.include_router(build_router(self.container))
        client = TestClient(app)
        signup = self.signup_for_test_digest(client)
        sender = StubSender()
        self.container.subscriber_digest.sender = sender
        payload = {
            "email": signup["email"],
            "token": signup["subscriber_token"],
        }

        first = client.post("/api/digest/test", json=payload)
        second = client.post("/api/digest/test", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("after 24 hours", second.json()["detail"])
        self.assertEqual(len(sender.messages), 1)

    def test_signup_feedback_unsubscribe_and_cron_auth(self):
        app = FastAPI()
        app.include_router(build_router(self.container))
        client = TestClient(app)

        signup = client.post(
            "/api/subscribers",
            json={
                "email": "investor@example.com",
                "frequency": "daily",
                "signal_interests": "open source",
                "seed_accounts": "https://x.com/example, https://linkedin.com/in/example",
            },
        )
        self.assertEqual(signup.status_code, 200)
        self.assertTrue(signup.json()["subscriber_token"])
        subscriber = self.container.subscribers.get_by_email("investor@example.com")
        self.assertEqual(len(subscriber.preferences["seed_accounts"]), 2)

        unauthorized = client.post("/api/digest/cron?dry_run=true")
        self.assertEqual(unauthorized.status_code, 401)
        authorized = client.post(
            "/api/digest/cron?dry_run=true&recipient=investor@example.com",
            headers={"Authorization": "Bearer test-secret"},
        )
        self.assertEqual(authorized.status_code, 200)
        self.assertTrue(authorized.json()["dry_run"])

        feedback = client.get(
            "/api/digest/feedback",
            params={
                "token": subscriber.unsubscribe_token,
                "person_id": self.person.id,
                "vote": "up",
            },
        )
        self.assertEqual(feedback.status_code, 200)
        self.assertIn("All set", feedback.text)

        unsubscribe = client.get(
            "/api/digest/unsubscribe",
            params={"token": subscriber.unsubscribe_token},
        )
        self.assertEqual(unsubscribe.status_code, 200)
        self.assertFalse(
            self.container.subscribers.get_by_email("investor@example.com").active
        )


if __name__ == "__main__":
    unittest.main()
