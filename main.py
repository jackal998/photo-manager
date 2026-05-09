from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from loguru import logger

from app.viewmodels.main_vm import MainVM
from app.views.main_window import MainWindow
from infrastructure.i18n import init_translator
from infrastructure.image_service import ImageService
from infrastructure.logging import init_logging
from infrastructure.settings import JsonSettings

BASE_DIR = Path(__file__).parent
# QA / test runs may point at an alternative config root by setting
# PHOTO_MANAGER_HOME. When unset, we keep the historical behavior of
# reading settings.json from the repo root. Relative paths are
# resolved against the repo root so the env var is robust to cwd.
_home_env = os.environ.get("PHOTO_MANAGER_HOME")
CONFIG_HOME = (BASE_DIR / _home_env).resolve() if _home_env else BASE_DIR


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


def install_locale_translators(app: QApplication, settings: JsonSettings) -> None:
    """Initialize the YAML catalog + Qt's bundled translator for the
    locale stored at ``settings.ui.locale``.

    Called at startup AND on live language switch. The Qt translator
    setup is idempotent: any previously-installed QTranslator children
    of ``app`` are removed before installing the new one, so calling
    this twice doesn't accumulate stale translators.
    """
    locale = settings.get("ui.locale", "en") or "en"
    init_translator(locale, BASE_DIR / "translations")

    # Remove any QTranslator we previously installed on the app.
    # PySide6 sets the parent of an installed translator to the app,
    # so findChildren picks them up. Removing then deleting avoids
    # leaks across switches.
    for old in app.findChildren(QTranslator):
        app.removeTranslator(old)
        old.deleteLater()

    qt_translator = QTranslator(app)
    qt_locale_code = locale.replace("_", "-")
    if qt_translator.load(
        QLocale(qt_locale_code),
        "qtbase",
        "_",
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
    ):
        app.installTranslator(qt_translator)


def make_main_window(
    vm: MainVM,
    image_service: ImageService,
    settings: JsonSettings,
) -> MainWindow:
    """Build a fresh MainWindow with the current locale and a populated tree.

    Shared between startup and the live-language-switch path. The
    Translator must already be initialized (call
    ``install_locale_translators`` first); construction-time ``t()``
    calls bake in the active locale.
    """
    from infrastructure.i18n import t

    win = MainWindow(vm=vm, image_service=image_service, settings=settings)
    win.refresh_tree(vm.groups)
    win.statusBar().showMessage(t("main_window.status_ready"), 2000)
    return win


def main() -> int:
    init_logging()
    settings = JsonSettings(CONFIG_HOME / "settings.json")

    # Cross-platform QA / hosted-CI escape hatch (#129): the Windows native
    # IFileSaveDialog and equivalent macOS NSSavePanel cannot be driven by
    # synthesized input on hosted runners, but Qt's widget-based file dialog
    # responds to UIA / AX normally. Setting this attribute before
    # QApplication is constructed switches every QFileDialog in the process
    # to the non-native variant — one switch, every platform.
    if os.environ.get("PHOTO_MANAGER_QT_FILE_DIALOG") == "1":
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs)

    app = QApplication(sys.argv)

    # Initialize translation catalogs (YAML + Qt's bundled qtbase_*.qm)
    # for the persisted ui.locale. Same helper used by the live
    # language switch.
    install_locale_translators(app, settings)

    img = ImageService(settings)
    default_sort = _parse_default_sort(settings)
    vm = MainVM(default_sort=default_sort)

    # HEIC diagnostics: log supported formats and try WIC 512/1024 on the first HEIC
    try:
        fmts = sorted(
            {
                bytes(f).decode("ascii", errors="ignore").lower()
                for f in QImageReader.supportedImageFormats()
            }
        )
        logger.info("Qt supported formats: {}", ", ".join(fmts))
        heic_path: str | None = None
        for g in getattr(vm, "groups", []) or []:
            for rec in getattr(g, "items", []) or []:
                p = getattr(rec, "file_path", "")
                if isinstance(p, str) and p.lower().endswith((".heic", ".heif")):
                    heic_path = p
                    break
            if heic_path:
                break
        if heic_path:
            try:
                exists = os.path.exists(heic_path)
                logger.info("HEIC probe path: {} | exists={}", heic_path, exists)
                if exists:
                    try:
                        r = QImageReader(heic_path)
                        r.setAutoTransform(True)
                        _img = r.read()
                        if _img is None or _img.isNull():
                            logger.info(
                                "Qt read (orig) failed: {}", r.errorString() or "null image"
                            )
                        else:
                            logger.info("Qt read (orig) ok: {}x{}", _img.width(), _img.height())
                    except Exception as ex:
                        logger.info("Qt read (orig) exception: {}", ex)

                    for side in (512, 1024):
                        try:
                            wic = img._load_via_shell_thumbnail(heic_path, side)  # type: ignore[attr-defined]
                            if wic is None or wic.isNull():
                                logger.info("WIC {} failed", side)
                            else:
                                logger.info("WIC {} ok: {}x{}", side, wic.width(), wic.height())
                        except Exception as ex:
                            logger.info("WIC {} exception: {}", side, ex)

                    try:
                        pub512 = img.get_thumbnail(heic_path, 512)
                        if pub512 is None or pub512.isNull():
                            logger.info("Public thumbnail 512 failed")
                        else:
                            logger.info(
                                "Public thumbnail 512 ok: {}x{}", pub512.width(), pub512.height()
                            )
                    except Exception as ex:
                        logger.info("Public thumbnail 512 exception: {}", ex)

                    try:
                        pub1024 = img.get_preview(heic_path, 1024)
                        if pub1024 is None or pub1024.isNull():
                            logger.info("Public preview 1024 failed")
                        else:
                            logger.info(
                                "Public preview 1024 ok: {}x{}", pub1024.width(), pub1024.height()
                            )
                    except Exception as ex:
                        logger.info("Public preview 1024 exception: {}", ex)
            except Exception as ex:
                logger.info("HEIC probe outer exception: {}", ex)
        else:
            logger.info("No HEIC path found in loaded data for diagnostics.")
    except Exception as ex:
        logger.info("HEIC diagnostics skipped due to exception: {}", ex)

    win = make_main_window(vm, img, settings)
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
