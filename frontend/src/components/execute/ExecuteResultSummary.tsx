// ExecuteResultSummary — renders the Files-Not-Found / Files-Failed-to-Delete
// split after an Execute completes (#742). Mirrors the desktop's two
// QMessageBox.warning dialogs (app/views/dialogs/execute_action_dialog.py
// _on_execute, execute_dialog.files_not_found_* / files_failed_*) as inline
// sections instead of blocking dialogs — the web keeps the execute dialog
// open and lets the user review both lists alongside the (now-updated) tree.
//
// Renders nothing when both lists are empty (the common all-succeeded case).
// This is purely additive to the existing generic `executeError` alert,
// which still covers whole-operation errors (e.g. a locked-paths 409).

import { useT } from "@/i18n/useT";
import { EXECUTE_RESULT_MISSING, EXECUTE_RESULT_FAILED } from "@/testids";

export interface ExecuteResultSummaryProps {
  /** Paths that no longer existed on disk when Execute ran. */
  missing: string[];
  /**
   * Each element is a [path, reason] pair. `reason` is the decoded winerror
   * reason (infrastructure/winerror_reasons.py) when the OS error carried a
   * known code, else the raw exception message.
   */
  failed: [string, string][];
}

export function ExecuteResultSummary({
  missing,
  failed,
}: ExecuteResultSummaryProps) {
  const t = useT();
  if (missing.length === 0 && failed.length === 0) return null;

  return (
    <div className="mt-2 flex flex-col gap-2 text-sm">
      {missing.length > 0 && (
        <div
          data-testid={EXECUTE_RESULT_MISSING}
          className="rounded-[8px] border border-caution-line border-l-[3px] bg-caution-bg px-[12px] py-[10px] text-caution-ink"
        >
          {/* Not found is the caution half of the post-execute split (the
              failed half below keeps the danger role): the files are gone,
              which is the outcome the user asked for, reported rather than
              warned about. Same three tokens as the two banners above. */}
          <p className="text-[13px] font-medium leading-[1.5]">
            <span aria-hidden="true" className="mr-2 text-[12px]">
              ▲
            </span>
            {t("web.execute_dialog.files_not_found_title", "Files Not Found")}
          </p>
          <ul className="mt-1 list-disc pl-5 text-[13px] leading-[1.5]">
            {missing.map((path) => (
              <li key={path} className="break-all">
                {path}
              </li>
            ))}
          </ul>
        </div>
      )}
      {failed.length > 0 && (
        <div
          data-testid={EXECUTE_RESULT_FAILED}
          className="rounded border border-danger-warm/40 bg-delete-row p-2"
        >
          <p className="font-medium text-danger-warm">
            {t(
              "web.execute_dialog.files_failed_title",
              "Files Failed to Delete"
            )}
          </p>
          <ul className="mt-1 list-disc pl-5 text-danger-warm">
            {failed.map(([path, reason]) => (
              <li key={path} className="break-all">
                {path} — {reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
