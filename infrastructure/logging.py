"""Logging initialization utilities using loguru."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from loguru import logger


def init_logging(log_dir: str | None = None) -> None:
    """Initialize rotating file logging under the given directory."""
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


def get_log_directory() -> str:
    """Get the main log directory path."""
    return str(Path.home() / "AppData" / "Local" / "PhotoManager" / "logs")


def get_delete_log_directory() -> str:
    """Get the delete log directory path."""
    return os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "PhotoManager", "delete_logs")


def find_latest_log_file(log_dir: str | None = None) -> Path | None:
    """Find the latest log file in the specified directory."""
    if log_dir is None:
        log_dir = get_log_directory()

    try:
        log_path = Path(log_dir)
        if not log_path.exists():
            return None

        # Find files matching app_*.log pattern
        log_files = list(log_path.glob("app_*.log"))
        if not log_files:
            return None

        # Return the most recently modified file
        return max(log_files, key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError, FileNotFoundError):
        return None


def find_latest_delete_log_file() -> Path | None:
    """Find the latest delete log file."""
    delete_log_dir = get_delete_log_directory()

    try:
        delete_path = Path(delete_log_dir)
        if not delete_path.exists():
            return None

        # Find files matching delete_*.csv pattern
        delete_files = list(delete_path.glob("delete_*.csv"))
        if not delete_files:
            return None

        # Return the most recently modified file
        return max(delete_files, key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError, FileNotFoundError):
        return None


def open_file_in_default_app(file_path: str) -> bool:
    """Open a file in the default application for its type."""
    try:
        if os.name == "nt":  # Windows
            os.startfile(file_path)
        else:  # macOS/Linux
            subprocess.run(["xdg-open", file_path], check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def open_directory_in_explorer(dir_path: str) -> bool:
    """Open a directory in the file explorer."""
    try:
        if os.name == "nt":  # Windows
            os.startfile(dir_path)
        else:  # macOS/Linux
            subprocess.run(["xdg-open", dir_path], check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def open_latest_log() -> bool:
    """Open the latest log file in the default application."""
    log_file = find_latest_log_file()
    if log_file:
        return open_file_in_default_app(str(log_file))
    return False


def open_latest_delete_log() -> bool:
    """Open the latest delete log file in the default application."""
    delete_file = find_latest_delete_log_file()
    if delete_file:
        return open_file_in_default_app(str(delete_file))
    return False


def open_log_directory() -> bool:
    """Open the log directory in the file explorer."""
    return open_directory_in_explorer(get_log_directory())


def open_delete_log_directory() -> bool:
    """Open the delete log directory in the file explorer."""
    return open_directory_in_explorer(get_delete_log_directory())
