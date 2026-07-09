from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(data_dir: Path) -> None:
    log_path = data_dir / "polybot.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path),
        ],
    )
