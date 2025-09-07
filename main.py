from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.views.main_window import MainWindow
from app.viewmodels.main_vm import MainVM
from infrastructure.csv_repository import CsvPhotoRepository
from infrastructure.logging import init_logging
from infrastructure.settings import JsonSettings


BASE_DIR = Path(__file__).parent


def _parse_default_sort(settings: JsonSettings) -> list[tuple[str, bool]]:
    # Expect a list like: [{"field":"file_size_bytes","asc":false}, ...]
    raw = settings.get("sorting.defaults", [])
    result: list[tuple[str, bool]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "field" in item:
                field = str(item.get("field"))
                asc = bool(item.get("asc", True))
                result.append((field, asc))
    return result


def main() -> int:
    init_logging()
    settings = JsonSettings(BASE_DIR / "settings.json")

    app = QApplication(sys.argv)

    repo = CsvPhotoRepository()
    default_sort = _parse_default_sort(settings)
    vm = MainVM(repo, default_sort=default_sort)

    sample_csv = BASE_DIR / "samples" / "sample.csv"
    if sample_csv.exists():
        vm.load_csv(str(sample_csv))

    win = MainWindow(vm=vm, repo=repo)
    win.refresh_tree(vm.groups)
    win.statusBar().showMessage("Ready", 2000)
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
