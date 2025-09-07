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


def main() -> int:
    init_logging()
    settings = JsonSettings(BASE_DIR / "settings.json")

    app = QApplication(sys.argv)

    repo = CsvPhotoRepository()
    vm = MainVM(repo)

    sample_csv = BASE_DIR / "samples" / "sample.csv"
    if sample_csv.exists():
        vm.load_csv(str(sample_csv))

    win = MainWindow(vm=vm, repo=repo)
    win.show_group_counts(vm.group_count)
    win.show_groups_summary(vm.groups)
    win.refresh_tree(vm.groups)
    win.statusBar().showMessage("Ready", 2000)
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
