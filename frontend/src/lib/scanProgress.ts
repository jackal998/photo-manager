// Pure helpers for the #740 scan-progress UI: stage-label i18n mapping,
// throughput formatting, and the ETA min-samples gate. No React, no store —
// mirrors the Qt #424 desktop contract in
// app/views/dialogs/scan_dialog.py (_format_throughput / _format_eta /
// _on_stage_progress) so the web port shows the same numbers and the same
// "don't show a garbage ETA yet" gating.

/**
 * Raw `stage_name` values the backend actually emits on the SSE "stage"
 * event (`app/web/routes/scan.py` `SseScanBus.stage` → the
 * `core/app_service/scan_runner.py` `STAGE_WALK`/`STAGE_HASH`/
 * `STAGE_EXIFTOOL`/`STAGE_CLASSIFY`/`STAGE_SCORE`/`STAGE_WRITE` constants,
 * which are the literal strings below — verified by reading scan_runner.py,
 * not guessed). Each maps to its `web.scan.*` i18n key plus an English
 * fallback that matches the desktop's `scan_dialog.stage_*` wording
 * (translations/en.yml).
 */
const STAGE_LABELS: Record<string, { key: string; fallback: string }> = {
  WALK: { key: "web.scan.stage_walk", fallback: "Walking sources" },
  HASH: { key: "web.scan.stage_hash", fallback: "Hashing files" },
  EXIFTOOL: {
    key: "web.scan.stage_exiftool",
    fallback: "Reading EXIF + scoring signals",
  },
  CLASSIFY: { key: "web.scan.stage_classify", fallback: "Classifying duplicates" },
  SCORE: { key: "web.scan.stage_score", fallback: "Scoring keepers" },
  WRITE: { key: "web.scan.stage_write", fallback: "Writing manifest" },
};

/**
 * Resolve the localized label for a raw SSE stage name.
 *
 * An unmapped stage (a future backend stage this build doesn't know about
 * yet, or the empty string before the first "stage" event arrives) is NOT
 * an error — it falls back to the raw stage name, or "Running…" when empty,
 * exactly like the pre-#740 behaviour. This function must never throw.
 */
export function stageLabel(
  stageName: string,
  t: (key: string, fallback: string) => string
): string {
  const entry = STAGE_LABELS[stageName];
  if (entry === undefined) {
    return stageName !== "" ? stageName : "Running…";
  }
  return t(entry.key, entry.fallback);
}

/**
 * Render files/sec for the throughput row. Mirrors Qt's
 * `_format_throughput`: null (caller hides the row) when the rate is zero
 * or negative; whole numbers above 10/s (a wobbling tenths digit at a
 * 200/s rate looks busier than the underlying scan); one decimal below.
 */
export function formatThroughput(filesPerSec: number): string | null {
  if (filesPerSec <= 0) return null;
  if (filesPerSec >= 10) return `${Math.round(filesPerSec)} files/sec`;
  return `${filesPerSec.toFixed(1)} files/sec`;
}

/**
 * Render a human duration ETA from a remaining-count and throughput.
 * Mirrors Qt's `_format_eta`. Returns null (caller hides the row) when the
 * rate is zero/negative or there's nothing left to do.
 */
export function formatEta(remaining: number, filesPerSec: number): string | null {
  if (filesPerSec <= 0 || remaining <= 0) return null;
  const seconds = remaining / filesPerSec;
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `~${Math.floor(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (minutes < 60) return `~${minutes}m ${String(secs).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `~${hours}h ${String(mins).padStart(2, "0")}m`;
}

/** Matches the Qt `_ETA_MIN_SAMPLES_SECONDS` receiver-side gate constant. */
export const ETA_MIN_SAMPLES_SECONDS = 5;

/**
 * The Qt #424 ETA gate: suppress the ETA until (a) the current stage has
 * been running for ≥5s (enough throughput samples for the rate to have
 * settled) AND (b) the stage has a known total (an indeterminate stage —
 * CLASSIFY/SCORE/WRITE — has no "remaining" to estimate against).
 */
export function isEtaReady(elapsedSeconds: number, total: number): boolean {
  return total > 0 && elapsedSeconds >= ETA_MIN_SAMPLES_SECONDS;
}
