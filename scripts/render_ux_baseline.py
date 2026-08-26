"""Render a UX baseline: offscreen screenshots of the main view + key dialogs.

Phase-0 tool for the UI/UX refinement effort. Constructs the real
:class:`MainWindow` (and the Execute Action / Set Action dialogs) in-process
against a *synthetic* manifest that deliberately exercises every visible state
of the results tree — ``Ref`` / ``100%`` / ``N%`` / ``N*%`` / ``—`` similarity
labels, locked rows, all three decision states, scored + unscored rows — then
``.grab()``s each surface to a PNG.

Why synthetic (not a real manifest): real manifests embed the user's actual
file paths (PII). Synthetic data keeps the baseline shareable and lets us pin
exactly which UX states appear.

Isolation: every byte of persisted state (``settings.json``, ``window_state.ini``,
the QSettings column/geometry blobs) is anchored under a disposable
``PHOTO_MANAGER_HOME`` so running this never reads or overwrites the dev app's
real window layout. The process hard-exits before Qt's ``aboutToQuit`` cleanup
fires, so nothing is written back.

Usage::

    .venv/Scripts/python.exe scripts/render_ux_baseline.py [OUTPUT_DIR]

Defaults to ``docs/audits/ux-baseline-<today>/``. This is a dev/QA helper, not
shipped app code — no unit tests (it *is* a rendering harness).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# --- Isolation: MUST be set before any PySide6 / app import ------------------
# NOTE: we deliberately do NOT force QT_QPA_PLATFORM=offscreen — that platform
# ships a font DB without glyphs, so every label renders as tofu (□). The
# native platform renders text correctly; the window flashes briefly and the
# process self-exits. Set QT_QPA_PLATFORM=offscreen yourself only if you accept
# tofu text (e.g. true headless CI where only layout matters).
_SANDBOX_REL = "qa/sandbox/_disposable/ux_baseline_home"
(REPO / _SANDBOX_REL).mkdir(parents=True, exist_ok=True)
os.environ["PHOTO_MANAGER_HOME"] = _SANDBOX_REL  # anchors settings.json + window_state.ini

# Make the repo importable when run as a bare script.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from app.views.constants import IGNORE_DECISION, headers  # noqa: E402
from app.views.dialogs.execute_action_dialog import ExecuteActionDialog  # noqa: E402
from app.views.dialogs.select_dialog import ActionDialog  # noqa: E402
from app.views.main_window import MainWindow  # noqa: E402
from app.views.theme import apply_theme  # noqa: E402
from app.viewmodels.main_vm import MainVM  # noqa: E402
from core.models import PhotoGroup, PhotoRecord  # noqa: E402
from infrastructure.i18n import init_translator  # noqa: E402
from infrastructure.image_service import ImageService  # noqa: E402
from infrastructure.settings import JsonSettings  # noqa: E402

# pHash references (16 hex = 64 bits). Distances below are chosen so the
# similarity column renders human-recognisable percentages.
_REF_A = "ffffffffffffffff"   # group-1 reference
_REF_C = "aaaaaaaaaaaaaaaa"   # group-3 reference (RAW+JPG companion)

_T0 = datetime(2026, 5, 14, 9, 41, 12)


def _img_dir() -> Path:
    d = REPO / _SANDBOX_REL / "imgs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_sample_image(name: str, w: int, h: int, color: tuple[int, int, int]) -> str:
    """Write a tiny real image so the preview pane has something to render.

    Falls back to just returning the path if Pillow can't write (the tree
    screenshots don't depend on the file existing).
    """
    path = _img_dir() / name
    try:
        from PIL import Image, ImageDraw

        im = Image.new("RGB", (w, h), color)
        d = ImageDraw.Draw(im)
        d.rectangle([w * 0.1, h * 0.1, w * 0.9, h * 0.9], outline=(255, 255, 255), width=4)
        d.text((w * 0.15, h * 0.15), name, fill=(255, 255, 255))
        im.save(path)
    except Exception:  # pragma: no cover - preview is best-effort
        pass
    return str(path)


def _rec(**kw: object) -> PhotoRecord:
    base: dict[str, object] = dict(
        group_number=kw["group_number"],
        is_mark=False,
        is_locked=False,
        folder_path=kw.get("folder_path", str(_img_dir())),
        file_path=kw["file_path"],
        capture_date=None,
        modified_date=None,
        file_size_bytes=kw.get("file_size_bytes", 0),
        creation_date=kw.get("creation_date"),
        shot_date=kw.get("shot_date"),
        pixel_width=kw.get("pixel_width"),
        pixel_height=kw.get("pixel_height"),
        action=kw.get("action", ""),
        user_decision=kw.get("user_decision", ""),
        hamming_distance=kw.get("hamming_distance"),
        phash=kw.get("phash"),
        score=kw.get("score"),
    )
    return PhotoRecord(**base)  # type: ignore[arg-type]


def build_sample_groups() -> list[PhotoGroup]:
    """Synthetic manifest covering every results-tree visual state."""
    # Real images are written to the sandbox so the preview pane has bytes to
    # decode, but the displayed Folder column reads `folder_path` (a synthetic
    # value) — the real on-disk path lives only in the hidden PATH_ROLE and is
    # never rendered. This keeps the screenshots free of the machine's home
    # path (PII) while still showing a real thumbnail.
    ref_img = _make_sample_image("IMG_4471.jpg", 1200, 900, (70, 110, 160))
    dup_img = _make_sample_image("IMG_4472.jpg", 1200, 900, (72, 112, 158))
    _FAKE_FOLDER = "D:/iPhone/2026/05/"

    # Group 1 — classic Live-Photo burst: Ref (scored HEIC), its MOV
    # passenger ("—", unscored video), and two near-dup REVIEW_DUPLICATE rows
    # marked for delete (95% and 88%).
    g1 = PhotoGroup(
        group_number=1,
        items=[
            _rec(group_number=1, folder_path=_FAKE_FOLDER, file_path=ref_img,
                 file_size_bytes=3_812_044,
                 creation_date=_T0, shot_date=_T0, pixel_width=4032, pixel_height=3024,
                 action="", score=0.92, phash=_REF_A),
            _rec(group_number=1, folder_path=_FAKE_FOLDER, file_path=str(_img_dir() / "IMG_4471.MOV"),
                 file_size_bytes=1_204_887, creation_date=_T0, shot_date=_T0,
                 action="", score=None, phash=None),  # Live Photo MOV -> "—"
            _rec(group_number=1, folder_path=_FAKE_FOLDER, file_path=dup_img,
                 file_size_bytes=3_902_511,
                 creation_date=_T0 + timedelta(seconds=2), shot_date=_T0 + timedelta(seconds=2),
                 pixel_width=4032, pixel_height=3024,
                 action="REVIEW_DUPLICATE", score=0.81, user_decision="delete",
                 phash="fffffffffffffff8"),  # ~95%
            _rec(group_number=1, folder_path=_FAKE_FOLDER, file_path=str(_img_dir() / "IMG_4473.jpg"),
                 file_size_bytes=3_701_220, creation_date=_T0 + timedelta(seconds=4),
                 shot_date=_T0 + timedelta(seconds=4), pixel_width=4032, pixel_height=3024,
                 action="REVIEW_DUPLICATE", score=0.74, user_decision="delete",
                 phash="ffffffffffffff00"),  # ~88%
        ],
    )

    # Group 2 — exact duplicate across two folders. One row 100% + delete,
    # and a locked row to show the 🔒 column and "remove from list".
    t2 = datetime(2025, 12, 1, 18, 3, 0)
    g2 = PhotoGroup(
        group_number=2,
        items=[
            _rec(group_number=2, folder_path="D:/Photos/2025/", file_path="D:/Photos/2025/DSC_0012.JPG",
                 file_size_bytes=5_001_233, creation_date=t2, shot_date=t2,
                 pixel_width=6000, pixel_height=4000, action="", score=0.88, phash=_REF_C),
            _rec(group_number=2, folder_path="D:/Backup/2025/", file_path="D:/Backup/2025/DSC_0012.JPG",
                 file_size_bytes=5_001_233, creation_date=t2, shot_date=t2,
                 pixel_width=6000, pixel_height=4000, action="EXACT", score=0.88,
                 user_decision="delete", phash=_REF_C),
            _rec(group_number=2, folder_path="D:/Backup/2025/", file_path="D:/Backup/2025/DSC_0012 (copy).JPG",
                 file_size_bytes=5_001_233, creation_date=t2, shot_date=t2,
                 pixel_width=6000, pixel_height=4000, action="EXACT", score=0.88,
                 user_decision=IGNORE_DECISION, phash=_REF_C),
        ],
    )
    g2.items[0].is_locked = True  # show 🔒 on the kept reference

    # Group 3 — same-stem RAW+JPG companion: JPG wins Ref (lex), RAF renders
    # as an "N*%" passenger with a tooltip (#536 Direction A).
    t3 = datetime(2026, 3, 22, 14, 27, 5)
    g3 = PhotoGroup(
        group_number=3,
        items=[
            _rec(group_number=3, folder_path="D:/Fuji/", file_path="D:/Fuji/_DSF1009.JPG",
                 file_size_bytes=8_440_120, creation_date=t3, shot_date=t3,
                 pixel_width=6240, pixel_height=4160, action="", score=0.95, phash=_REF_C),
            _rec(group_number=3, folder_path="D:/Fuji/", file_path="D:/Fuji/_DSF1009.RAF",
                 file_size_bytes=54_220_880, creation_date=t3, shot_date=t3,
                 pixel_width=6240, pixel_height=4160, action="", score=0.95,
                 phash="aaaaaaaaaaaaaaa0"),  # ~97*% passenger
        ],
    )

    return [g1, g2, g3]


def _save(widget, out: Path, name: str, app: QApplication) -> None:
    app.processEvents()
    pm = widget.grab()
    dest = out / name
    pm.save(str(dest))
    print(f"  wrote {dest.relative_to(REPO)}  ({pm.width()}x{pm.height()})")


def _tidy_columns(win: MainWindow) -> None:
    header = win.tree.header()
    for i in range(header.count()):
        win.tree.resizeColumnToContents(i)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        REPO / "docs" / "audits" / f"ux-baseline-{datetime.now():%Y-%m-%d}"
    )
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    apply_theme(app)  # Daylight · light — same stylesheet the real app installs.
    settings = JsonSettings(REPO / _SANDBOX_REL / "settings.json")
    init_translator(settings.get("ui.locale", "en") or "en", REPO / "translations")
    img = ImageService(settings)

    groups = build_sample_groups()
    vm = MainVM(default_sort=[])
    win = MainWindow(vm=vm, image_service=img, settings=settings)
    win.resize(1560, 880)

    # 1) Empty state (pre-manifest) — Scan Sources… / Open Manifest… buttons.
    win.refresh_tree([])
    win.show()
    _save(win, out, "01_main_empty_state.png", app)

    # 2) Populated + expanded (default).
    vm.groups = groups
    win.refresh_tree(groups)
    _tidy_columns(win)
    win.show()
    # Select the group-1 reference so the preview pane renders a real image.
    proxy = win.tree.model()
    if proxy is not None and proxy.rowCount() > 0:
        grp0 = proxy.index(0, 0)
        if proxy.rowCount(grp0) > 0:
            child = proxy.index(0, 0, grp0)
            win.tree.setCurrentIndex(child)
            win.tree.selectionModel().select(
                child, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
            )
    for _ in range(40):  # let async preview decode settle
        app.processEvents()
    _save(win, out, "02_main_expanded.png", app)

    # 3) Collapsed groups.
    win.tree.collapseAll()
    _save(win, out, "03_main_collapsed.png", app)

    # 4) Execute Action dialog (shows only rows with a decision).
    try:
        exec_dlg = ExecuteActionDialog(
            groups=groups, manifest_path=None, parent=win, settings=settings,
            task_runner=None, status_reporter=None,
        )
        exec_dlg.resize(1240, 720)
        exec_dlg.show()
        _save(exec_dlg, out, "04_execute_action_dialog.png", app)
        exec_dlg.close()
    except Exception as exc:  # pragma: no cover
        print(f"  [skip] ExecuteActionDialog: {exc!r}")

    # 5) Set Action (Field/Regex) dialog.
    try:
        action_dlg = ActionDialog(
            fields=list(headers()), parent=win, groups=groups, context_id="ux_baseline",
            settings=settings,
        )
        action_dlg.resize(900, 640)
        action_dlg.show()
        _save(action_dlg, out, "05_set_action_dialog.png", app)
        action_dlg.close()
    except Exception as exc:  # pragma: no cover
        print(f"  [skip] ActionDialog: {exc!r}")

    print(f"\nBaseline written to {out.relative_to(REPO)}")
    # Hard-exit before Qt aboutToQuit cleanup so nothing persists to the sandbox.
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
