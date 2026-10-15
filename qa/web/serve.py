"""Start the web app for a local QA batch, with app data isolated (#968).

Use this instead of ``python -m app.web.main`` before running
``python -m qa.web._batch``. It sets ``PHOTO_MANAGER_DATA_DIR`` to a fresh temp
dir (unless one is already set), so the server's thumbnails, transcodes, logs
and delete audit CSVs never reach the owner's real app-data folder. It serves
the same factory on the same port, so the batch's default ``--base-url`` works.

The owner's own launch (``python -m app.web.main``, ``launcher.py``) is
unchanged and still uses the real app-data folder.
"""

from __future__ import annotations

from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from infrastructure.app_data import ensure_isolated_app_data  # noqa: E402


def main() -> None:  # pragma: no cover - script entry; uvicorn.run needs a live server
    import uvicorn

    data_dir = ensure_isolated_app_data()
    print(f"qa serve: app data -> {data_dir}", flush=True)
    uvicorn.run(
        "app.web.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
