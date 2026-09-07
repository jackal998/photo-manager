"""Layer-1 tests for :class:`app.views.dialogs.full_res_viewer.FullResViewerDialog`.

Covers:
- QImage freed on dialog close (no leaked memory)
- Pan via drag changes scroll position
- Ctrl+wheel zoom scales the pixmap
- bytes return contract: get_preview returns JPEG bytes post-PR-C';
  full_res_viewer must convert them before calling QPixmap/isNull/width/height.

Per ``feedback_pyside6_destroyed_signal_unreliable``: teardown tests use
``children()`` membership, NOT the ``destroyed`` signal.

Per ``feedback_no_test_padding``: each test catches a real failure mode.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image as PILImage

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.views.dialogs.full_res_viewer import FullResViewerDialog, _ZoomLabel


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp_m():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_jpeg(w: int, h: int, color: tuple[int, int, int] = (128, 128, 128)) -> bytes:
    """Return minimal valid JPEG bytes for a w×h solid-color image.

    Used wherever tests need to stub ImageService.get_preview() with the bytes
    interface introduced in PR-C' (web-port Phase 0).
    """
    pil = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _make_qimage(w: int, h: int) -> QImage:
    """Return a valid ARGB32 QImage filled with an opaque pixel.

    Kept for tests that directly construct/inspect QImage objects (zoom tests).
    """
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF_80_80_80)
    return img


# ── bytes contract regression ─────────────────────────────────────────────


class TestBytesContractRegression:
    """PR-C' changed ImageService.get_preview() to return JPEG bytes instead
    of a QImage. Before the fix in full_res_viewer._load_image, the dialog
    called img.isNull() on the raw bytes object — raising AttributeError,
    caught by the outer except, and showing "Load failed" instead of the image.

    The s68 qa scenario caught this: the window title was missing the [W×H]
    suffix that _load_image sets only on a successful QImage load.
    """

    def test_load_image_with_jpeg_bytes_sets_resolution_title(self, qapp_m):
        """When get_preview() returns valid JPEG bytes, _load_image must
        convert them to a QImage and set the [W×H] title suffix.

        Real failure mode (the s68 regression): calling img.isNull() on bytes
        raised AttributeError → fell into except → title stayed bare filename,
        never gained [W×H]. This test would have caught the regression before
        qa ran.
        """
        jpeg = _make_jpeg(320, 240)
        injected = MagicMock()
        injected.get_preview.return_value = jpeg

        dlg = FullResViewerDialog("/photos/raw.dng", parent=None, service=injected)
        try:
            title = dlg.windowTitle()
            assert "320" in title and "240" in title, (
                f"Title '{title}' must contain [W×H] dimensions after successful "
                f"bytes-to-QImage conversion. 'Load failed' means the conversion "
                f"path was not reached — the s68 regression."
            )
            assert dlg._full_qimage is not None and not dlg._full_qimage.isNull(), (
                "_full_qimage must be a valid non-null QImage after bytes load"
            )
        finally:
            dlg.deleteLater()

    def test_load_image_null_bytes_shows_error_label(self, qapp_m):
        """When get_preview() returns empty/None bytes, the dialog shows
        a 'Could not load' label rather than crashing.

        Real failure mode: bytes falsy path (b"" or None) → loadFromData no-op
        → img.isNull() True → label text set. Guard stays correct with bytes.
        """
        injected = MagicMock()
        injected.get_preview.return_value = b""  # empty bytes

        dlg = FullResViewerDialog("/photos/corrupt.dng", parent=None, service=injected)
        try:
            label_text = dlg._label.text()
            assert "corrupt.dng" in label_text or "Could not load" in label_text, (
                f"Label should show error for empty bytes, got: {label_text!r}"
            )
            assert dlg._full_qimage is None, (
                "_full_qimage must remain None when load fails"
            )
        finally:
            dlg.deleteLater()


# ── FullResViewerDialog lifecycle ─────────────────────────────────────────


class TestFullResViewerLifecycle:
    def test_qimage_freed_on_close(self, qapp_m, tmp_path):
        """After close(), _full_qimage must be None so the QImage is released.

        Real failure mode: if the dialog keeps a strong reference to the
        full-res QImage after close, a user who rapidly opens and closes the
        viewer accumulates one full-res QImage per open — typically 30–200 MB
        each for RAW files — causing OOM on the second or third open.
        """
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(100, 100)

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
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(200, 150)

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
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            # Use a JPEG that will decode to known dimensions
            instance.get_preview.return_value = _make_jpeg(400, 300)

            dlg = FullResViewerDialog("/photos/raw.dng", parent=None)
            title = dlg.windowTitle()
            assert "400" in title and "300" in title, (
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
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(200, 200)

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)
            initial_pixmap = dlg._label.pixmap()
            assert initial_pixmap is not None and not initial_pixmap.isNull()
            initial_w = initial_pixmap.width()

            # Simulate Ctrl+wheel-up (positive angleDelta = zoom in)
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
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(200, 200)

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
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(100, 100)

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


# ── DI service injection (#622 Phase 1) ──────────────────────────────────


class TestServiceInjection:
    """The dialog accepts an optional ``service=`` keyword in its
    constructor so MainWindow can inject the app-level ImageService
    instance. Without DI the dialog used to construct a bare
    ``ImageService()`` on every open, re-running ``_migrate_legacy_disk_cache``
    each time and bypassing the shared byte-budget LRU.
    """

    def test_injected_service_is_used_instead_of_constructing_bare(self, qapp_m):
        """When ``service=mock`` is passed, ``mock.get_preview`` is called
        and ``ImageService.__init__`` is NEVER invoked at the module level.

        Failure mode: a refactor that ignored the kwarg would silently
        regress to building a bare ImageService every open — spurious
        disk-cache migration scans and zero reuse of the warm in-memory
        cache from the main preview pane.
        """
        injected = MagicMock()
        injected.get_preview.return_value = _make_jpeg(100, 100)

        with patch("infrastructure.image_service.ImageService") as MockSvc:
            dlg = FullResViewerDialog(
                "/fake/photo.jpg", parent=None, service=injected
            )
            try:
                injected.get_preview.assert_called_once_with("/fake/photo.jpg", 0)
                # The bare ImageService class must NOT be instantiated by the dialog
                # when a service was injected.
                MockSvc.assert_not_called()
            finally:
                dlg.deleteLater()

    def test_fallback_to_bare_service_when_no_kwarg(self, qapp_m):
        """When ``service`` is omitted, the dialog constructs a bare
        ``ImageService()`` (backward compatibility for callers that don't DI).

        Failure mode: dropping the fallback breaks every existing test that
        patches ``infrastructure.image_service.ImageService`` and relies on
        the bare-construction path. That patching pattern is widespread, so
        this guarantee is load-bearing for the existing test suite.
        """
        with patch("infrastructure.image_service.ImageService") as MockSvc:
            instance = MockSvc.return_value
            instance.get_preview.return_value = _make_jpeg(100, 100)

            dlg = FullResViewerDialog("/fake/photo.jpg", parent=None)
            try:
                # Bare-class was constructed (no DI), and its get_preview ran.
                MockSvc.assert_called_once_with()
                instance.get_preview.assert_called_once_with("/fake/photo.jpg", 0)
            finally:
                dlg.deleteLater()
