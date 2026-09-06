// FullResViewer — full-resolution image overlay.
//
// Shown when store.preview.fullResPath != null.
// Uses the Radix Dialog primitive (same pattern as ScanDialog / SettingsDialog).
//
// Features:
//   - Loads GET /api/image?path=...&size=0 via the fullResUrl helper.
//   - Loading spinner while the <img> is decoding.
//   - onError handler: if the server returns 413 (file_too_large_for_full_res)
//     the <img> fires onerror. We show a fallback button that calls
//     store.revealInExplorer(path) so the user can open it in their native app.
//     (We cannot inspect the HTTP status from an <img> onerror, so ANY load
//     error triggers the fallback — this is correct behaviour: the image is
//     broken regardless of cause, and revealing it in Explorer is always safe.)
//   - Esc key and backdrop click close the overlay (store.closeFullRes).
//   - Ctrl+wheel zoom + drag-to-pan via CSS transform (simple, no extra deps).
//   - Movable + resizable window with persisted geometry (#739, owner sign-off
//     2026-07-17): drag the title bar to move, the bottom-right grip to
//     resize, both via the shared useOverlayGeometry mechanism. The viewer
//     still OPENS filling the viewport (the `inset-0` default below), so a
//     first open is unchanged; once moved/resized the geometry persists per
//     browser and is re-clamped into the viewport on every open.

import {
  useState,
  useRef,
  useCallback,
  useEffect,
  type MouseEvent,
  type SyntheticEvent,
  type WheelEvent,
} from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";

import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { fullResUrl, mediaUrl } from "@/api/client";
import { canPlayHevc, prefersTranscodedVideo } from "@/lib/videoCapabilities";
import { useOverlayGeometry } from "@/hooks/useOverlayGeometry";
import { OverlayResizeHandle } from "@/components/ui/overlay-resize-handle";
import {
  FULLRES_DIALOG,
  FULLRES_IMAGE,
  FULLRES_RESIZE_HANDLE,
  FULLRES_TITLE_BAR,
} from "@/testids";
import type { FileRow } from "@/api/types";

// ---------------------------------------------------------------------------
// FullResViewer
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Row lookup — same pattern as PreviewPane
// ---------------------------------------------------------------------------

function findRow(
  groups: ReturnType<typeof useAppStore.getState>["manifest"]["groups"],
  filePath: string
): FileRow | null {
  for (const group of groups) {
    const row = group.items.find((f) => f.file_path === filePath);
    if (row !== undefined) return row;
  }
  return null;
}

// ---------------------------------------------------------------------------
// FullResViewer
// ---------------------------------------------------------------------------

