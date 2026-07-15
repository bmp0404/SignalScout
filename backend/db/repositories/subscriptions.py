"""Persistence for subscribers, never-repeat sends, and feedback votes."""

from datetime import datetime

from backend.db.repositories.base import BaseRepository
from backend.domain.subscriber import Subscriber, utc_now


class SubscriberRepository(BaseRepository):
    def subscribe(self, email: str, frequency: str, preferences: dict) -> Subscriber:
        subscriber = Subscriber(
            email=email.strip().lower(),
            frequency=frequency,
            preferences=preferences,
        )
        self.conn.execute(
            """INSERT INTO subscribers
               (id, email, frequency, preferences, unsubscribe_token, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                 frequency = excluded.frequency,
                 preferences = excluded.preferences,
                 active = 1,
                 updated_at = excluded.updated_at""",
            (
                subscriber.id,
                subscriber.email,
                subscriber.frequency,
                self.dumps(subscriber.preferences),
                subscriber.unsubscribe_token,
                int(subscriber.active),
                subscriber.created_at,
                subscriber.updated_at,
            ),
        )
        self.conn.commit()
        return self.get_by_email(subscriber.email)  # type: ignore[return-value]

    def get_by_email(self, email: str) -> Subscriber | None:
        row = self.conn.execute(
            "SELECT * FROM subscribers WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return self._to_model(row) if row else None

    def get_by_token(self, token: str) -> Subscriber | None:
        row = self.conn.execute(
            "SELECT * FROM subscribers WHERE unsubscribe_token = ?",
            (token,),
        ).fetchone()
        return self._to_model(row) if row else None

    def active(self, frequency: str | None = None, email: str | None = None) -> list[Subscriber]:
        clauses = ["active = 1"]
        params: list[str] = []
        if frequency:
            clauses.append("frequency = ?")
            params.append(frequency)
        if email:
            clauses.append("email = ?")
            params.append(email.strip().lower())
        rows = self.conn.execute(
            f"SELECT * FROM subscribers WHERE {' AND '.join(clauses)} ORDER BY created_at",
            tuple(params),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def deactivate(self, token: str) -> bool:
        now = utc_now()
        cursor = self.conn.execute(
            """UPDATE subscribers SET active = 0, updated_at = ?
               WHERE unsubscribe_token = ? AND active = 1""",
            (now, token),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _to_model(row) -> Subscriber:
        return Subscriber(
            id=row["id"],
            email=row["email"],
            frequency=row["frequency"],
            preferences=BaseRepository.loads(row["preferences"], {}),
            unsubscribe_token=row["unsubscribe_token"],
            active=bool(row["active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class DigestSendRepository(BaseRepository):
    def sent_since(self, subscriber_id: str, since: datetime) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM digest_sends
               WHERE subscriber_id = ? AND sent_at >= ?
               LIMIT 1""",
            (subscriber_id, since.isoformat(timespec="seconds")),
        ).fetchone()
        return row is not None

    def sent_person_ids(self, subscriber_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT person_id FROM digest_sends WHERE subscriber_id = ?",
            (subscriber_id,),
        ).fetchall()
        return {row["person_id"] for row in rows}

    def record_many(
        self,
        subscriber_id: str,
        person_ids: list[str],
        provider_message_id: str | None,
    ) -> None:
        sent_at = utc_now()
        for person_id in person_ids:
            self.conn.execute(
                """INSERT INTO digest_sends
                   (subscriber_id, person_id, sent_at, provider_message_id)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(subscriber_id, person_id) DO NOTHING""",
                (subscriber_id, person_id, sent_at, provider_message_id),
            )
        self.conn.commit()


class FeedbackRepository(BaseRepository):
    def upsert(self, subscriber_id: str, person_id: str, vote: str) -> None:
        now = utc_now()
        self.conn.execute(
            """INSERT INTO feedback_votes
               (subscriber_id, person_id, vote, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(subscriber_id, person_id) DO UPDATE SET
                 vote = excluded.vote,
                 updated_at = excluded.updated_at""",
            (subscriber_id, person_id, vote, now, now),
        )
        self.conn.commit()
