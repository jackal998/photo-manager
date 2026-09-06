"""#622 Phase 3 instrument: measure the Phase 1/2 preview redesign end-to-end.

This script MEASURES; it makes no claim. Every number it prints lands in one
JSON artifact carrying the fields the project's perf-claim rule requires
(``probe`` / ``git_sha`` / ``args``, plus the artifact path itself) so a
reading can be cited rather than asserted. The owner runs it against the real
NAS library — ``docs/audits/preview-phase3-runbook.md`` holds the copy-paste
commands, the full JSON schema, the decision tables and the results template.

The pure analysis half (summary roll-up, box-2 ratio, schema check, artifact
write) lives in :mod:`scripts.preview_phase3_analysis` and is unit-tested;
this module owns the Qt harness, which is layer-3 / manual by nature.

What it drives
--------------
A headless Qt harness (``QT_QPA_PLATFORM=offscreen`` unless the caller already
set a platform) holding the REAL production objects on the measured path:

* ``infrastructure.image_service.ImageService`` — byte-budget LRU, DNG
  ``extract_thumb`` fast path, versioned disk cache;
* ``app.views.image_tasks.ImageTaskRunner`` — which owns the real
  ``PreviewRequestCoordinator`` (per-device serialisation, cancellation);
* ``app.views.preview_pane.PreviewPane`` — the widget the user looks at,
  including the QPixmap conversion and the fit-to-width pass;
* ``app.views.dialogs.full_res_viewer.FullResViewerDialog`` — the double-click
  full-res viewer.

The ONE thing it substitutes is ``MainWindow``: the harness supplies a small
``QObject`` that owns ``imageLoaded = Signal(str, str, object)`` and forwards
to ``pane.on_image_loaded`` — what ``MainWindow._on_image_loaded``
(``app/views/main_window.py:855-863``) does. A real ``MainWindow`` needs a
loaded manifest, the tree model, menus and dialogs, none of which is on the
preview path being measured.

Not driven (so it is not silently reported as covered): the 1-ahead prefetch,
which fires from ``MainWindow._prefetch_next_group`` on GROUP selection
(``main_window.py:836-850``), not from a single-file click.

The four acceptance boxes of issue #622
---------------------------------------
box 1  ``summary.box1``  — steady-state RSS over the last ``--steady-window``
       clicks, against ``--rss-threshold-mb``.
box 2  ``summary.box2``  — time-to-first-paint ratio, embedded-JPEG path vs a
       forced full raw decode, COMPUTED by ``compute_box2_ratio`` from two
       runs (``--force-full-decode`` / ``--compare-json``); never typed.
box 3  ``modal``         — a real ``QMouseEvent`` double-click on the pane's
       single-view label, the ``requestFullRes`` emission it produces, and the
       dialog's pan wiring plus an actual zoom step.
box 4  ``modal``         — whether the dialog's QImage is freed on close,
       detected with ``shiboken6.isValid`` plus ``QObject.children()``. The
       ``destroyed`` signal is deliberately NOT used: it is unreliable for
       ``deleteLater`` under a headless PySide6 (project memory
       ``feedback_pyside6_destroyed_signal_unreliable``).

Which decode path a click took is observed at the real seam rather than
guessed: ``ImageService`` exposes no counter, so the probe wraps the bound
methods ``_load_from_source`` and ``_try_rawpy_embedded_thumb`` on ITS OWN
service instance and records what they returned. ``--force-full-decode`` makes
the wrapper return ``None`` without calling ``extract_thumb``, which is exactly
the pre-Phase-1 route into ``raw.postprocess``.

Safety: the probe never writes to, deletes from, or renames anything under
``--root``. The only things it writes are the JSON artifact and the disk-cache
directory, which defaults to a fresh temp directory (never the app's real
thumbs cache) so the two box-2 runs cannot serve each other's bytes from cache.
Nothing is ever deleted, including that temp directory.

CLI usage
---------

    python scripts/preview_phase3_probe.py --root J:\\photos --clicks 100 \\
        --viewport-cap 2048 --output phase3_embedded.json

    python scripts/preview_phase3_probe.py --root J:\\photos --clicks 100 \\
        --viewport-cap 2048 --force-full-decode \\
        --compare-json phase3_embedded.json --output phase3_fulldecode.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preview_phase3_analysis import (  # noqa: E402  (needs sys.path above)
    DEFAULT_BOX2_RATIO,
    DEFAULT_EXTS,
    DEFAULT_RSS_THRESHOLD_MB,
    DEFAULT_STEADY_WINDOW,
    PATH_CACHE_HIT,
    PATH_EMBEDDED,
    PATH_FORCED_FULL,
    PATH_NON_RAW,
    PATH_RAW_FULL,
    PATH_UNKNOWN,
    PROBE_NAME,
    build_payload,
    click_order,
    compute_box2_ratio,
    discover_images,
    git,
    summarise_clicks,
    validate_payload,
    write_json,
)


def _rss_private() -> tuple[int, int]:
    """(rss_bytes, private_bytes) for this process; (0, 0) if unavailable.

    Reuses ``scripts/memory_probe.py``'s ctypes reader — the instrumentation
    shipped for #614/#627 — rather than adding a ``psutil`` dependency this
    project deliberately does not have.
    """
    try:
        from scripts.memory_probe import _get_process_memory
        rss, _vms, private = _get_process_memory()
        return int(rss), int(private)
    except Exception:
        return 0, 0


class _ServiceInstrumentation:
    """Observes which decode route a request took, at the real seams.

    ``ImageService`` publishes no counter, so rather than infer the route from
    timing (which is the thing being measured) this wraps two bound methods on
    the probe's OWN service instance:

    * ``_load_from_source`` — called only on a miss, so its absence identifies
      a cache hit;
    * ``_try_rawpy_embedded_thumb`` — returns bytes when the embedded JPEG
      satisfied the request and ``None`` when ``_load_via_rawpy`` falls through
      to ``raw.postprocess``.

    With ``force_full_decode`` the second wrapper returns ``None`` WITHOUT
    calling the real method, which is precisely the pre-Phase-1 route: straight
    into ``postprocess``, no ``extract_thumb`` cost included.

    Decodes run on QThreadPool threads while the harness waits on the GUI
    thread, so the record is written under a lock and read only after the click
    has settled.
    """

    def __init__(self, service: Any, *, force_full_decode: bool) -> None:
        import threading

        self._service = service
        self._force = bool(force_full_decode)
        self._lock = threading.Lock()
        self._source_loaded = False
        self._route: str | None = None

        real_load = service._load_from_source
        real_thumb = service._try_rawpy_embedded_thumb

        def load_from_source(path: str, requested_side: int) -> Any:
            with self._lock:
                self._source_loaded = True
                self._route = PATH_NON_RAW
            return real_load(path, requested_side)

        def try_embedded(raw: Any, viewport_cap: int) -> Any:
            if self._force:
                with self._lock:
                    self._route = PATH_FORCED_FULL
                return None
            result = real_thumb(raw, viewport_cap)
            with self._lock:
                self._route = PATH_EMBEDDED if result is not None else PATH_RAW_FULL
            return result

        service._load_from_source = load_from_source
        service._try_rawpy_embedded_thumb = try_embedded

    def begin_click(self) -> None:
        with self._lock:
            self._source_loaded = False
            self._route = None

    def read(self) -> tuple[bool, str]:
        with self._lock:
            if not self._source_loaded:
                return False, PATH_CACHE_HIT
            return True, (self._route or PATH_UNKNOWN)


def _make_info(path: Path) -> dict:
    """The info dict ``MainWindow`` hands ``show_single``.

    Passing it is not decoration: a non-empty info dict is what makes the pane
    fire the off-thread ``_ResolutionTask`` (#622 Phase 1 item 6), so the
    header read is on the measured path exactly as it is in the app.
    """
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    return {
        "name": path.name,
        "folder": str(path.parent),
        "size": f"{size_bytes}",
        "creation": "",
        "shot": "",
    }


def _drain(app: Any, seconds: float = 0.2) -> None:
    """Process pending Qt events (including DeferredDelete) for ``seconds``."""
    from PySide6.QtCore import QCoreApplication, QEvent

    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def run_modal_check(app: Any, pane: Any, service: Any, path: str) -> dict:
    """Boxes 3 + 4: real double-click -> viewer -> pan/zoom -> freed on close.

    Box 4's "freed" is decided by ``shiboken6.isValid`` on the dialog and its
    label plus the dialog's ``children()`` count, never by the ``destroyed``
    signal, which does not fire reliably for ``deleteLater`` in a headless
    PySide6 process. ``label_valid_before_close`` is recorded beside
    ``label_valid_after_close`` on purpose: a True -> False transition is a
    measurement, whereas a lone False could equally mean the check never had
    anything to look at.
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    import shiboken6

    from app.views.dialogs.full_res_viewer import FullResViewerDialog

    result: dict[str, Any] = {
        "attempted": True,
        "path": path,
        "detection": (
            "shiboken6.isValid + QObject.children(); the destroyed signal is "
            "deliberately not used (unreliable for deleteLater headless)"
        ),
    }

    emitted: list[str] = []
    pane.requestFullRes.connect(emitted.append)
    pane.show_single(path, _make_info(Path(path)))
    _drain(app, 0.3)

    label = pane._single_label
    pos = QPointF(label.width() / 2 or 1.0, label.height() / 2 or 1.0)
    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    result["double_click_delivered"] = bool(app.sendEvent(label, event))
    _drain(app, 0.2)
    result["request_full_res_emitted"] = bool(emitted)
    result["request_full_res_path"] = emitted[0] if emitted else None

    if not emitted:
        result["dialog_opened"] = False
        result["box3_pass"] = False
        result["box4_pass"] = None
        return result

    rss_before, _ = _rss_private()
    # Mirrors main_window.on_open_full_res_viewer: same class, same DI.
    dlg = FullResViewerDialog(emitted[0], parent=None, service=service)
    dlg.show()
    _drain(app, 0.5)
    rss_open, _ = _rss_private()

    img = dlg._full_qimage
    result["dialog_opened"] = True
    result["dialog_is_modal"] = bool(dlg.isModal())
    result["qimage_loaded"] = bool(img is not None and not img.isNull())
    result["qimage_w"] = int(img.width()) if img is not None else None
    result["qimage_h"] = int(img.height()) if img is not None else None

    def _pixmap_size(widget: Any) -> Any:
        pm = widget.pixmap()
        return None if pm is None or pm.isNull() else pm.size()

    dlg_label = dlg._label
    scale_before = dlg._current_scale
    pm_before = _pixmap_size(dlg_label)
    dlg._apply_zoom(1.25)
    _drain(app, 0.2)
    pm_after = _pixmap_size(dlg_label)
    result["zoom_scale_before"] = round(float(scale_before), 4)
    result["zoom_scale_after"] = round(float(dlg._current_scale), 4)
    result["zoom_pixmap_grew"] = bool(
        pm_before is not None and pm_after is not None
        and pm_after.width() > pm_before.width()
    )
    result["pan_wired"] = dlg_label._scroll_area is dlg._scroll
    hbar = dlg._scroll.horizontalScrollBar()
    result["pan_scroll_range"] = int(hbar.maximum() - hbar.minimum()) if hbar else 0

    result["children_count_before_close"] = len(dlg.children())
    result["label_valid_before_close"] = bool(shiboken6.isValid(dlg_label))

    dlg.close()
    try:
        result["full_qimage_none_after_close"] = dlg._full_qimage is None
    except RuntimeError:
        # The C++ dialog was already reaped: the attribute is unreachable, but
        # an unreachable dialog cannot be holding a QImage either.
        result["full_qimage_none_after_close"] = True
    _drain(app, 0.6)

    result["dialog_valid_after_close"] = bool(shiboken6.isValid(dlg))
    result["label_valid_after_close"] = bool(shiboken6.isValid(dlg_label))
    try:
        result["children_count_after_close"] = (
            len(dlg.children()) if shiboken6.isValid(dlg) else None
        )
    except RuntimeError:
        result["children_count_after_close"] = None
    rss_close, _ = _rss_private()
    result["rss_bytes_before_open"] = rss_before
    result["rss_bytes_after_open"] = rss_open
    result["rss_bytes_after_close"] = rss_close

    result["box3_pass"] = bool(
        result["request_full_res_emitted"]
        and result["dialog_opened"]
        and result["qimage_loaded"]
        and result["zoom_pixmap_grew"]
        and result["pan_wired"]
    )
    result["box4_pass"] = bool(
        result["full_qimage_none_after_close"]
        and not result["dialog_valid_after_close"]
        and not result["label_valid_after_close"]
    )
    return result


def _click_once(app: Any, pane: Any, path: str, timeout_s: float, state: dict,
                loop_box: dict) -> float | None:
    """Drive one selection and wait for its paint. Returns ttfp_ms or None."""
    from PySide6.QtCore import QEventLoop, QTimer

    state["token"] = None
    state["t1"] = None
    state["image"] = None
    loop = QEventLoop()
    loop_box["loop"] = loop

    t0 = time.perf_counter()
    pane.show_single(path, _make_info(Path(path)))
    state["token"] = pane._current_single_token
    if state["t1"] is None:
        # The decode runs on a QThreadPool thread and its signal is queued, so
        # it cannot have been delivered before this loop starts.
        QTimer.singleShot(int(timeout_s * 1000), loop.quit)
        loop.exec()
    loop_box["loop"] = None

    t1 = state["t1"]
    return None if t1 is None else round((t1 - t0) * 1000, 3)


def run_session(args: argparse.Namespace, on_progress: Callable[[str], None]) -> dict:
    """Drive the harness: N single-file selections, then the modal check."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    import PySide6

    from app.views import image_tasks
    from app.views.image_tasks import ImageTaskRunner
    from app.views.preview_pane import PreviewPane
    from infrastructure.device_key import device_key
    from infrastructure.image_service import ImageService, _compute_cache_budgets

    class _Receiver(QObject):
        """Stands in for MainWindow on the measured path only.

        ``MainWindow`` owns ``imageLoaded = Signal(str, str, object)``
        (``main_window.py:75``) and forwards it to the pane
        (``main_window.py:855-863``). That forward is the whole of its
        involvement in a preview, so the harness reproduces it and skips the
        manifest / tree / menu machinery a real MainWindow would demand.
        """

        imageLoaded = Signal(str, str, object)

    root = Path(args.root)
    files = discover_images(root, args.ext)
    if not files:
        raise SystemExit(f"ERROR: no images with {args.ext} under {root}")
    sequence = click_order(files, args.clicks)

    app = QApplication.instance() or QApplication([])
    try:
        from infrastructure.i18n import init_translator
        init_translator(args.locale, REPO_ROOT / "translations")
    except Exception:
        pass  # untranslated labels cannot change a timing or an RSS reading

    if args.viewport_cap:
        image_tasks._VIEWPORT_CAP = int(args.viewport_cap)
    viewport_cap = image_tasks._compute_viewport_cap()

    disk_cache_dir = args.disk_cache_dir or tempfile.mkdtemp(prefix="pm-phase3-probe-")
    Path(disk_cache_dir).mkdir(parents=True, exist_ok=True)
    service = ImageService(settings={"thumbnail_disk_cache_dir": disk_cache_dir})
    instrumentation = _ServiceInstrumentation(
        service, force_full_decode=args.force_full_decode
    )

    receiver = _Receiver()
    runner = ImageTaskRunner(service=service, receiver=receiver)
    pane = PreviewPane(None, runner)
    pane.resize(args.pane_width, args.pane_height)
    pane.show()
    _drain(app, 0.3)

    state: dict[str, Any] = {"token": None, "t1": None, "image": None}
    loop_box: dict[str, Any] = {"loop": None}

    def _on_image(token: str, path: str, image: Any) -> None:
        pane.on_image_loaded(token, path, image)
        if token == state["token"]:
            # Stamped AFTER the pane painted: time-to-first-PAINT includes the
            # QPixmap conversion and the fit pass, not just the decode.
            state["t1"] = time.perf_counter()
            state["image"] = image
            loop = loop_box.get("loop")
            if loop is not None:
                loop.quit()

    receiver.imageLoaded.connect(_on_image)

    per_click: list[dict] = []
    for i, path in enumerate(sequence):
        p = str(path)
        instrumentation.begin_click()
        ttfp_ms = _click_once(app, pane, p, args.timeout_s, state, loop_box)
        image = state["image"]
        source_loaded, route = instrumentation.read()
        rss, private = _rss_private()
        per_click.append({
            "i": i,
            "path": p,
            "ext": path.suffix.lower(),
            "device": device_key(p),
            "size_bytes": (path.stat().st_size if path.exists() else 0),
            "ttfp_ms": ttfp_ms,
            "ok": ttfp_ms is not None,
            "timed_out": ttfp_ms is None,
            "decode_path": route,
            "source_loaded": source_loaded,
            "image_w": (int(image.width()) if image is not None else None),
            "image_h": (int(image.height()) if image is not None else None),
            "rss_bytes": rss,
            "private_bytes": private,
            "lru_thumb_bytes": service._thumb_cache.total_bytes,
            "lru_preview_bytes": service._preview_cache.total_bytes,
        })
        if (i + 1) % max(1, args.progress_every) == 0:
            on_progress(
                f"  click {i + 1}/{len(sequence)}  ttfp={ttfp_ms}ms  "
                f"route={route}  rss={rss / 1e6:.1f}MB"
            )

    modal: dict[str, Any]
    if args.no_modal_check:
        modal = {"attempted": False, "reason": "--no-modal-check"}
    else:
        modal_path = args.modal_path or next(
            (r["path"] for r in per_click if r["ok"] and r["ext"] == ".dng"),
            next((r["path"] for r in per_click if r["ok"]), str(sequence[0])),
        )
        modal = run_modal_check(app, pane, service, modal_path)

    summary = summarise_clicks(
        per_click,
        steady_window_n=args.steady_window,
        rss_threshold_mb=args.rss_threshold_mb,
    )
    summary["box3"] = {
        "criterion": "double-click opens the full-res viewer with pan/zoom",
        "pass": modal.get("box3_pass"),
    }
    summary["box4"] = {
        "criterion": "the viewer's QImage is freed when it closes",
        "pass": modal.get("box4_pass"),
        "detection": modal.get("detection"),
    }

    if args.compare_json:
        baseline = json.loads(Path(args.compare_json).read_text(encoding="utf-8"))
        if args.force_full_decode:
            embedded_rows, forced_rows = baseline.get("per_click", []), per_click
        else:
            embedded_rows, forced_rows = per_click, baseline.get("per_click", [])
        box2 = compute_box2_ratio(
            embedded_rows, forced_rows, threshold=args.box2_ratio,
        )
        box2["compared_with"] = {
            "json": str(args.compare_json),
            "git_sha": baseline.get("git_sha"),
            "probe": baseline.get("probe"),
        }
        summary["box2"] = box2

    env = {
        "root": str(root),
        "file_count": len(files),
        "clicks_planned": len(sequence),
        "viewport_cap": viewport_cap,
        "viewport_cap_pinned": bool(args.viewport_cap),
        "disk_cache_dir": disk_cache_dir,
        "cache_budget_bytes": dict(
            zip(("thumb_bytes", "preview_bytes"), _compute_cache_budgets())
        ),
        "ext_histogram": dict(Counter(p.suffix.lower() for p in files)),
    }
    host = {
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyside6": PySide6.__version__,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "total_ram_bytes": _total_ram(),
    }
    return {"per_click": per_click, "modal": modal, "summary": summary,
            "env": env, "host": host}


def _total_ram() -> int | None:
    """Total physical RAM, via the same probe ImageService sizes its LRU with."""
    try:
        from infrastructure.image_service import _probe_total_ram
        return _probe_total_ram()
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="#622 Phase 3 preview probe (measures; makes no claim)."
    )
    p.add_argument("--root", required=True,
                   help="Directory of images to click through (read-only).")
    p.add_argument("--clicks", type=int, default=100,
                   help="Number of single-file selections (issue box 1 uses 100).")
    p.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                   help="Comma-separated extensions to include.")
    p.add_argument("--steady-window", type=int, default=DEFAULT_STEADY_WINDOW,
                   help="Clicks at the tail treated as steady state (box 1).")
    p.add_argument("--rss-threshold-mb", type=float, default=DEFAULT_RSS_THRESHOLD_MB,
                   help="Box 1 threshold from the issue.")
    p.add_argument("--box2-ratio", type=float, default=DEFAULT_BOX2_RATIO,
                   help="Box 2 threshold from the issue.")
    p.add_argument("--force-full-decode", action="store_true",
                   help="Skip the embedded-JPEG thumb so every DNG takes the "
                        "full raw decode — the box-2 comparison run.")
    p.add_argument("--compare-json", default=None,
                   help="A JSON from the other run; the probe then COMPUTES "
                        "the box-2 ratio instead of anyone typing it.")
    p.add_argument("--viewport-cap", type=int, default=None,
                   help="Pin the viewport cap in px (offscreen Qt reports a "
                        "small virtual screen; pass the real display width).")
    p.add_argument("--disk-cache-dir", default=None,
                   help="Disk cache for this run. Default: a fresh temp dir, "
                        "so the two box-2 runs cannot serve each other's bytes "
                        "and the app's real thumbs cache is never touched.")
    p.add_argument("--timeout-s", type=float, default=60.0,
                   help="Per-click wait before recording a timeout.")
    p.add_argument("--pane-width", type=int, default=1200)
    p.add_argument("--pane-height", type=int, default=900)
    p.add_argument("--locale", default="en")
    p.add_argument("--modal-path", default=None,
                   help="File to open in the full-res viewer (boxes 3+4). "
                        "Default: the first DNG clicked, else the first file.")
    p.add_argument("--no-modal-check", action="store_true",
                   help="Skip boxes 3+4.")
    p.add_argument("--progress-every", type=int, default=10)
    p.add_argument("--output", default=None, help="Write the JSON artifact here.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    args.ext = [e.strip() for e in str(args.ext).split(",") if e.strip()]

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2
    if args.compare_json and not Path(args.compare_json).is_file():
        print(f"ERROR: --compare-json {args.compare_json} not found", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    print(f"=== {PROBE_NAME} === root={root} clicks={args.clicks} "
          f"force_full_decode={args.force_full_decode}")
    session = run_session(args, on_progress=print)
    wall = time.perf_counter() - t0
    finished = datetime.now(timezone.utc)

    payload = build_payload(
        args=vars(args),
        argv=list(argv),
        git_sha=git("rev-parse", "HEAD"),
        git_dirty=bool(git("status", "--porcelain")),
        host=session["host"],
        timestamps={
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
            "wall_s": round(wall, 3),
        },
        env=session["env"],
        per_click=session["per_click"],
        modal=session["modal"],
        summary=session["summary"],
    )
    problems = validate_payload(payload)
    if problems:
        print("SCHEMA PROBLEMS: " + "; ".join(problems), file=sys.stderr)

    s = payload["summary"]
    print("\n--- summary ---")
    print(f"  clicks={s['clicks']} ok={s['ok_clicks']} timeouts={s['timeouts']}")
    print(f"  ttfp_ms={s['ttfp_ms']}")
    print(f"  decode_path_counts={s['decode_path_counts']}")
    print(f"  box1={s['box1']['steady_state_rss_mb']} "
          f"threshold_mb={s['box1']['threshold_mb']} pass={s['box1']['pass']}")
    print(f"  box2 status={s['box2'].get('status')} pass={s['box2'].get('pass')}")
    print(f"  box3 pass={s['box3']['pass']}  box4 pass={s['box4']['pass']}")
    print(f"  disk_cache_dir={payload['env']['disk_cache_dir']}")

    if args.output:
        out = Path(args.output)
        write_json(out, payload)
        print(f"\nwrote {out}")
        print(f"cite as: probe={PROBE_NAME} sha={payload['git_sha']} "
              f"args={' '.join(argv[1:])} json={out}")
    else:
        print("\n(no --output given: nothing was written)")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
