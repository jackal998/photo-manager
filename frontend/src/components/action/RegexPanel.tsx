// RegexPanel — regex input + simple write-through row for string fields.
//
// Two controls, one source of truth:
//   SimpleRow: op combo + text input → writes through to the regex input.
//   RegexRow: direct regex input (source of truth submitted as `pattern`).
//
// Cheatsheet chips insert tokens into the regex input at cursor position.

import { type ChangeEvent, useCallback, useRef, useState } from "react";

import { useT } from "@/i18n/useT";
import {
  ACTION_REGEX_ROW,
  ACTION_REGEX_INPUT,
  ACTION_VALIDATION_ICON,
  ACTION_VALIDATION_ERROR,
  ACTION_SIMPLE_ROW,
  ACTION_SIMPLE_OP,
  ACTION_SIMPLE_TEXT,
  ACTION_SIMPLE_DISABLED_NOTE,
  actionCheatsheetTestid,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Escape a string for safe embedding in a regex. */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type SimpleOp = "contains" | "starts_with" | "ends_with" | "exact";

function simpleToRegex(op: SimpleOp, text: string): string {
  const esc = escapeRegex(text);
  switch (op) {
    case "contains":
      return esc;
    case "starts_with":
      return `^${esc}`;
    case "ends_with":
      return `${esc}$`;
    case "exact":
      return `^${esc}$`;
  }
}

/** Try to validate a regex string. Returns null if valid, error message if not. */
function validateRegex(pattern: string): string | null {
  if (pattern === "") return null;
  try {
    new RegExp(pattern);
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

const CHEATSHEET_TOKENS = [".*", "\\d", "\\w", "^", "$", "\\.", "[abc]"];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface RegexPanelProps {
  pattern: string;
  onPatternChange: (pattern: string) => void;
  /** When false (0 dedup groups → no match_fn), the Simple write-through
   *  inputs are disabled and an explanatory note is shown; the Regex input
   *  stays interactive as the only usable path. Mirrors the desktop
   *  select_dialog.py match_fn=None branch (#689 / s55). Defaults true. */
  hasGroups?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RegexPanel({
  pattern,
  onPatternChange,
  hasGroups = true,
}: RegexPanelProps) {
  const t = useT();
  const regexInputRef = useRef<HTMLInputElement>(null);

  // Simple row local state (NOT stored in Zustand — derived from regex is optional)
  const [simpleOp, setSimpleOp] = useState<SimpleOp>("contains");
  const [simpleText, setSimpleText] = useState("");

  const regexError = validateRegex(pattern);
  const isValid = pattern === "" || regexError === null;

  // ---------------------------------------------------------------------------
  // Simple write-through handler
  // ---------------------------------------------------------------------------

  const handleSimpleChange = useCallback(
    (op: SimpleOp, text: string) => {
      setSimpleOp(op);
      setSimpleText(text);
      if (text === "") {
        onPatternChange("");
      } else {
        onPatternChange(simpleToRegex(op, text));
      }
    },
    [onPatternChange]
  );

  const handleSimpleOpChange = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      handleSimpleChange(e.target.value as SimpleOp, simpleText);
    },
    [handleSimpleChange, simpleText]
  );

  const handleSimpleTextChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      handleSimpleChange(simpleOp, e.target.value);
    },
    [handleSimpleChange, simpleOp]
  );

  // ---------------------------------------------------------------------------
  // Cheatsheet chip insert
  // ---------------------------------------------------------------------------

  const handleChip = useCallback(
    (token: string) => {
      const input = regexInputRef.current;
      if (input !== null) {
        const start = input.selectionStart ?? pattern.length;
        const end = input.selectionEnd ?? pattern.length;
        const newVal = pattern.slice(0, start) + token + pattern.slice(end);
        onPatternChange(newVal);
        // Restore cursor after token insertion on next tick
        requestAnimationFrame(() => {
          input.focus();
          const newPos = start + token.length;
          input.setSelectionRange(newPos, newPos);
        });
      } else {
        onPatternChange(pattern + token);
      }
    },
    [pattern, onPatternChange]
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-3">
      {/* No-match_fn placeholder note (0 dedup groups): the Simple inputs
          below are disabled and this explains why; the Regex row stays
          interactive. Mirrors the desktop select_dialog.py match_fn=None
          branch (#689 / s55). */}
      {!hasGroups && (
        <p
          data-testid={ACTION_SIMPLE_DISABLED_NOTE}
          className="text-xs italic text-neutral-500"
        >
          {t(
            "web.action_dialog.simple_disabled_no_match_fn",
            "Write-through preview unavailable — Simple inputs are read-only on this entry point."
          )}
        </p>
      )}

      {/* Simple row */}
      <div
        data-testid={ACTION_SIMPLE_ROW}
        className="flex items-center gap-2"
      >
        <span className="text-sm text-neutral-600 flex-shrink-0">
          {t("web.action_dialog.simple_prefix", "Find rows where it")}
        </span>
        <select
          data-testid={ACTION_SIMPLE_OP}
          value={simpleOp}
          onChange={handleSimpleOpChange}
          disabled={!hasGroups}
          className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400"
        >
          <option value="contains">
            {t("web.action_dialog.simple_op_contains", "contains")}
          </option>
          <option value="starts_with">
            {t("web.action_dialog.simple_op_starts_with", "starts with")}
          </option>
          <option value="ends_with">
            {t("web.action_dialog.simple_op_ends_with", "ends with")}
          </option>
          <option value="exact">
            {t("web.action_dialog.simple_op_exact", "exactly matches")}
          </option>
        </select>
        <input
          data-testid={ACTION_SIMPLE_TEXT}
          type="text"
          value={simpleText}
          onChange={handleSimpleTextChange}
          disabled={!hasGroups}
          placeholder={t(
            "web.action_dialog.simple_text_placeholder",
            "type the text to match…"
          )}
          className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1 disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400"
        />
      </div>

      {/* Regex row */}
      <div data-testid={ACTION_REGEX_ROW} className="space-y-1">
        <label className="text-sm font-medium text-neutral-700">
          {t("web.action_dialog.regex_label", "Regex:")}
        </label>
        <div className="flex items-center gap-2">
          {/* Validation icon */}
          <span
            data-testid={ACTION_VALIDATION_ICON}
            className={
              pattern === ""
                ? "text-neutral-300 text-base select-none w-5 text-center"
                : isValid
                ? "text-green-500 text-base select-none w-5 text-center"
                : "text-red-500 text-base select-none w-5 text-center"
            }
            aria-hidden="true"
          >
            {pattern === "" ? "○" : isValid ? "✓" : "✗"}
          </span>
          <input
            ref={regexInputRef}
            data-testid={ACTION_REGEX_INPUT}
            type="text"
            value={pattern}
            onChange={(e) => onPatternChange(e.target.value)}
            placeholder={t(
              "web.action_dialog.regex_placeholder",
              "e.g. ^IMG_\\d+\\.HEIC$"
            )}
            className="rounded border border-neutral-300 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1"
            aria-invalid={!isValid}
          />
        </div>

        {/* Validation error */}
        {regexError !== null && (
          <p
            data-testid={ACTION_VALIDATION_ERROR}
            className="text-xs text-red-600"
          >
            {t("web.action_dialog.invalid_regex", "Invalid regex: {error}", {
              error: regexError,
            })}
          </p>
        )}

        {/* Cheatsheet chips */}
        <div className="flex flex-wrap gap-1 mt-1">
          {CHEATSHEET_TOKENS.map((token) => (
            <button
              key={token}
              data-testid={actionCheatsheetTestid(token)}
              type="button"
              onClick={() => handleChip(token)}
              className="rounded bg-neutral-100 border border-neutral-300 px-2 py-0.5 text-xs font-mono hover:bg-neutral-200 focus:outline-none focus:ring-1 focus:ring-neutral-400"
              title={`Insert ${token}`}
            >
              {token}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
