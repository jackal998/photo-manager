// NumericPanel — threshold or top-N input for numeric/date fields.
//
// Two modes:
//   Threshold: cmp-op combo + value input → encodes "__cmp__:OP:VALUE"
//   Top-N:     order combo (Top/Bottom) + N input → encodes "__top_n__:N:asc|desc"
//
// Pattern encoding mirrors select_dialog.py exactly:
//   Threshold: "__cmp__:>:1000"
//   Top-N:     "__top_n__:3:desc"

import { useCallback, useState } from "react";

import { useT } from "@/i18n/useT";
import {
  ACTION_NUMERIC_ROW,
  ACTION_NUMERIC_MODE_THRESHOLD,
  ACTION_NUMERIC_MODE_TOPN,
  ACTION_NUMERIC_CMP,
  ACTION_NUMERIC_VALUE,
  ACTION_NUMERIC_ERROR,
  ACTION_NUMERIC_ORDER,
  ACTION_NUMERIC_N,
} from "@/testids";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DATE_FIELDS = new Set(["Creation Date", "Shot Date"]);

type CmpOp = ">" | ">=" | "<" | "<=" | "==" | "!=";
const CMP_OPS: CmpOp[] = [">", ">=", "<", "<=", "==", "!="];

type NumericMode = "threshold" | "topn";
type Order = "top" | "bottom"; // top = desc, bottom = asc

// ---------------------------------------------------------------------------
// Encode helpers
// ---------------------------------------------------------------------------

function encodeThreshold(op: CmpOp, value: string): string {
  return `__cmp__:${op}:${value}`;
}

