from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..service import BotService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class StrategyUpdate(BaseModel):
    strategy: str | None = None
    signal_filter: str | None = None


def create_app(service: BotService) -> FastAPI:
    app = FastAPI(title="polybot control panel")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/status")
    def get_status():
        return service.status()

    @app.get("/api/strategy")
    def get_strategy():
        return service.get_strategy_info()

    @app.post("/api/strategy")
    def set_strategy(update: StrategyUpdate):
        try:
            return service.set_strategy(update.strategy, update.signal_filter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
