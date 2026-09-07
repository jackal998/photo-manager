// ImageTile — a single image tile inside GroupGrid.
//
// Renders the thumbnail (thumbnail_url from FileRow — '/api/image?path=...&size=512').
// DOUBLE-CLICK opens the FullResViewer via store.openFullRes(path), matching
// the Qt double-click-opens-full-res contract.
//
// No video-player state, no context registration — ImageTile is stateless
// from the GroupMedia perspective.

import React, { useCallback } from "react";
import { useAppStore } from "@/store/useAppStore";
import type { FileRow } from "@/api/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ImageTileProps {
  row: FileRow;
  "data-testid"?: string;
}

// ---------------------------------------------------------------------------
// ImageTile
// ---------------------------------------------------------------------------

export function ImageTile({
  row,
  "data-testid": testId,
}: ImageTileProps): React.ReactElement {
  const openFullRes = useAppStore((s) => s.openFullRes);

  const handleDoubleClick = useCallback(() => {
    openFullRes(row.file_path);
  }, [row.file_path, openFullRes]);

  return (
    <div
      data-testid={testId}
      className="relative aspect-square bg-neutral-200 rounded overflow-hidden cursor-zoom-in select-none"
      onDoubleClick={handleDoubleClick}
      title={`${row.basename} — double-click to open full resolution`}
    >
      <img
        src={row.thumbnail_url}
        alt={row.basename}
        className="absolute inset-0 w-full h-full object-cover"
        draggable={false}
        loading="lazy"
      />

      {/* Filename label at the bottom */}
      <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-xs truncate px-1 py-0.5 pointer-events-none">
        {row.basename}
      </div>
    </div>
  );
}
