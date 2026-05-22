"""Tests for app/views/window_state.py — geometry persistence helpers (#215).

The save/restore plumbing around Qt's own ``saveGeometry`` /
``restoreGeometry`` is uniform infrastructure — these tests target the
pieces that are NOT just "Qt does its thing":

  * ``qsettings_path`` resolves under ``PHOTO_MANAGER_HOME`` correctly
    (the QA batch and dev runs depend on this isolation).
  * ``is_rect_visible_on_any_screen`` rejects rects that overlap a
    screen by < 25% of their area — the multi-monitor-disconnect
    failure mode the acceptance criteria call out.
  * ``restore_widget_geometry`` rejects off-screen blobs and leaves
    the widget's pre-call geometry untouched, so caller defaults
    survive a stale INI from a since-unplugged monitor.

Mocking QSettings just to bump branch coverage would be metric gaming
(see CLAUDE.md — "no test padding"). Where we DO touch QSettings, we
use a real INI in tmp_path so the test catches actual round-trip
failures.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QDialog

from app.views.window_state import (
    is_rect_visible_on_any_screen,
    qsettings_path,
    restore_widget_geometry,
    save_widget_geometry,
    window_state_qsettings,
)


# ---------------------------------------------------------------------------
# qsettings_path
# ---------------------------------------------------------------------------


class TestQSettingsPath:
    def test_falls_back_to_repo_root_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("PHOTO_MANAGER_HOME", raising=False)
        path = qsettings_path()
        assert path.name == "window_state.ini"
        # parent should be the repo root (two ``parents[]`` up from
        # ``app/views/window_state.py``).
        assert path.parent.is_dir()

    def test_respects_photo_manager_home(self, monkeypatch, tmp_path):
        # The helper resolves the env value AGAINST the repo root, so
        # we use a relative subdir (mirroring how the qa batch sets
        # PHOTO_MANAGER_HOME=qa).
        monkeypatch.setenv("PHOTO_MANAGER_HOME", "qa")
        path = qsettings_path()
        assert path.name == "window_state.ini"
        assert path.parent.name == "qa"


# ---------------------------------------------------------------------------
# is_rect_visible_on_any_screen
# ---------------------------------------------------------------------------


class TestIsRectVisibleOnAnyScreen:
    """The off-screen guard's responsibility: decide whether a restored
    rect would land somewhere the user can actually reach with the
    mouse. Empty / negative-area rects are off-screen by definition;
    a sliver overlap (e.g. 1px of a window peeks onto the primary
    screen) is off-screen because the user can't grab the title bar
    to drag it back. >= 25% is on-screen — large enough that the user
    can reasonably grab the title bar.
    """

    def test_empty_rect_is_off_screen(self, qapp):
        assert not is_rect_visible_on_any_screen(QRect(0, 0, 0, 0))

    def test_negative_size_rect_is_off_screen(self, qapp):
        # QRect with negative width is "invalid" / empty per Qt.
        assert not is_rect_visible_on_any_screen(QRect(0, 0, -10, -10))

    def test_rect_inside_primary_screen_is_on_screen(self, qapp):
        # 200x200 at (100, 100) — comfortably inside any reasonable
        # primary screen the test runner has.
        assert is_rect_visible_on_any_screen(QRect(100, 100, 200, 200))

    def test_rect_far_off_screen_is_off_screen(self, qapp):
        # Far enough from origin that no real screen geometry covers
        # this region (50_000 px is well past the rightmost edge of
        # any plausible multi-monitor span).
        assert not is_rect_visible_on_any_screen(
            QRect(50_000, 50_000, 200, 200)
        )

    def test_sliver_overlap_is_off_screen(self, qapp):
        """A rect mostly off-screen with only a few pixels overlapping
        should still be rejected — < 25% visible means the user can't
        grab the title bar."""
        screen = QApplication.instance().primaryScreen()
        avail = screen.availableGeometry()
        # Rect that overhangs the right edge by all but 5 pixels.
        # Total area 1000x500=500_000; overlap is 5x500=2_500 px
        # (0.5%, well below 25%).
        rect = QRect(avail.right() - 5, avail.top(), 1000, 500)
        assert not is_rect_visible_on_any_screen(rect)

    def test_majority_overlap_is_on_screen(self, qapp):
        """A rect with > 25% overlap counts as visible."""
        screen = QApplication.instance().primaryScreen()
        avail = screen.availableGeometry()
        # Rect that overhangs the right edge by half its width —
        # 50% overlap.
        rect = QRect(avail.right() - 200, avail.top() + 100, 400, 300)
        assert is_rect_visible_on_any_screen(rect)


# ---------------------------------------------------------------------------
# restore_widget_geometry — the off-screen guard's behavioural contract
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_qsettings(monkeypatch, tmp_path):
    """Point ``PHOTO_MANAGER_HOME`` at a tmp dir so we get a clean INI.

    The helper resolves ``PHOTO_MANAGER_HOME`` against the repo root,
    so a relative subdir under tmp_path doesn't quite fit. We use a
    relative path that exists under cwd via a chdir.
    """
    # Stash the absolute path the helper would compute. We can't
    # easily redirect it without monkeypatching window_state itself —
    # but we CAN delete + write the INI by computing the same path.
    monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path.name))
    # Helper resolves under repo root; make the dir exist.
    repo_root = qsettings_path().parent
    repo_root.mkdir(parents=True, exist_ok=True)
    ini = qsettings_path()
    if ini.exists():
        ini.unlink()
    yield ini
    if ini.exists():
        ini.unlink()
    # tmp parent created under repo root — leave it; harmless empty dir.


class TestRestoreWidgetGeometry:
    def test_returns_false_when_no_saved_blob(self, qapp, isolated_qsettings):
        dlg = QDialog()
        dlg.resize(400, 300)
        original = dlg.saveGeometry()
        applied = restore_widget_geometry(dlg, "geometry/missing_key")
        assert applied is False
        # The widget's geometry was not touched.
        assert dlg.saveGeometry() == original

    def test_round_trip_restores_size(self, qapp, isolated_qsettings):
        """Save a known on-screen geometry, then restore it on a fresh
        widget — the size must come back through."""
        dlg_a = QDialog()
        dlg_a.resize(500, 350)
        dlg_a.move(100, 100)
        save_widget_geometry(dlg_a, "geometry/round_trip")

        dlg_b = QDialog()
        dlg_b.resize(200, 200)  # different from saved
        applied = restore_widget_geometry(dlg_b, "geometry/round_trip")
        assert applied is True
        # Width and height match (within rounding — Qt geometry storage
        # is exact for these in-process round-trips). Use saveGeometry
        # blob equality to dodge platform frame-size noise.
        assert dlg_b.size().width() == 500
        assert dlg_b.size().height() == 350

    def test_off_screen_blob_reverts_to_pre_state(
        self, qapp, isolated_qsettings, monkeypatch
    ):
        """If the saved rect is off-screen (multi-monitor disconnect),
        the restore must NOT apply and the widget's pre-call geometry
        must remain — the dialog's hardcoded defaults stay in force.
        """
        # Save geometry from a dialog placed wherever (on-screen), then
        # force the off-screen check to return False at restore time.
        # This isolates the guard's revert step from any specific
        # screen-coord arithmetic the test runner happens to have.
        dlg_a = QDialog()
        dlg_a.resize(500, 350)
        save_widget_geometry(dlg_a, "geometry/offscreen")

        import app.views.window_state as ws
        monkeypatch.setattr(
            ws, "is_rect_visible_on_any_screen", lambda _rect: False
        )

        dlg_b = QDialog()
        dlg_b.resize(400, 250)
        pre_size = dlg_b.size()
        applied = restore_widget_geometry(dlg_b, "geometry/offscreen")
        assert applied is False
        # Revert leaves the widget at its pre-call size. We compare
        # size (not the raw saveGeometry blob) because Qt's geometry
        # blob includes window-state bits that flip on restore even
        # for a no-op round-trip — what users notice is "did the
        # dialog change shape", and that's the size.
        assert dlg_b.size() == pre_size

    def test_corrupt_blob_returns_false(self, qapp, isolated_qsettings):
        """Qt's restoreGeometry returns False on a blob it can't parse;
        the helper must propagate that as ``applied=False`` rather than
        raising."""
        store = window_state_qsettings()
        store.setValue("geometry/corrupt", b"not a real geometry blob")
        store.sync()
        dlg = QDialog()
        applied = restore_widget_geometry(dlg, "geometry/corrupt")
        assert applied is False


# ---------------------------------------------------------------------------
# save_widget_geometry — silent on storage failure
# ---------------------------------------------------------------------------


class TestSaveWidgetGeometry:
    def test_save_persists_blob(self, qapp, isolated_qsettings):
        dlg = QDialog()
        dlg.resize(600, 400)
        save_widget_geometry(dlg, "geometry/persist_check")
        store = window_state_qsettings()
        blob = store.value("geometry/persist_check")
        assert blob is not None
        assert len(blob) > 0

    def test_save_swallows_setvalue_error(
        self, qapp, isolated_qsettings, monkeypatch
    ):
        """Storage failure must never abort the dialog's close path —
        next launch just falls back to defaults. Drives the broad
        except-Exception in save_widget_geometry, which exists for
        exactly this reason (read-only INI dirs in locked-down env's,
        Windows roaming-profile contention)."""
        import app.views.window_state as ws

        class _BrokenSettings:
            def setValue(self, *_args, **_kwargs):
                raise OSError("simulated INI write failure")

            def sync(self):
                pass

        monkeypatch.setattr(ws, "window_state_qsettings", _BrokenSettings)
        dlg = QDialog()
        # Should not raise.
        save_widget_geometry(dlg, "geometry/broken")


class TestActionDialogNoHardcodedResize:
    """#215 — hardcoded ``self.resize(780, 420)`` was replaced by a
    geometry restore. Pin that the hardcoded-pixel form doesn't drift
    back in via a careless edit — the issue requires "treat hardcoded
    values as defaults only", and Qt's default-sizing flow is the source
    of truth now.

    Wave 8 (E5 from #351) added a user-initiated "Reset window size"
    affordance that calls ``self.resize(self.minimumSize())`` — that
    explicit defaults-driven shape is allowed; only literal-pixel
    ``self.resize(NNN, MMM)`` calls violate #215.
    """

    def test_action_dialog_source_has_no_hardcoded_resize_call(self):
        import re as _re
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "app" / "views" / "dialogs" / "select_dialog.py"
        text = src.read_text(encoding="utf-8")
        code_lines = [
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        ]
        # Find every line containing `self.resize(`. The only allowed
        # shape is `self.resize(self.minimumSize())` (E5 from #351, Wave 8 —
        # user-initiated reset back to setMinimumSize defaults).
        # Blocked shape: `self.resize(780, 420)` (the #215 antipattern).
        for line in code_lines:
            if "self.resize(" not in line:
                continue
            stripped = line.strip()
            assert stripped == "self.resize(self.minimumSize())", (
                f"Hardcoded resize call found: {stripped!r}. "
                f"ActionDialog geometry must come from setMinimumSize + "
                f"restore_widget_geometry (#215). The only allowed "
                f"resize call is `self.resize(self.minimumSize())` "
                f"(E5 reset, Wave 8)."
            )


class TestActionDialogDoneSavesGeometry:
    """``done()`` is the unified close-path hook (#215). The flat
    layout (no ``match_fn``) deliberately skips the save because its
    geometry isn't user-resizable in a meaningful way. The
    preview-enabled layout (with ``match_fn``) must save."""

    def test_done_with_match_fn_saves_geometry(
        self, qapp, isolated_qsettings
    ):
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.window_state import QSETTINGS_KEY_ACTION_DIALOG_GEOM
        # match_fn need only be callable — the dialog calls it on
        # field change, not during done().
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda _f, _p: (0, 0, []),
        )
        dlg.resize(900, 500)
        dlg.done(0)  # rejected
        store = window_state_qsettings()
        blob = store.value(QSETTINGS_KEY_ACTION_DIALOG_GEOM)
        assert blob is not None and len(blob) > 0

    def test_done_without_match_fn_skips_save(
        self, qapp, isolated_qsettings
    ):
        """Flat layout has no resizable splitter — saving its size
        would write a useless blob and risk reloading a stale rect
        into the preview-enabled layout next time."""
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.window_state import QSETTINGS_KEY_ACTION_DIALOG_GEOM
        dlg = ActionDialog(fields=["File Name"], match_fn=None)
        dlg.done(0)
        store = window_state_qsettings()
        assert store.value(QSETTINGS_KEY_ACTION_DIALOG_GEOM) is None


class TestExecuteActionDialogDoneSavesGeometry:
    def test_done_saves(self, qapp, isolated_qsettings):
        from app.views.dialogs.execute_action_dialog import (
            ExecuteActionDialog,
        )
        from app.views.window_state import (
            QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM,
        )
        dlg = ExecuteActionDialog(groups=[], manifest_path=None)
        dlg.resize(1100, 700)
        dlg.done(0)
        store = window_state_qsettings()
        blob = store.value(QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM)
        assert blob is not None and len(blob) > 0


class TestSaveManifestDialogGeomKey:
    """#230 — the new ``QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM`` is the
    wiring between the FileOperationsHandler save flow and the existing
    save/restore helpers. The handler-level tests verify the call sites,
    but a misspelled or duplicated key here would silently lose geometry
    or collide with another dialog's blob. This round-trip pins the key
    string against accidental drift and confirms the helpers accept it.
    """

    def test_round_trip_with_save_manifest_key(
        self, qapp, isolated_qsettings
    ):
        from PySide6.QtWidgets import QFileDialog
        from app.views.window_state import (
            QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM,
        )

        # Dimensions match the precedent in ``test_round_trip_restores_size``
        # — small enough to fit on the hosted-CI Windows runners (some
        # have an 800px-wide virtual screen, so anything >= 900 gets
        # clamped on restore and the round-trip looks "off by 100px").
        dlg_a = QFileDialog()
        dlg_a.resize(500, 350)
        dlg_a.move(100, 100)
        save_widget_geometry(dlg_a, QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM)

        store = window_state_qsettings()
        blob = store.value(QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM)
        assert blob is not None and len(blob) > 0

        dlg_b = QFileDialog()
        dlg_b.resize(200, 200)
        applied = restore_widget_geometry(
            dlg_b, QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM
        )
        assert applied is True
        assert dlg_b.size().width() == 500
        assert dlg_b.size().height() == 350


class TestScanDialogDoneSavesGeometry:
    def test_done_saves(self, qapp, isolated_qsettings, tmp_path):
        """ScanDialog needs a JsonSettings instance; tmp_path keeps it
        isolated. The done() hook persists geometry regardless of
        whether settings were touched."""
        import json
        from app.views.dialogs.scan_dialog import ScanDialog
        from app.views.window_state import QSETTINGS_KEY_SCAN_DIALOG_GEOM
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        dlg.resize(1300, 800)
        dlg.done(0)
        store = window_state_qsettings()
        blob = store.value(QSETTINGS_KEY_SCAN_DIALOG_GEOM)
        assert blob is not None and len(blob) > 0
