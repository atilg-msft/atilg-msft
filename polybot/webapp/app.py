from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ..service import BotService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(service: BotService) -> FastAPI:
    app = FastAPI(title="polybot control panel")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/status")
    def get_status():
        return service.status()

    @app.post("/api/start")
    def start():
        service.start()
        return service.status()

    @app.post("/api/stop")
    def stop():
        service.stop()
        return service.status()

    @app.post("/api/liquidate")
    def liquidate():
        logger.warning("manual liquidation requested via API")
        result = service.liquidate()
        return {**result, "status": service.status()}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
