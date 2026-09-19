// PreviewPane — inline image preview panel.
//
// Reads store.preview.selectedFilePath and store.preview.selectedGroupId.
// Mode-aware rendering (selectPreviewMode selector):
//   - 'grid'   → <GroupGrid groupId={selectedGroupId}> keyed on groupId so
//                videos are paused+unmounted on group change (no orphan audio).
//   - 'single' → the image frame + heading + metadata + decisions + score.
//   - 'empty'  → the same frame wearing the warm hatch, with
//                "Select a file to preview".
//
// Layout slice PV (#878; closes #905) gives the pane the REPLY's P1-P7 shape:
// a 42px title strip, a 4:3 image frame on the warm-tinted near-black
// `image-surround`, the filename as a heading, a five-row metadata table, the
// DECISION block #905 asked for, and the keep-worthiness bar. The pane is now
// a column that judges ONE file, not a viewport with a caption.
//
// CRITICAL (#535 fix): the pane's scroll container uses overflow-y:scroll
// (always-on, never "auto") to avoid the portrait fit-on-width oscillation
// loop where adding the content causes the scrollbar to appear, shrinking the
// viewport, causing the content to reflow, making the scrollbar disappear, …
// This replicates Qt's ScrollBarAlwaysOn invariant. Slice PV moves the
// scroller OUT one level — it now wraps the whole content column rather than
// just the image — and the frame it contains has a FIXED 4/3 aspect ratio, so
// the image's own aspect no longer participates in the pane's height at all.
// Both halves matter: the always-on scrollbar is still what makes the width
// constant, and a fixed-aspect frame is what makes the height constant.
//
// Double-clicking the image opens the full-resolution overlay via
// store.openFullRes(path).

import { useCallback, useState, useEffect } from "react";
import { Lock } from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { selectPreviewMode } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { useDateLocale } from "@/i18n/useDateLocale";
import { thumbnailUrl, mediaUrl } from "@/api/client";
import { cn } from "@/lib/utils";
import { canPlayHevc, prefersTranscodedVideo } from "@/lib/videoCapabilities";
import {
  formatBytes,
  formatScore,
  formatDims,
  formatDate,
  similarityLabel,
} from "@/lib/format";
import { scoreBarWidth } from "@/lib/scoreBar";
import {
  SIMILARITY_BADGE,
  similarityBadgeBorderClass,
  similarityBadgeState,
} from "@/lib/similarityBadge";
import {
  DECISION_ACTIVE_CHIP,
  DECISION_VOCAB,
} from "./result/DecisionControl";
import {
  PREVIEW_PANE,
  PREVIEW_SINGLE_IMAGE,
  PREVIEW_INFO,
  PREVIEW_TITLE,
  PREVIEW_DECISION,
  PREVIEW_KEEP_WORTHINESS,
} from "@/testids";
import type { DecisionValue, FileRow } from "@/api/types";
import { GroupGrid } from "./GroupGrid";
import { SimilarityLegend } from "./SimilarityLegend";

// ---------------------------------------------------------------------------
// Shared geometry (REPLY §"Preview pane")
// ---------------------------------------------------------------------------

/** The 4:3 frame, its warm hairline and its radius — worn by BOTH the single
 *  mode (surround + photo) and the empty mode (hatch + prompt), so the pane's
 *  silhouette does not change when a row is picked. */
const FRAME =
  "relative aspect-[4/3] w-full overflow-hidden rounded-[12px] border border-hairline-soft";

/** The three decision options, in the row control's order. Derived from the
 *  shared vocabulary table rather than re-listed, so the pane can never print
 *  a word the row does not. */
const DECISION_ORDER: DecisionValue[] = ["", "delete", "ignore"];

/** Testid slug per decision value — the SAME mapping the row control uses
 *  (`""` → `none`, so an id never ends in a bare dash). */
const DECISION_SLUG: Record<DecisionValue, string> = {
  "": "none",
  delete: "delete",
  ignore: "ignore",
};

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

