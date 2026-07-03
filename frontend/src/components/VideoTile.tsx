// VideoTile — a single video tile inside GroupGrid.
//
// Renders row.thumbnail_url as a poster image (the /api/image endpoint falls
// through to the Shell/WIC thumbnail provider for video paths too, so this
// resolves to a real decoded frame — see infrastructure/image_service.py's
// GetImage rescue attempt, which also benefits Qt since it's shared
// infrastructure). The poster is best-effort: onError falls back to the dark
// background. A play affordance (▶ glyph) is shown over the poster (or the
// dark fallback) until the user clicks.
//
// On CLICK: mounts a native <video controls autoPlay> using the V1/V2
// mediaUrl() + transcode-fallback swap pattern (same [useTranscode,
// videoFailed, canPlay] triple as PreviewPane and FullResViewer, but scoped
// per-tile so tiles are independent). The element is registered with the
// GroupMediaProvider on mount and unregistered on unmount.
//
// The autoplay-once-per-grid guard lives in GroupMediaController (the
// controller fires playAll() after all tiles have registered). Each tile
// itself only autoplays on an explicit click (autoPlay attr on the <video>
// so the browser starts it after the element mounts).
//
// Per-tile independent transcode state satisfies the hook-rules constraint
// (no conditional hooks) because each tile is its own component instance.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { mediaUrl } from "@/api/client";
import { useGroupMedia } from "@/hooks/useGroupMedia";
import type { FileRow } from "@/api/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface VideoTileProps {
  row: FileRow;
  "data-testid"?: string;
}

// ---------------------------------------------------------------------------
// VideoTile
// ---------------------------------------------------------------------------

export function VideoTile({
  row,
  "data-testid": testId,
}: VideoTileProps): React.ReactElement {
  const { register, unregister } = useGroupMedia();

  // Whether the user has clicked to mount the player yet.
  const [playerMounted, setPlayerMounted] = useState(false);

  // Whether the poster <img> failed to load — falls back to the dark
  // background (the ▶ glyph stays either way).
  const [posterFailed, setPosterFailed] = useState(false);

  // Per-tile independent transcode-fallback state — same pattern as
  // PreviewPane.tsx:62-69 and FullResViewer.tsx:76-89.
  const [useTranscode, setUseTranscode] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [canPlay, setCanPlay] = useState(false);

  // Reset transcode state whenever the file path changes (e.g. grid refresh).
  useEffect(() => {
    setUseTranscode(false);
    setVideoFailed(false);
    setCanPlay(false);
    setPosterFailed(false);
  }, [row.file_path]);

  // Ref to the <video> element — used to register/unregister with the context.
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Register on mount (of the <video> element), unregister on unmount.
  // The ref callback pattern ensures registration happens exactly when the
  // DOM element is available.
  const videoRefCallback = useCallback(
    (el: HTMLVideoElement | null) => {
      if (el !== null) {
        videoRef.current = el;
        register(row.file_path, el);
      } else {
        // el === null means the element is being unmounted.
        unregister(row.file_path);
        videoRef.current = null;
      }
    },
    [row.file_path, register, unregister]
  );

  // Transcode-fallback error handler — identical contract to PreviewPane.
  const handleVideoError = useCallback(() => {
    if (!useTranscode) {
      setUseTranscode(true);
      setCanPlay(false);
    } else {
      setVideoFailed(true);
    }
  }, [useTranscode]);

  const handleClick = useCallback(() => {
    if (!playerMounted) {
      setPlayerMounted(true);
    }
  }, [playerMounted]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      data-testid={testId}
      className="relative aspect-square bg-neutral-900 rounded overflow-hidden cursor-pointer select-none"
      onClick={!playerMounted ? handleClick : undefined}
      title={row.basename}
    >
      {!playerMounted ? (
        // Poster frame (or dark fallback on load failure) with play
        // affordance — before user clicks.
        <div className="absolute inset-0 flex items-center justify-center">
          {!posterFailed && (
            <img
              data-testid={testId ? `${testId}-poster` : undefined}
              src={row.thumbnail_url}
              alt=""
              className="absolute inset-0 w-full h-full object-cover"
              draggable={false}
              onError={() => setPosterFailed(true)}
            />
          )}
          <span
            className="text-white text-3xl opacity-80 pointer-events-none"
            aria-label="Play video"
          >
            ▶
          </span>
        </div>
      ) : videoFailed ? (
        // Terminal failure state — both original and transcode streams failed.
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-neutral-400 text-xs text-center px-2">
            Video cannot be played
          </span>
        </div>
      ) : (
        // Native video player — mounts after first click, autoPlays.
        <>
          {/* Preparing overlay while transcode is in flight */}
          {useTranscode && !canPlay && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
              <span className="text-neutral-400 text-xs">Preparing…</span>
            </div>
          )}
          <video
            // The inner <video> carries its own testid ("{tile-testid}-video")
            // so scenarios can target the media element directly (the outer div
            // keeps the plain {testId}). A static probe in tests/test_ui_probes.py
            // pins this contract so it can't silently re-drift.
            data-testid={testId ? `${testId}-video` : undefined}
            // key forces a full remount when src swaps to the transcode URL,
            // replicating PreviewPane:117 and FullResViewer:255 key pattern.
            key={row.file_path + (useTranscode ? ":h264" : "")}
            ref={videoRefCallback}
            src={mediaUrl(
              row.file_path,
              useTranscode ? { transcode: "h264" } : undefined
            )}
            controls
            autoPlay
            className="absolute inset-0 w-full h-full object-contain"
            onError={handleVideoError}
            onCanPlay={() => setCanPlay(true)}
          />
        </>
      )}

      {/* Filename label at the bottom */}
      <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-xs truncate px-1 py-0.5 pointer-events-none">
        {row.basename}
      </div>
    </div>
  );
}
