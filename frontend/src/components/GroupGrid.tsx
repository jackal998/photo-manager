// GroupGrid — renders a responsive CSS grid of VideoTile / ImageTile for a
// single group, followed by a GroupMediaController bar when the group contains
// at least one video.
//
// Sorting contract (mirrors Qt preview_pane.py:275-423):
//   VIDEOS FIRST — by aspect bucket (landscape → square → portrait), then
//   larger-file-first within each bucket.
//   IMAGES after — by file_size_bytes descending.
//
// The component receives a groupId (number) and derives the file list from the
// Zustand store. It does NOT modify any store state.
//
// GroupMediaProvider is mounted here so the context is scoped to this grid
// instance. When groupId changes the provider is replaced (via key= on the
// wrapping element in the caller — GroupGrid itself does not key itself).

import React, { useMemo } from "react";
import { useAppStore } from "@/store/useAppStore";
import type { FileRow } from "@/api/types";
import { GroupMediaProvider } from "./GroupMediaProvider";
import { VideoTile } from "./VideoTile";
import { ImageTile } from "./ImageTile";
// GroupMediaController — built by the Components-controller agent.
import { GroupMediaController } from "./GroupMediaController";
import {
  GRID_CONTAINER,
  gridVideoTileTestid,
  gridImageTileTestid,
} from "@/testids";

// ---------------------------------------------------------------------------
// Aspect-bucket helpers (mirrors Qt sort in preview_pane.py)
// ---------------------------------------------------------------------------

type AspectBucket = 0 | 1 | 2; // 0=landscape, 1=square, 2=portrait

function aspectBucket(row: FileRow): AspectBucket {
  const w = row.pixel_width;
  const h = row.pixel_height;
  // Null dims (common for video rows) fall into landscape bucket (index 0) so
  // they sort stably at the front of the video section. The Qt code has the
  // same behaviour: undefined dims => no special case => landscape assumed.
  if (w === null || h === null || w <= 0 || h <= 0) return 0;
  const ratio = w / h;
  if (ratio > 1.05) return 0; // landscape
  if (ratio < 0.95) return 2; // portrait
  return 1; // square
}

// ---------------------------------------------------------------------------
// Sort: videos-first then images, with secondary sort by file_size_bytes DESC
// ---------------------------------------------------------------------------

function sortGridItems(items: FileRow[]): FileRow[] {
  const videos = items.filter((r) => r.media_type === "video");
  const images = items.filter((r) => r.media_type !== "video");

  // FAITHFUL to Qt (preview_pane.py:286,322-323): videos carry resolution=""
  // there, so aspect_bucket_from_resolution("") collapses every null/empty-dim
  // video into a SINGLE bucket, and the secondary key -file_size sorts them
  // larger-first. Our aspectBucket() maps null/<=0 dims to bucket 0 (landscape),
  // giving the same single-bucket + file_size_bytes-descending result. This
  // collapse is intentional, not a missing-dimension bug.
  videos.sort((a, b) => {
    const bucketDiff = aspectBucket(a) - aspectBucket(b);
    if (bucketDiff !== 0) return bucketDiff;
    return b.file_size_bytes - a.file_size_bytes;
  });

  images.sort((a, b) => b.file_size_bytes - a.file_size_bytes);

  return [...videos, ...images];
}

// ---------------------------------------------------------------------------
// GroupGrid
// ---------------------------------------------------------------------------

export interface GroupGridProps {
  groupId: number;
}

export function GroupGrid({ groupId }: GroupGridProps): React.ReactElement {
  const groups = useAppStore((s) => s.manifest.groups);

  const group = groups.find((g) => g.group_number === groupId) ?? null;

  const sortedItems = useMemo<FileRow[]>(() => {
    if (group === null) return [];
    return sortGridItems(group.items);
  }, [group]);

  const videoCount = sortedItems.filter((r) => r.media_type === "video").length;
  const hasVideo = videoCount > 0;

  if (group === null) {
    return (
      <div className="flex items-center justify-center h-full text-neutral-400 text-sm select-none">
        Group not found
      </div>
    );
  }

  return (
    <GroupMediaProvider>
      <div className="flex flex-col h-full overflow-hidden">
        {/* Tile grid — scrollable */}
        <div
          data-testid={GRID_CONTAINER}
          className="flex-1 min-h-0 overflow-y-auto p-2"
        >
          <div
            className={[
              "grid gap-2",
              // Responsive: auto-fill columns, minimum 160 px each.
              "grid-cols-[repeat(auto-fill,minmax(160px,1fr))]",
            ].join(" ")}
          >
            {sortedItems.map((row) =>
              row.media_type === "video" ? (
                <VideoTile
                  key={row.file_path}
                  row={row}
                  data-testid={gridVideoTileTestid(
                    String(groupId),
                    row.basename
                  )}
                />
              ) : (
                <ImageTile
                  key={row.file_path}
                  row={row}
                  data-testid={gridImageTileTestid(
                    String(groupId),
                    row.basename
                  )}
                />
              )
            )}
          </div>
        </div>

        {/* GroupMediaController — only when the group has at least one video.
            Pass expectedPlayerCount so the threshold group-autoplay fires
            exactly once when all video tiles have registered. */}
        {hasVideo && (
          <GroupMediaController expectedPlayerCount={videoCount} />
        )}
      </div>
    </GroupMediaProvider>
  );
}