// The empty pane is the SAME frame wearing the warm diagonal hatch the row
// thumbnail uses before it loads (REPLY P3: «placeholder = the warm diagonal
// hatch, when no photo is selected»). A hatched frame reads as "frame awaiting
// an image"; the flat centred sentence it replaces read as a broken pane.
function EmptyState() {
  const t = useT();
  return (
    <div className="flex-1 overflow-y-scroll overflow-x-hidden min-h-0 p-4">
      <div className={cn(FRAME, "thumb-hatch")} />
      <p className="mt-[14px] text-center text-sm text-ink-muted select-none">
        {t("web.preview.select_file", "Select a file to preview")}
      </p>
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
  // The APP's locale, not the browser's — see useDateLocale.
  const dateLocale = useDateLocale();

  const row = selectedFilePath !== null ? findRow(groups, selectedFilePath) : null;
  // The same 5-state badge spec the row cell reads — colour + border STYLE +
  // weight, so the state survives a grayscale render (lib/similarityBadge.ts).
  const badge =
    row !== null ? SIMILARITY_BADGE[similarityBadgeState(row.similarity)] : null;

  // Transcode fallback state — reset when the selected file changes.
  //
  // The initial value is a HINT from the #787 capability probe: when the engine
  // has told us it cannot decode HEVC and this is an HEVC-in-practice
  // container, start on the transcode instead of paying a guaranteed-to-fail
  // original-bytes attempt first. The probe answers "unknown" until it
  // resolves (and always, in jsdom), so this is `false` — today's behaviour —
  // unless we positively know better. The error handler below turns this into
  // a two-attempt contract: whichever source we start on, the first error
  // swaps to the OTHER one.
  const [useTranscode, setUseTranscode] = useState(
    () => selectedFilePath !== null && prefersTranscodedVideo(selectedFilePath)
  );
  // Whether the one allowed swap has been spent. Tracked separately from
  // useTranscode because that flag alone is ambiguous once the #787 hint can
  // choose the STARTING source: `useTranscode === true` no longer implies "we
  // already fell back", and reading it that way made a hint-started transcode
  // terminal on its first error (an ffmpeg-less server 501s, so a perfectly
  // playable H.264 .mov died without ever trying the original bytes).
  const [swapAttempted, setSwapAttempted] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [canPlay, setCanPlay] = useState(false);

  // The transcode choice is re-made during RENDER on every path change, not in
  // the effect below. Measured in headless Chromium: resetting it from an
  // effect commits one render carrying the original-bytes src, so the browser
  // starts the exact fetch this pre-check exists to skip, and only then swaps.
  // This is React's documented "adjust state when a prop changes" pattern —
  // React re-runs the render before anything reaches the DOM.
  const [hintedPath, setHintedPath] = useState<string | null>(selectedFilePath);
  if (hintedPath !== selectedFilePath) {
    setHintedPath(selectedFilePath);
    setUseTranscode(
      selectedFilePath !== null && prefersTranscodedVideo(selectedFilePath)
    );
    setSwapAttempted(false);
  }

  useEffect(() => {
    // Fire-and-forget: memoized, so this costs one decodingInfo call per page
    // life. PreviewPane mounts with the app, so the answer is normally ready
    // long before the first video row is selected.
    void canPlayHevc();
    setVideoFailed(false);
    setCanPlay(false);
  }, [selectedFilePath]);

  const handleVideoError = useCallback(() => {
    if (!swapAttempted) {
      // First error: swap to the OTHER source. Started on the original bytes
      // → try the H.264 transcode (the long-standing fallback). Started on the
      // transcode because the #787 hint chose it → try the original bytes,
      // which is the recovery path when the transcode is unavailable (ffmpeg
      // missing → HTTP 501) or when the hint was simply wrong about the file.
      setSwapAttempted(true);
      setUseTranscode((prev) => !prev);
      setCanPlay(false);
    } else {
      // Second error (both sources failed): show terminal state.
      setVideoFailed(true);
    }
  }, [swapAttempted]);

  const handleDoubleClick = useCallback(() => {
    if (selectedFilePath !== null) {
      openFullRes(selectedFilePath);
    }
  }, [selectedFilePath, openFullRes]);

  const setDecision = useAppStore((s) => s.setDecision);
  const handleDecision = useCallback(
    (value: DecisionValue) => {
      if (selectedFilePath === null) return;
      // The SAME store action the row control dispatches — #905's whole point
      // is that the pane is a second surface onto one decision, not a second
      // decision. The row's chip re-renders from the same state.
      void setDecision(selectedFilePath, value);
    },
    [selectedFilePath, setDecision]
  );

  return (
    <div
      data-testid={PREVIEW_PANE}
      className="flex flex-col h-full bg-panel border-l border-hairline"
    >
      {/* Pane title (P2) — the pane had none, which is what let it read as a
          continuation of the table rather than as its own surface. 11px
          uppercase is the hardest text on the screen to read, so it takes
          `ink-muted` (the L3 `dim2` table), not `ink-faint`. */}
      <div
        data-testid={PREVIEW_TITLE}
        className="flex h-[42px] flex-shrink-0 items-center border-b border-hairline-soft px-4 text-[11px] font-bold uppercase tracking-[.05em] text-ink-muted select-none"
      >
        {t("web.preview.title", "Preview · Selected photo")}
      </div>
      {previewMode === "grid" && selectedGroupId !== null ? (
        // Grid mode — GroupGrid is keyed on selectedGroupId so a group change
        // forces a full unmount+remount of the GroupMediaProvider and all its
        // VideoTile children. This is the web analog of Qt's clear() cleanup:
        // on group change every <video> element is destroyed (no orphan audio).
        <GroupGrid key={selectedGroupId} groupId={selectedGroupId} />
      ) : previewMode === "single" &&
        selectedFilePath !== null &&
        row !== null &&
        badge !== null ? (
        // The whole content column scrolls, and its overflow-y is ALWAYS
        // `scroll`, never `auto` — the #535 invariant, moved out one level by
        // slice PV because the metadata table lost its own `max-h-56` cap
        // («five rows do not need to scroll; let the pane scroll instead»).
        <div
          data-preview-scroll=""
          className="flex-1 overflow-y-scroll overflow-x-hidden min-h-0 p-4"
        >
          {/* Image frame (P3) — 4:3, radius 12, warm hairline, the photo
              letterboxed on `image-surround`. The FRAME owns the height, so
              the image's own aspect never feeds back into the pane's layout:
              that feedback is the other half of the #535 loop, and the frame
              is what removes it rather than merely damping it. */}
          <div className={cn(FRAME, "bg-image-surround")}>
            {/* Similarity badge over the surround (P3) — the same 5-state
                badge the row wears, at full opacity, plus a white hairline so
                it detaches from the photo rather than floating on it. */}
            <span
              data-sim-state={similarityBadgeState(row.similarity)}
              className={cn(
                "absolute left-[11px] top-[11px] z-10 inline-flex items-center gap-[5px] rounded-[5px] border px-2 py-[2px] text-[11px] ring-1 ring-white/15",
                similarityBadgeBorderClass(badge.border),
                badge.weight,
                badge.colors
              )}
            >
              {badge.prefixGlyph && (
                <span aria-hidden="true" className="text-[12px] leading-none">
                  {badge.glyph}
                </span>
              )}
              <span>{similarityLabel(row.similarity, t)}</span>
            </span>
            {row.media_type === "video" ? (
              videoFailed ? (
                <div className="flex items-center justify-center h-full text-neutral-400 text-sm select-none">
                  {t("web.preview.video_cannot_be_played", "Video cannot be played")}
                </div>
              ) : (
                <>
                  {/* "Preparing video…" overlay while the transcode is in flight. */}
                  {useTranscode && !canPlay && (
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
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
                    className="h-full w-full object-contain"
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
                className="h-full w-full object-contain cursor-zoom-in"
                onDoubleClick={handleDoubleClick}
                draggable={false}
              />
            )}
          </div>

          {/* Filename heading (P4) — the pane's subject, named once at size.
              The padlock trails it because a locked file is the one fact that
              changes what every control below can do. */}
          <h2
            title={row.basename}
            className="mt-[14px] flex items-center gap-[6px] truncate text-[15px] font-bold text-ink"
          >
            <span className="truncate">{row.basename}</span>
            {row.is_locked && (
              // The SAME lucide glyph the row's padlock wears when locked, at
              // the accent — a second, differently-drawn lock icon here would
              // read as a different state.
              <Lock
                aria-label={t("web.column.lock", "Lock")}
                className="h-[13px] w-[13px] flex-shrink-0 text-warm"
              />
            )}
          </h2>

          {/* Metadata table (P7) — five rows, striped, values right-aligned
              and mono: «right alignment is what makes a two-column metadata
              list read as a table rather than as ragged prose, and it puts the
              values on a common edge for comparison when the user clicks
              between two copies». Score left (the row carries it, and the
              keep-worthiness bar below says it in the pane's own terms) and
              Created left (it is not a property of the photograph). */}
          <div
            data-testid={PREVIEW_INFO}
            className="mt-[14px] overflow-hidden rounded-[11px] border border-hairline-soft"
          >
            <MetaRow label={t("web.preview.meta_name", "Name")} value={row.basename} odd />
            <MetaRow label={t("web.preview.meta_folder", "Folder")} value={row.folder} />
            <MetaRow
              label={t("web.preview.meta_size", "Size")}
              value={formatBytes(row.file_size_bytes)}
              odd
            />
            <MetaRow
              label={t("web.preview.meta_dims", "Resolution")}
              value={formatDims(row.pixel_width, row.pixel_height)}
            />
            <MetaRow
              label={t("web.preview.meta_shot", "Shot date")}
              value={formatDate(row.shot_date, dateLocale)}
              odd
            />
          </div>

          {/* Decision block (P5 = #905) — three full-width stacked buttons,
              «NOT a 2x2 grid — there are three states now», and at 320px three
              full-width rows give the labels room in both languages. Same
              vocabulary, same selected treatment and the same store action as
              the row control, so the two surfaces can never disagree. */}
          <div className="mt-[16px]">
            <div className="mb-[8px] text-[11px] font-bold uppercase tracking-[.04em] text-ink-muted select-none">
              {t("web.preview.decision_label", "Decision")}
            </div>
            <div
              role="group"
              aria-label={t("web.column.decision", "Decision")}
              data-testid={PREVIEW_DECISION}
              className="flex flex-col gap-[6px]"
            >
              {DECISION_ORDER.map((value) => {
                const active = row.user_decision === value;
                const vocab = DECISION_VOCAB[value];
                return (
                  <button
                    key={DECISION_SLUG[value]}
                    type="button"
                    // Locked rows refuse a decision write server-side; the row
                    // control disables itself for the same reason, and an
                    // enabled button here would be the one surface that lets a
                    // user ask for something the app will refuse.
                    disabled={row.is_locked}
                    aria-pressed={active}
                    data-testid={`${PREVIEW_DECISION}-${DECISION_SLUG[value]}`}
                    title={row.is_locked
                      ? t("web.preview.decision_locked", "This file is locked — unlock it to change its decision")
                      : undefined}
                    onClick={() => handleDecision(value)}
                    className={cn(
                      "flex h-[36px] items-center justify-center gap-[6px] rounded-[8px] border text-[12.5px] leading-none transition-colors",
                      active
                        ? DECISION_ACTIVE_CHIP[value]
                        : "border-hairline bg-transparent font-medium text-ink-muted hover:bg-subtle",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-warm",
                      row.is_locked && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {t(vocab.key, vocab.fallback)}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Keep-worthiness (P6) — the row's score, given a name. Same track
              and same SOLID fill tokens as the row's mini bar; the bar is
              aria-hidden and the number beside the label is the queryable
              value, exactly as in the row. */}
          <div className="mt-[16px]">
            <div className="mb-[6px] flex items-baseline justify-between">
              <span className="text-[11px] font-bold uppercase tracking-[.04em] text-ink-muted select-none">
                {t("web.preview.keep_worthiness", "Keep-worthiness")}
              </span>
              <span
                data-testid={PREVIEW_KEEP_WORTHINESS}
                className="font-mono text-[12px] text-ink"
              >
                {formatScore(row.score)}
              </span>
            </div>
            {row.score !== null && (
              <div
                aria-hidden="true"
                data-keep-track=""
                className="h-[7px] w-full overflow-hidden rounded-[4px] bg-score-track"
              >
                <div
                  data-keep-fill=""
                  className="h-full rounded-[4px] bg-score-fill"
                  style={{ width: scoreBarWidth(row.score) }}
                />
              </div>
            )}
          </div>
        </div>
      ) : (
        <EmptyState />
      )}
      {/* Similarity legend (audit P8, layout slice E) — the pane's last child
          and the only one outside the scroller above, so the key to the badge
          code stays on screen while the metadata scrolls under it. */}
      <SimilarityLegend />
    </div>
  );
}

// ---------------------------------------------------------------------------
// MetaRow — a label / value pair in the info panel
// ---------------------------------------------------------------------------

interface MetaRowProps {
  label: string;
  value: string;
  /** Odd row → the warm stripe. Passed explicitly rather than derived from a
   *  CSS `:nth-child`, because the five rows are five sibling elements in one
   *  frame and an `odd:` utility would silently re-stripe the table if a row
   *  were ever added conditionally. */
  odd?: boolean;
}

function MetaRow({ label, value, odd = false }: MetaRowProps) {
  return (
    <div
      className={cn(
        "flex items-baseline gap-2 px-[13px] py-[9px]",
        odd && "bg-toolbar"
      )}
    >
      <span className="flex-shrink-0 text-[12px] font-medium text-ink-muted">
        {label}
      </span>
      {/* Right-aligned mono (P7). `break-all` stays: a 320px pane holds a
          folder path only by wrapping it, and wrapping beats truncating here
          because the folder is what tells two copies apart. */}
      <span
        className="ml-auto break-all text-right font-mono text-[12px] text-ink"
        title={value}
      >
        {value}
      </span>
    </div>
  );
}
