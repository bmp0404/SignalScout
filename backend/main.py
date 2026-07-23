"""FastAPI app factory. Run: uvicorn backend.main:app --reload --port 8000"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import build_router
from backend.container import Container
from backend.services.discovery_scheduler import DiscoveryScheduler

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    container = Container()
    container.db.init_schema()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = container.settings
        scheduler = DiscoveryScheduler(
            settings,
            container_factory=lambda: Container(settings),
        )
        app.state.discovery_scheduler = scheduler
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="Signal Scout", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_router(container))
    # Serve the built frontend (production/Docker). Local dev keeps using Vite on 5173.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()