function encodeTopN(n: string, order: Order): string {
  const dir = order === "top" ? "desc" : "asc";
  return `__top_n__:${n}:${dir}`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NumericPanelProps {
  field: string;
  pattern: string;
  onPatternChange: (pattern: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function NumericPanel({ field, onPatternChange }: NumericPanelProps) {
  const t = useT();

  const isDate = DATE_FIELDS.has(field);

  const [mode, setMode] = useState<NumericMode>("threshold");
  const [cmpOp, setCmpOp] = useState<CmpOp>(">=");
  const [thresholdValue, setThresholdValue] = useState("");
  const [order, setOrder] = useState<Order>("top");
  const [nValue, setNValue] = useState("3");
  const [thresholdError, setThresholdError] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Mode switch
  // ---------------------------------------------------------------------------

  const handleModeThreshold = useCallback(() => {
    setMode("threshold");
    // Re-encode current values
    if (thresholdValue !== "") {
      onPatternChange(encodeThreshold(cmpOp, thresholdValue));
    } else {
      onPatternChange("");
    }
  }, [cmpOp, thresholdValue, onPatternChange]);

  const handleModeTopN = useCallback(() => {
    setMode("topn");
    const n = nValue !== "" ? nValue : "3";
    onPatternChange(encodeTopN(n, order));
  }, [nValue, order, onPatternChange]);

  // ---------------------------------------------------------------------------
  // Threshold handlers
  // ---------------------------------------------------------------------------

  const handleCmpOpChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const op = e.target.value as CmpOp;
      setCmpOp(op);
      if (thresholdValue !== "") {
        onPatternChange(encodeThreshold(op, thresholdValue));
      }
    },
    [thresholdValue, onPatternChange]
  );

  const handleThresholdValueChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setThresholdValue(val);
      setThresholdError(null);

      if (val === "") {
        onPatternChange("");
        return;
      }

      // Validate: date fields expect YYYY-MM-DD, numeric fields expect a number
      if (isDate) {
        const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(val);
        if (!dateOk) {
          setThresholdError(
            t(
              "web.action_dialog.invalid_threshold",
              "Couldn't parse '{input}' as a value for this field",
              { input: val }
            )
          );
          onPatternChange(""); // Don't submit invalid pattern
          return;
        }
      } else {
        if (isNaN(Number(val))) {
          setThresholdError(
            t(
              "web.action_dialog.invalid_threshold",
              "Couldn't parse '{input}' as a value for this field",
              { input: val }
            )
          );
          onPatternChange("");
          return;
        }
      }

      onPatternChange(encodeThreshold(cmpOp, val));
    },
    [cmpOp, isDate, t, onPatternChange]
  );

  // ---------------------------------------------------------------------------
  // Top-N handlers
  // ---------------------------------------------------------------------------

  const handleOrderChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const ord = e.target.value as Order;
      setOrder(ord);
      const n = nValue !== "" ? nValue : "3";
      onPatternChange(encodeTopN(n, ord));
    },
    [nValue, onPatternChange]
  );

  const handleNChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setNValue(val);
      if (val !== "" && !isNaN(Number(val)) && Number(val) >= 1) {
        onPatternChange(encodeTopN(val, order));
      } else {
        onPatternChange("");
      }
    },
    [order, onPatternChange]
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div data-testid={ACTION_NUMERIC_ROW} className="space-y-3">
      {/* Mode radio */}
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-1.5 cursor-pointer text-sm">
          <input
            data-testid={ACTION_NUMERIC_MODE_THRESHOLD}
            type="radio"
            name="numeric-mode"
            value="threshold"
            checked={mode === "threshold"}
            onChange={handleModeThreshold}
            className="accent-neutral-700"
          />
          {t("web.action_dialog.numeric_mode_threshold", "Threshold")}
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer text-sm">
          <input
            data-testid={ACTION_NUMERIC_MODE_TOPN}
            type="radio"
            name="numeric-mode"
            value="topn"
            checked={mode === "topn"}
            onChange={handleModeTopN}
            className="accent-neutral-700"
          />
          {t("web.action_dialog.numeric_mode_topn", "Top N per group")}
        </label>
      </div>

      {/* Threshold row */}
      {mode === "threshold" && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-sm text-neutral-600 flex-shrink-0">
              {t("web.action_dialog.numeric_threshold_label", "Where the value is")}
            </span>
            <select
              data-testid={ACTION_NUMERIC_CMP}
              value={cmpOp}
              onChange={handleCmpOpChange}
              className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400"
            >
              {CMP_OPS.map((op) => {
                // Map operator symbol(s) to i18n key suffix
                const keyMap: Record<string, string> = {
                  ">": "gt", ">=": "ge", "<": "lt", "<=": "le", "==": "eq", "!=": "ne",
                };
                const keySuffix = keyMap[op] ?? op;
                return (
                  <option key={op} value={op}>
                    {t(`web.action_dialog.cmp_op_${keySuffix}`, op)}
                  </option>
                );
              })}
            </select>
            <input
              data-testid={ACTION_NUMERIC_VALUE}
              type="text"
              value={thresholdValue}
              onChange={handleThresholdValueChange}
              placeholder={
                isDate
                  ? t(
                      "web.action_dialog.numeric_value_placeholder_date",
                      "type a date (YYYY-MM-DD)…"
                    )
                  : t(
                      "web.action_dialog.numeric_value_placeholder_number",
                      "type a number…"
                    )
              }
              className="rounded border border-neutral-300 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1"
            />
          </div>
          {thresholdError !== null && (
            <p
              data-testid={ACTION_NUMERIC_ERROR}
              className="text-xs text-red-600"
            >
              {thresholdError}
            </p>
          )}
        </div>
      )}

      {/* Top-N row */}
      {mode === "topn" && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-neutral-600 flex-shrink-0">
            {t("web.action_dialog.numeric_topn_label", "Pick the")}
          </span>
          <select
            data-testid={ACTION_NUMERIC_ORDER}
            value={order}
            onChange={handleOrderChange}
            className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400"
          >
            <option value="top">
              {t("web.action_dialog.numeric_order_top", "Top")}
            </option>
            <option value="bottom">
              {t("web.action_dialog.numeric_order_bottom", "Bottom")}
            </option>
          </select>
          <input
            data-testid={ACTION_NUMERIC_N}
            type="number"
            min={1}
            max={10000}
            value={nValue}
            onChange={handleNChange}
            className="rounded border border-neutral-300 px-2 py-1 text-sm w-24 focus:outline-none focus:ring-2 focus:ring-neutral-400"
          />
          <span className="text-sm text-neutral-600">
            {t("web.action_dialog.numeric_topn_suffix", "in each group")}
          </span>
        </div>
      )}
    </div>
  );
}
