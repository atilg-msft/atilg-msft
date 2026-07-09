from __future__ import annotations

from ..config import Settings
from .base import ExecutorBase


def get_executor(settings: Settings) -> ExecutorBase:
    if settings.mode == "live":
        from .live import LiveExecutor

        return LiveExecutor(settings)
    from .paper import PaperExecutor

    return PaperExecutor(settings)
