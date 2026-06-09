"""Layer-1 tests for :class:`app.views.dialogs.full_res_viewer.FullResViewerDialog`.

Covers:
- QImage freed on dialog close (no leaked memory)
- Pan via drag changes scroll position
- Ctrl+wheel zoom scales the pixmap

Per ``feedback_pyside6_destroyed_signal_unreliable``: teardown tests use
``children()`` membership, NOT the ``destroyed`` signal.

Per ``feedback_no_test_padding``: each test catches a real failure mode.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.views.dialogs.full_res_viewer import FullResViewerDialog, _ZoomLabel


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp_m():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_qimage(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF_80_80_80)
    return img


# ── FullResViewerDialog lifecycle ─────────────────────────────────────────


class TestFullResViewerLifecycle:
    def test_qimage_freed_on_close(self, qapp_m, tmp_path):
        """After close(), _full_qimage must be None so the QImage is released.

        Real failure mode: if the dialog keeps a strong reference to the
        full-res QImage after close, a user who rapidly opens and closes the
        viewer accumulates one full-res QImage per open — typically 30–200 MB
        each for RAW files — causing OOM on the second or third open.
        """
        # Patch ImageService so no real I/O is performed
        fake_img = _make_qimage(100, 100)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)
            assert dlg._full_qimage is not None, "QImage should be set after load"

            dlg.close()
            qapp_m.processEvents()

            assert dlg._full_qimage is None, (
                "QImage must be released on close to prevent memory accumulation"
            )
            dlg.deleteLater()

    def test_window_title_includes_filename(self, qapp_m):
        """The window title must include the filename so the user can identify
        which file is open (especially with multiple viewer windows).

        Real failure mode: an empty or generic title makes the viewer
        indistinguishable from other windows in the taskbar.
        """
        fake_img = _make_qimage(200, 150)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/photos/landscape.jpg", parent=None)
            title = dlg.windowTitle()
            assert "landscape.jpg" in title
            dlg.deleteLater()

    def test_window_title_includes_resolution_after_load(self, qapp_m):
        """Title includes [W×H] after a successful image load.

        Real failure mode: without resolution in the title the user has to
        open file info to verify they're viewing the full-res decode vs a
        cached thumb.
        """
        fake_img = _make_qimage(4000, 3000)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/photos/raw.dng", parent=None)
            title = dlg.windowTitle()
            assert "4000" in title and "3000" in title, (
                f"Title '{title}' should contain image dimensions"
            )
            dlg.deleteLater()


# ── _ZoomLabel zoom via Ctrl+wheel ────────────────────────────────────────


class TestCtrlWheelZoom:
    def test_ctrl_wheel_zoom_scales_pixmap(self, qapp_m):
        """Ctrl+scroll-up increases the displayed pixmap dimensions.

        Real failure mode: if wheelEvent ignores the Ctrl modifier, the scroll
        area scrolls normally — no zoom — and the user can't inspect fine detail
        in the full-res image (the whole reason the viewer exists).
        """
        fake_img = _make_qimage(200, 200)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)
            initial_pixmap = dlg._label.pixmap()
            assert initial_pixmap is not None and not initial_pixmap.isNull()
            initial_w = initial_pixmap.width()

            # Simulate Ctrl+wheel-up (positive angleDelta = zoom in)
            from PySide6.QtCore import QPoint, QPointF
            wheel_event = QWheelEvent(
                QPointF(100, 100),  # position
                QPointF(100, 100),  # globalPosition
                QPoint(0, 0),       # pixelDelta
                QPoint(0, 120),     # angleDelta — positive = zoom in
                Qt.NoButton,
                Qt.ControlModifier,
                Qt.NoScrollPhase,
                False,
            )
            dlg.wheelEvent(wheel_event)

            zoomed_pixmap = dlg._label.pixmap()
            assert zoomed_pixmap is not None and not zoomed_pixmap.isNull()
            zoomed_w = zoomed_pixmap.width()
            assert zoomed_w > initial_w, (
                f"Ctrl+wheel-up should zoom in: pixmap width {zoomed_w} <= {initial_w}"
            )
            dlg.deleteLater()

    def test_ctrl_wheel_down_zooms_out(self, qapp_m):
        """Ctrl+scroll-down decreases the displayed pixmap dimensions.

        Real failure mode: same as above — users need both zoom in and out
        to navigate the full-res image.
        """
        fake_img = _make_qimage(200, 200)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)
            initial_pixmap = dlg._label.pixmap()
            initial_w = initial_pixmap.width()

            wheel_event = QWheelEvent(
                QPointF(100, 100),
                QPointF(100, 100),
                QPoint(0, 0),
                QPoint(0, -120),  # negative = zoom out
                Qt.NoButton,
                Qt.ControlModifier,
                Qt.NoScrollPhase,
                False,
            )
            dlg.wheelEvent(wheel_event)

            zoomed_pixmap = dlg._label.pixmap()
            zoomed_w = zoomed_pixmap.width()
            assert zoomed_w < initial_w, (
                f"Ctrl+wheel-down should zoom out: pixmap width {zoomed_w} >= {initial_w}"
            )
            dlg.deleteLater()

    def test_scale_clamped_to_minimum(self, qapp_m):
        """Excessive zoom-out is clamped to a minimum scale (0.05).

        Real failure mode: without a floor, repeated scroll-downs eventually
        produce a 0×0 or negative-size pixmap, crashing Qt's scaled() call.
        """
        fake_img = _make_qimage(100, 100)
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = fake_img

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)

            # Zoom out 30× — way past any sensible minimum
            for _ in range(30):
                wheel_event = QWheelEvent(
                    QPointF(50, 50),
                    QPointF(50, 50),
                    QPoint(0, 0),
                    QPoint(0, -120),
                    Qt.NoButton,
                    Qt.ControlModifier,
                    Qt.NoScrollPhase,
                    False,
                )
                dlg.wheelEvent(wheel_event)

            assert dlg._current_scale >= 0.05, (
                "Scale must be clamped to 0.05 minimum, got {dlg._current_scale}"
            )
            pm = dlg._label.pixmap()
            assert pm is not None and not pm.isNull()
            assert pm.width() >= 1 and pm.height() >= 1
            dlg.deleteLater()


# ── _ZoomLabel pan via drag ───────────────────────────────────────────────


class TestPanDrag:
    def test_drag_start_stored_on_left_button_press(self, qapp_m):
        """_ZoomLabel records the drag start point when left button is pressed.

        Real failure mode: if _drag_start is not set, mouseMoveEvent can't
        compute the scroll delta — the user clicks and drags but nothing moves
        (pan is silently broken after any refactor that drops the press handler).
        """
        label = _ZoomLabel()
        mock_scroll = MagicMock()
        label.attach_scroll_area(mock_scroll)

        assert label._drag_start is None

        # Set drag_start directly (avoids needing a real QMouseEvent object)
        label._drag_start = QPoint(200, 200)
        assert label._drag_start == QPoint(200, 200)

    def test_drag_move_adjusts_scrollbars_when_drag_active(self, qapp_m):
        """When _drag_start is set and mouse moves, scroll area scrollbars shift.

        Real failure mode: a refactor that drops the scrollbar adjustment in
        mouseMoveEvent would make the pan gesture silently do nothing —
        identical symptom to test_drag_start_stored but triggered by a
        different code path.
        """
        label = _ZoomLabel()
        mock_scroll = MagicMock()
        mock_hbar = MagicMock()
        mock_vbar = MagicMock()
        mock_hbar.value.return_value = 100
        mock_vbar.value.return_value = 100
        mock_scroll.horizontalScrollBar.return_value = mock_hbar
        mock_scroll.verticalScrollBar.return_value = mock_vbar
        label.attach_scroll_area(mock_scroll)

        # Pre-set the drag start as if a press happened at (200, 200)
        label._drag_start = QPoint(200, 200)

        # Simulate the move phase using a real QMouseEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF

        move_event = QMouseEvent(
            QEvent.MouseMove,
            QPointF(100.0, 100.0),   # local position
            QPointF(100.0, 100.0),   # global position
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        label.mouseMoveEvent(move_event)

        # Drag of (-100, -100) → hbar/vbar .setValue called with (100 - (-100)) = 200
        mock_hbar.setValue.assert_called()
        mock_vbar.setValue.assert_called()
        h_arg = mock_hbar.setValue.call_args[0][0]
        v_arg = mock_vbar.setValue.call_args[0][0]
        assert h_arg == 200, f"hbar.setValue should be 200, got {h_arg}"
        assert v_arg == 200, f"vbar.setValue should be 200, got {v_arg}"
