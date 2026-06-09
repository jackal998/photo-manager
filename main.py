from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QThread, QTranslator
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from loguru import logger

from app.viewmodels.main_vm import MainVM
from app.views.main_window import MainWindow
from infrastructure.i18n import init_translator
from infrastructure.image_service import ImageService
from infrastructure.logging import init_logging
from infrastructure.settings import JsonSettings

if getattr(sys, "frozen", False):
    # PyInstaller --onedir: bundled read-only assets (translations/) live
    # under sys._MEIPASS, but writable user state (settings.json,
    # window_state.ini) must sit next to the executable so it survives
    # process exit and is discoverable / portable.
    BASE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    CONFIG_HOME = Path(sys.executable).parent
else:
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


def _cleanup_on_quit(app: QApplication) -> None:
    """#473 — Graceful shutdown hook wired to ``QApplication.aboutToQuit``.

    Fires on every Qt-detected quit path (main window close cascade,
    Qt-detected OS logoff, ``QApplication.quit()``) — NOT on hard
    SIGKILL / Job Object termination, which #460 handles separately.

    Responsibilities:
      1. Signal any in-flight ``ScanWorker`` to stop. The worker is
         owned by ``ScanDialog._worker`` rather than the app or vm,
         so we discover it by walking top-level widgets and looking
         for an attribute named ``_worker`` that's a running
         ``QThread``. Stays import-cycle-free (no ScanDialog import)
         and naturally extends to any future dialog owning a QThread
         under the same attribute name.
      2. Flush loguru. The file sink runs with ``enqueue=True`` (see
         ``infrastructure/logging.init_logging``), so pending records
         sit in a background queue; ``logger.complete()`` waits for
         the queue to drain and ``logger.remove()`` closes the sink
         so the rotating ``app_<date>.log`` is fully written before
         the process exits.

    Best-effort throughout — any exception is logged but never raised,
    because Qt swallows exceptions from ``aboutToQuit`` slots and a
    half-failed cleanup must not block shutdown.
    """
    try:
        for widget in app.topLevelWidgets():
            worker = getattr(widget, "_worker", None)
            if isinstance(worker, QThread) and worker.isRunning():
                logger.info("aboutToQuit: signalling running ScanWorker to stop")
                worker.requestInterruption()
                # Match scan_dialog.closeEvent's 3s budget — long enough
                # for the worker to tear down exiftool + consumer threads,
                # short enough that quit doesn't visibly hang.
                # #491 — capture wait()'s return so a timeout on the
                # graceful-shutdown path is visible in the log (mirrors
                # scan_dialog's hook). Without this we have no signal
                # for diagnosing whether shutdown was clean or
                # orphaned-by-timeout.
                finished = worker.wait(3000)
                if not finished:
                    logger.warning(
                        "aboutToQuit: worker.wait(3000) timed out — "
                        "QThread orphaned at process exit (relies on "
                        "#460 KILL_ON_JOB_CLOSE to reap exiftool)"
                    )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("aboutToQuit worker cleanup failed: {}", exc)

    try:
        # complete() drains the enqueue=True background queue; remove()
        # then closes every sink so the log file handle is released.
        logger.complete()
        logger.remove()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


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
    win = MainWindow(vm=vm, image_service=image_service, settings=settings)
    win.refresh_tree(vm.groups)
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

    # #473 — graceful-shutdown hook. Pairs with #460 (Job Object kills
    # exiftool on hard exit) and #468 (main-window scan guard): this
    # covers the Qt-detected quit path. Lambda captures ``app`` so the
    # cleanup function gets the right QApplication instance.
    app.aboutToQuit.connect(lambda: _cleanup_on_quit(app))

    # Initialize translation catalogs (YAML + Qt's bundled qtbase_*.qm)
    # for the persisted ui.locale. Same helper used by the live
    # language switch.
    install_locale_translators(app, settings)

    img = ImageService(settings)
    default_sort = _parse_default_sort(settings)
    vm = MainVM(default_sort=default_sort)

    # #469 — HEIC diagnostics: log supported formats and try WIC 512/1024 on
    # the first HEIC. Gated on PHOTO_MANAGER_HEIC_DIAG so normal startups
    # stay quiet — the block is a developer probe (not user telemetry), and
    # on NAS-backed first-HEIC paths the synchronous WIC + COM init on the
    # main thread before app.exec() can make the app appear to "not launch"
    # for several seconds. Keep the probe available by env var; off by
    # default.
    if os.environ.get("PHOTO_MANAGER_HEIC_DIAG"):
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

    # Probe: auto-load manifest from --manifest <path> or PHOTO_MANAGER_PROBE_MANIFEST.
    # Supports PHOTO_MANAGER_PROBE_RELOAD_COUNT=N for N sequential reload measurements
    # with 7-second gaps (enough for Point 5 idle snapshot to fire between loads).
    _probe_manifest: str | None = None
    for _i, _arg in enumerate(sys.argv[1:], 1):
        if _arg == "--manifest" and _i < len(sys.argv):
            _probe_manifest = sys.argv[_i + 1]
            break
    if _probe_manifest is None:
        _probe_manifest = os.environ.get("PHOTO_MANAGER_PROBE_MANIFEST")

    if _probe_manifest:
        _probe_reload_count = max(1, int(os.environ.get("PHOTO_MANAGER_PROBE_RELOAD_COUNT", "1")))

        def _schedule_loads(remaining: int, delay_ms: int = 500) -> None:
            from PySide6.QtCore import QTimer
            from pathlib import Path as _Path

            def _do_load() -> None:
                win.file_operations._start_manifest_load(str(_Path(_probe_manifest)))
                if remaining > 1:
                    # 7 seconds: enough time for Point 5 (5s idle) to fire between loads.
                    _schedule_loads(remaining - 1, delay_ms=7_000)

            QTimer.singleShot(delay_ms, _do_load)

        _schedule_loads(_probe_reload_count)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