export function FullResViewer() {
  const fullResPath = useAppStore((s) => s.preview.fullResPath);
  const closeFullRes = useAppStore((s) => s.closeFullRes);
  const revealInExplorer = useAppStore((s) => s.revealInExplorer);
  const groups = useAppStore((s) => s.manifest.groups);
  const t = useT();

  const row = fullResPath !== null ? findRow(groups, fullResPath) : null;
  const isVideo = row?.media_type === "video";

  const isOpen = fullResPath !== null;

  // Movable/resizable window geometry (#739) — shared with the Execute and
  // Set Action dialogs. No default is passed: the viewer's own `inset-0`
  // class IS the default, and the first drag seeds from its rendered rect.
  const overlay = useOverlayGeometry("fullres", isOpen);

  // Reset image + video state whenever the path changes.
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [naturalDims, setNaturalDims] = useState<{
    w: number;
    h: number;
  } | null>(null);

  // Transcode fallback state (video only). The initial value is the #787
  // capability hint — "unknown" (today's behaviour) unless the engine has
  // positively reported it cannot decode HEVC and this is an HEVC-in-practice
  // container. handleVideoError below makes it a two-attempt contract:
  // whichever source we start on, the first error swaps to the other one.
  const [useTranscode, setUseTranscode] = useState(
    () => fullResPath !== null && prefersTranscodedVideo(fullResPath)
  );
  // See the same note in PreviewPane.tsx: useTranscode alone is ambiguous once
  // the hint can choose the STARTING source, so "have we already swapped?" is
  // tracked explicitly.
  const [swapAttempted, setSwapAttempted] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoCanPlay, setVideoCanPlay] = useState(false);

  // Re-made during RENDER on every path change — see the same note in
  // PreviewPane.tsx: an effect-based reset commits one render on the
  // original-bytes src, which starts the fetch this pre-check exists to skip.
  const [hintedPath, setHintedPath] = useState<string | null>(fullResPath);
  if (hintedPath !== fullResPath) {
    setHintedPath(fullResPath);
    setUseTranscode(fullResPath !== null && prefersTranscodedVideo(fullResPath));
    setSwapAttempted(false);
  }

  useEffect(() => {
    void canPlayHevc(); // memoized — one decodingInfo call per page life
    setLoading(true);
    setLoadError(false);
    setNaturalDims(null);
    setScale(1);
    setPan({ x: 0, y: 0 });
    setVideoFailed(false);
    setVideoCanPlay(false);
  }, [fullResPath]);

  // ---------------------------------------------------------------------------
  // Pan + zoom state
  // ---------------------------------------------------------------------------

  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });

  // Drag-to-pan
  const dragOrigin = useRef<{ mx: number; my: number; px: number; py: number } | null>(
    null
  );

  const handleMouseDown = useCallback((e: MouseEvent<HTMLImageElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    dragOrigin.current = {
      mx: e.clientX,
      my: e.clientY,
      px: pan.x,
      py: pan.y,
    };
  }, [pan]);

  const handleMouseMove = useCallback((e: MouseEvent<HTMLDivElement>) => {
    if (dragOrigin.current === null) return;
    const dx = e.clientX - dragOrigin.current.mx;
    const dy = e.clientY - dragOrigin.current.my;
    setPan({ x: dragOrigin.current.px + dx, y: dragOrigin.current.py + dy });
  }, []);

  const handleMouseUp = useCallback(() => {
    dragOrigin.current = null;
  }, []);

  // Ctrl+wheel zoom
  const handleWheel = useCallback(
    (e: WheelEvent<HTMLDivElement>) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      // Clamp 5%–800%, matching the desktop full-res viewer (#743, Qt
      // full_res_viewer.py:165). Was 10%–1000%.
      setScale((prev) => Math.max(0.05, Math.min(8, prev + delta)));
    },
    []
  );

  // ---------------------------------------------------------------------------
  // Image event handlers
  // ---------------------------------------------------------------------------

  const handleLoad = useCallback((e: SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setLoading(false);
    setLoadError(false);
    setNaturalDims({ w: img.naturalWidth, h: img.naturalHeight });
  }, []);

  const handleError = useCallback(() => {
    setLoading(false);
    setLoadError(true);
  }, []);

  // Video-specific error: first error swaps to the OTHER source (transcode if
  // we started native; original bytes if the #787 hint started us on the
  // transcode and it 501'd or failed), second error is terminal.
  const handleVideoError = useCallback(() => {
    if (!swapAttempted) {
      setSwapAttempted(true);
      setUseTranscode((prev) => !prev);
      setVideoCanPlay(false);
    } else {
      setVideoFailed(true);
    }
  }, [swapAttempted]);

  const handleReveal = useCallback(() => {
    if (fullResPath !== null) {
      void revealInExplorer(fullResPath);
    }
  }, [fullResPath, revealInExplorer]);

  // ---------------------------------------------------------------------------
  // Derived display values
  // ---------------------------------------------------------------------------

  const basename =
    fullResPath !== null
      ? fullResPath.split(/[\\/]/).pop() ?? fullResPath
      : "";
  const dimsLabel =
    naturalDims !== null ? `${naturalDims.w} × ${naturalDims.h}` : "";

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(open) => { if (!open) closeFullRes(); }}>
      <DialogPrimitive.Portal>
        {/* Backdrop */}
        <DialogPrimitive.Overlay
          className="fixed inset-0 z-50 bg-black/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
        />

        {/* Content panel */}
        <DialogPrimitive.Content
          ref={overlay.containerRef}
          data-testid={FULLRES_DIALOG}
          // `inset-0` is the DEFAULT layout (unchanged first open); once the
          // user moves or resizes, overlay.style pins explicit left/top/
          // width/height and neutralises right/bottom.
          className="fixed inset-0 z-50 flex flex-col overflow-hidden focus:outline-none"
          style={overlay.style}
          onEscapeKeyDown={closeFullRes}
          aria-label={`Full resolution: ${basename}`}
        >
          {/* Title bar — also the window's drag handle (#739) */}
          <DialogPrimitive.Title asChild>
            <div
              data-testid={FULLRES_TITLE_BAR}
              className="flex items-center justify-between px-4 py-2 bg-neutral-900 text-white text-sm flex-shrink-0 cursor-move select-none"
              onMouseDown={overlay.onMoveStart}
            >
              <span className="font-medium truncate max-w-xl">
                {basename}
                {dimsLabel !== "" && (
                  <span className="ml-2 text-neutral-400 font-normal">
                    {dimsLabel}
                  </span>
                )}
              </span>
              <button
                onClick={closeFullRes}
                className="ml-4 flex-shrink-0 text-neutral-300 hover:text-white transition-colors"
                aria-label={t("web.fullres.close", "Close")}
              >
                ✕
              </button>
            </div>
          </DialogPrimitive.Title>

          {/* Media canvas */}
          <div
            className="flex-1 overflow-hidden flex items-center justify-center select-none relative"
            style={{ cursor: isVideo ? "default" : "grab" }}
            onMouseMove={isVideo ? undefined : handleMouseMove}
            onMouseUp={isVideo ? undefined : handleMouseUp}
            onMouseLeave={isVideo ? undefined : handleMouseUp}
            onWheel={isVideo ? undefined : handleWheel}
            // Backdrop click closes the viewer (click on overlay, not on media).
            onClick={(e) => {
              if (e.target === e.currentTarget) closeFullRes();
            }}
          >
            {isVideo && fullResPath !== null ? (
              /* Video player — native controls, no autoplay (matches Qt desktop).
                 On decode error, swap ONCE to the H.264 transcode fallback; on a
                 second error show a terminal "cannot be played" message. */
              videoFailed ? (
                <div className="flex flex-col items-center gap-4 text-white">
                  <p className="text-sm text-neutral-300">
                    {t("web.fullres.video_cannot_be_played", "Video cannot be played")}
                  </p>
                </div>
              ) : (
                <>
                  {/* Spinner + "Preparing video…" while the transcode is in
                      flight (#737) — matches PreviewPane's message so the
                      first-view transcode stall reads as work-in-progress, not
                      a stuck player. */}
                  {useTranscode && !videoCanPlay && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 pointer-events-none">
                      <div className="w-10 h-10 border-4 border-white/30 border-t-white rounded-full animate-spin" />
                      <span className="text-white/80 text-sm">
                        {t("web.fullres.preparing_video", "Preparing video…")}
                      </span>
                    </div>
                  )}
                  <video
                    data-testid={FULLRES_IMAGE}
                    // key forces a full remount when the src swaps to the transcode
                    // URL so the browser re-initiates the decode attempt.
                    key={fullResPath + (useTranscode ? ":h264" : "")}
                    src={mediaUrl(fullResPath, useTranscode ? { transcode: "h264" } : undefined)}
                    controls
                    className="max-w-full max-h-full"
                    onError={handleVideoError}
                    onCanPlay={() => setVideoCanPlay(true)}
                    // No autoplay — matches Qt desktop single-view player behaviour.
                  />
                </>
              )
            ) : (
              <>
                {/* Loading spinner (images only) */}
                {loading && !loadError && fullResPath !== null && (
                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <div className="w-10 h-10 border-4 border-white/30 border-t-white rounded-full animate-spin" />
                  </div>
                )}

                {/* 413 / load-error fallback */}
                {loadError && (
                  <div className="flex flex-col items-center gap-4 text-white">
                    <p className="text-sm text-neutral-300">
                      {t(
                        "web.fullres.too_large",
                        "File too large for in-browser full-res preview."
                      )}
                    </p>
                    <button
                      onClick={handleReveal}
                      className="px-4 py-2 bg-white text-neutral-900 rounded font-medium text-sm hover:bg-neutral-200 transition-colors"
                    >
                      {t("web.fullres.open_in_default_app", "Open in default app")}
                    </button>
                  </div>
                )}

                {/* The full-res image — rendered even while loading so onLoad fires */}
                {fullResPath !== null && !loadError && (
                  <img
                    data-testid={FULLRES_IMAGE}
                    key={fullResPath}
                    src={fullResUrl(fullResPath)}
                    alt={basename}
                    className="max-w-none"
                    style={{
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
                      transformOrigin: "center center",
                      cursor: scale > 1 ? "grab" : "default",
                      visibility: loading ? "hidden" : "visible",
                    }}
                    onLoad={handleLoad}
                    onError={handleError}
                    onMouseDown={handleMouseDown}
                    draggable={false}
                  />
                )}
              </>
            )}
          </div>

          {/* Zoom hint (images only — video has native controls) */}
          {!isVideo && !loadError && (
            <div className="flex-shrink-0 text-center text-neutral-400 text-xs py-1 bg-neutral-900">
              {t(
                "web.fullres.zoom_hint",
                "Ctrl+scroll to zoom · Drag to pan · Esc to close"
              )}
            </div>
          )}

          {/* Resize grip (#739) — light-on-dark to match the viewer chrome */}
          <OverlayResizeHandle
            data-testid={FULLRES_RESIZE_HANDLE}
            onMouseDown={overlay.onResizeStart}
            className="text-white"
            label={t("web.overlay.resize", "Resize window")}
          />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
