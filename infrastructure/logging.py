from __future__ import annotations

from pathlib import Path
from loguru import logger


def init_logging(log_dir: str | None = None) -> None:
    if log_dir is None:
        log_dir = str(Path.home() / "AppData" / "Local" / "PhotoManager" / "logs")
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        str(log_path / "app_{time:YYYYMMDD}.log"),
        rotation="10 MB",
        retention="10 days",
        compression="zip",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        level="INFO",
    )
