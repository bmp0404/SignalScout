"""Background ticker that periodically runs due discovery recipes.

Uses a fresh Container per tick so SQLite/Postgres connections stay
thread-local. Disabled with DISCOVERY_BACKGROUND=0.
"""

import logging
import threading
import time
from collections.abc import Callable

from backend.config import Settings

logger = logging.getLogger(__name__)


class DiscoveryScheduler:
    def __init__(
        self,
        settings: Settings,
        container_factory: Callable[[], object],
    ):
        self.settings = settings
        self.container_factory = container_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.settings.discovery_background:
            logger.info("Discovery background scheduler disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="discovery-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Discovery background scheduler started (interval=%sh)",
            self.settings.discovery_background_interval_hours,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        # Short initial delay so the HTTP server finishes binding first.
        if self._stop.wait(15):
            return
        while not self._stop.is_set():
            self._tick()
            hours = max(1, int(self.settings.discovery_background_interval_hours))
            if self._stop.wait(hours * 3600):
                return

    def _tick(self) -> None:
        container = None
        try:
            container = self.container_factory()
            result = container.discovery_recipe_service.run_due()
            logger.info(
                "Discovery run_due: due=%s ran=%s errors=%s created=%s",
                result["due_count"],
                result["ran_count"],
                result["error_count"],
                result["created_total"],
            )
        except Exception:  # noqa: BLE001 — background loop must not die on one tick
            logger.exception("Discovery background tick failed")
        finally:
            if container is not None:
                try:
                    container.db.close()
                except Exception:  # noqa: BLE001
                    pass
            # Yield briefly so tests / reloads can observe completion.
            time.sleep(0)
