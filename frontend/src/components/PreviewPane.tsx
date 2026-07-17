// PreviewPane — inline image preview panel.
//
// Reads store.preview.selectedFilePath and store.preview.selectedGroupId.
// Mode-aware rendering (selectPreviewMode selector):
//   - 'grid'   → <GroupGrid groupId={selectedGroupId}> keyed on groupId so
//                videos are paused+unmounted on group change (no orphan audio).
//   - 'single' → single <img> / <video> for the selected file.
//   - 'empty'  → placeholder "Select a file to preview".
//
// CRITICAL (#535 fix): the image scroll container uses overflow-y:scroll
// (always-on, never "auto") to avoid the portrait fit-on-width oscillation
// loop where adding the content causes the scrollbar to appear, shrinking the
// viewport, causing the content to reflow, making the scrollbar disappear, …
// This replicates Qt's ScrollBarAlwaysOn invariant.
//
// Double-clicking the image opens the full-resolution overlay via
// store.openFullRes(path).

import { useCallback, useState, useEffect } from "react";
import { useAppStore } from "@/store/useAppStore";
import { selectPreviewMode } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { thumbnailUrl, mediaUrl } from "@/api/client";
import { formatBytes, formatScore, formatDims, formatDate } from "@/lib/format";
import { PREVIEW_PANE, PREVIEW_SINGLE_IMAGE, PREVIEW_INFO } from "@/testids";
import type { FileRow } from "@/api/types";
import { GroupGrid } from "./GroupGrid";

// ---------------------------------------------------------------------------
// Helper — find a FileRow by file_path in the manifest groups
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
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  const t = useT();
  return (
    <div className="flex items-center justify-center h-full text-neutral-400 text-sm select-none">
      {t("web.preview.select_file", "Select a file to preview")}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PreviewPane
// ---------------------------------------------------------------------------

export function PreviewPane() {
  const selectedFilePath = useAppStore((s) => s.preview.selectedFilePath);
  const selectedGroupId = useAppStore((s) => s.preview.selectedGroupId);
  const groups = useAppStore((s) => s.manifest.groups);
  const openFullRes = useAppStore((s) => s.openFullRes);
  const previewMode = useAppStore(selectPreviewMode);
  const t = useT();

  const row = selectedFilePath !== null ? findRow(groups, selectedFilePath) : null;

  // Transcode fallback state — reset when the selected file changes.
  const [useTranscode, setUseTranscode] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [canPlay, setCanPlay] = useState(false);
  useEffect(() => {
    setUseTranscode(false);
    setVideoFailed(false);
    setCanPlay(false);
  }, [selectedFilePath]);

  const handleVideoError = useCallback(() => {
    if (!useTranscode) {
      // First error: swap to the H.264 transcode fallback.
      setUseTranscode(true);
      setCanPlay(false);
    } else {
      // Second error (transcode also failed): show terminal state.
      setVideoFailed(true);
    }
  }, [useTranscode]);

  const handleDoubleClick = useCallback(() => {
    if (selectedFilePath !== null) {
      openFullRes(selectedFilePath);
    }
  }, [selectedFilePath, openFullRes]);

  return (
    <div
      data-testid={PREVIEW_PANE}
      className="flex flex-col h-full bg-neutral-50 border-l border-neutral-200"
    >
      {previewMode === "grid" && selectedGroupId !== null ? (
        // Grid mode — GroupGrid is keyed on selectedGroupId so a group change
        // forces a full unmount+remount of the GroupMediaProvider and all its
        // VideoTile children. This is the web analog of Qt's clear() cleanup:
        // on group change every <video> element is destroyed (no orphan audio).
        <GroupGrid key={selectedGroupId} groupId={selectedGroupId} />
      ) : previewMode === "single" && selectedFilePath !== null && row !== null ? (
        <>
          {/* Media area — overflow-y:scroll is ALWAYS-ON (not auto).
              This is the #535 fix: prevents the portrait oscillation loop
              where the scrollbar appearance/disappearance thrashes layout. */}
          <div className="flex-1 overflow-y-scroll overflow-x-hidden flex items-start justify-center bg-neutral-900 min-h-0">
            {row.media_type === "video" ? (
              videoFailed ? (
                <div className="flex items-center justify-center h-full text-neutral-400 text-sm select-none">
                  {t("web.preview.video_cannot_be_played", "Video cannot be played")}
                </div>
              ) : (
                <>
                  {/* "Preparing video…" overlay while the transcode is in flight. */}
                  {useTranscode && !canPlay && (
                    <div className="absolute flex items-center justify-center pointer-events-none">
                      <span className="text-neutral-400 text-sm">
                        {t("web.preview.preparing_video", "Preparing video…")}
                      </span>
                    </div>
                  )}
                  <video
                    // key forces a remount (and re-attempt) when the src swaps to
                    // the transcode URL; changing src alone is not always enough.
                    key={selectedFilePath + (useTranscode ? ":h264" : "")}
                    data-testid={PREVIEW_SINGLE_IMAGE}
                    src={mediaUrl(selectedFilePath, useTranscode ? { transcode: "h264" } : undefined)}
                    controls
                    className="max-w-full max-h-full object-contain"
                    onError={handleVideoError}
                    onCanPlay={() => setCanPlay(true)}
                    // No autoplay — matches Qt desktop single-view player behaviour.
                  />
                </>
              )
            ) : (
              <img
                data-testid={PREVIEW_SINGLE_IMAGE}
                src={thumbnailUrl(selectedFilePath, 512)}
                alt={row.basename}
                className="max-w-full object-contain cursor-zoom-in"
                onDoubleClick={handleDoubleClick}
                draggable={false}
              />
            )}
          </div>

          {/* Metadata sidebar */}
          <div
            data-testid={PREVIEW_INFO}
            className="flex-shrink-0 p-3 border-t border-neutral-200 bg-white text-xs space-y-1 overflow-y-auto max-h-56"
          >
            <MetaRow label={t("web.preview.meta_name", "Name")} value={row.basename} mono />
            <MetaRow label={t("web.preview.meta_folder", "Folder")} value={row.folder} mono />
            <MetaRow
              label={t("web.preview.meta_size", "Size")}
              value={formatBytes(row.file_size_bytes)}
            />
            <MetaRow
              label={t("web.preview.meta_score", "Score")}
              value={formatScore(row.score)}
            />
            <MetaRow
              label={t("web.preview.meta_dims", "Dims")}
              value={formatDims(row.pixel_width, row.pixel_height)}
            />
            <MetaRow
              label={t("web.preview.meta_shot", "Shot")}
              value={formatDate(row.shot_date)}
            />
            <MetaRow
              label={t("web.preview.meta_created", "Created")}
              value={formatDate(row.creation_date)}
            />
          </div>
        </>
      ) : (
        <EmptyState />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MetaRow — a label / value pair in the info panel
// ---------------------------------------------------------------------------

interface MetaRowProps {
  label: string;
  value: string;
  mono?: boolean;
}

function MetaRow({ label, value, mono = false }: MetaRowProps) {
  return (
    <div className="flex gap-1.5">
      <span className="text-neutral-500 w-14 flex-shrink-0">{label}</span>
      <span
        className={
          mono
            ? "font-mono text-neutral-800 break-all"
            : "text-neutral-800 break-all"
        }
        title={value}
      >
        {value}
      </span>
    </div>
  );
}
