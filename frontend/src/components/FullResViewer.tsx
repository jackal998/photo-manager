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
import { fullResUrl, mediaUrl } from "@/api/client";
import { FULLRES_DIALOG, FULLRES_IMAGE } from "@/testids";
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

  const row = fullResPath !== null ? findRow(groups, fullResPath) : null;
  const isVideo = row?.media_type === "video";

  const isOpen = fullResPath !== null;

  // Reset image + video state whenever the path changes.
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [naturalDims, setNaturalDims] = useState<{
    w: number;
    h: number;
  } | null>(null);

  // Transcode fallback state (video only).
  const [useTranscode, setUseTranscode] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoCanPlay, setVideoCanPlay] = useState(false);

  useEffect(() => {
    setLoading(true);
    setLoadError(false);
    setNaturalDims(null);
    setScale(1);
    setPan({ x: 0, y: 0 });
    setUseTranscode(false);
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

  // Video-specific error: swap to transcode on first error, terminal on second.
  const handleVideoError = useCallback(() => {
    if (!useTranscode) {
      setUseTranscode(true);
      setVideoCanPlay(false);
    } else {
      setVideoFailed(true);
    }
  }, [useTranscode]);

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
          data-testid={FULLRES_DIALOG}
          className="fixed inset-0 z-50 flex flex-col focus:outline-none"
          onEscapeKeyDown={closeFullRes}
          aria-label={`Full resolution: ${basename}`}
        >
          {/* Title bar */}
          <DialogPrimitive.Title asChild>
            <div className="flex items-center justify-between px-4 py-2 bg-neutral-900 text-white text-sm flex-shrink-0">
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
                aria-label="Close"
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
                    Video cannot be played
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
                      <span className="text-white/80 text-sm">Preparing video…</span>
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
                      File too large for in-browser full-res preview.
                    </p>
                    <button
                      onClick={handleReveal}
                      className="px-4 py-2 bg-white text-neutral-900 rounded font-medium text-sm hover:bg-neutral-200 transition-colors"
                    >
                      Open in default app
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
              Ctrl+scroll to zoom · Drag to pan · Esc to close
            </div>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
