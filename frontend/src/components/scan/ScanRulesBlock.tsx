// PR-4a — the "Rules" sub-block of the scan dialog. One checkbox per rule.
// Kept out of ScanDialog.tsx, which is already over the file-size cap.

import { Checkbox } from "@/components/ui/checkbox";
import { useT } from "@/i18n/useT";
import {
  SCAN_RULE_B3,
  SCAN_RULE_B4,
  SCAN_RULE_B5,
  SCAN_RULE_B6,
  SCAN_RULE_B7,
  SCAN_RULE_B8,
  SCAN_RULES,
} from "@/testids";
import type { RuleId, RuleToggles } from "@/api/types";

interface ScanRulesBlockProps {
  rules: RuleToggles;
  disabled: boolean;
  /** True when aggressive auto-select is on. B3, B4, B6 and B8 act only then. */
  aggressiveOn: boolean;
  onChange: (next: RuleToggles) => void;
}

interface RuleSpec {
  id: RuleId;
  testid: string;
  label: string;
  labelDefault: string;
  help: string;
  helpDefault: string;
  /** Acts only in aggressive mode, so the label reads muted while it is off. */
  aggressiveOnly: boolean;
}

const RULE_SPECS: readonly RuleSpec[] = [
  {
    id: "B3",
    testid: SCAN_RULE_B3,
    label: "web.scan.rule_b3_label",
    labelDefault: "Exact copies: mark the extra copies for delete",
    help: "web.scan.rule_b3_off",
    helpDefault:
      "When off, exact copies are left undecided. Used by Also mark all other files for delete.",
    aggressiveOnly: true,
  },
  {
    id: "B4",
    testid: SCAN_RULE_B4,
    label: "web.scan.rule_b4_label",
    labelDefault: "RAW + JPEG: keep both",
    help: "web.scan.rule_b4_off",
    helpDefault:
      "When off, the JPEG a RAW keeper outranks can be marked for delete. Used by Also mark all other files for delete.",
    aggressiveOnly: true,
  },
  {
    id: "B5",
    testid: SCAN_RULE_B5,
    label: "web.scan.rule_b5_label",
    labelDefault: "Burst: keep every frame",
    help: "web.scan.rule_b5_off",
    helpDefault:
      "When off, burst frames are judged as near-duplicates. Takes effect at the next scan.",
    aggressiveOnly: false,
  },
  {
    id: "B6",
    testid: SCAN_RULE_B6,
    label: "web.scan.rule_b6_label",
    labelDefault: "Live Photos: the video follows its photo",
    help: "web.scan.rule_b6_off",
    helpDefault:
      "When off, a Live Photo's video is kept even when its photo is marked for delete. Used by Also mark all other files for delete.",
    aggressiveOnly: true,
  },
  {
    id: "B7",
    testid: SCAN_RULE_B7,
    label: "web.scan.rule_b7_label",
    labelDefault: "Screenshots: treat two matching PNGs as uncertain",
    help: "web.scan.rule_b7_off",
    helpDefault:
      "When off, two matching PNGs are judged like photos. Takes effect at the next scan.",
    aggressiveOnly: false,
  },
  {
    id: "B8",
    testid: SCAN_RULE_B8,
    label: "web.scan.rule_b8_label",
    labelDefault: "People and pets: delete only exact copies",
    help: "web.scan.rule_b8_off",
    helpDefault:
      "When off, a near-duplicate can be marked for delete at the Medium threshold. Used by Also mark all other files for delete.",
    aggressiveOnly: true,
  },
];

export function ScanRulesBlock({
  rules,
  disabled,
  aggressiveOn,
  onChange,
}: ScanRulesBlockProps) {
  const t = useT();
  return (
    <div
      className="mt-1 flex flex-col gap-3 border-t border-hairline-soft pt-3"
      data-testid={SCAN_RULES}
    >
      <span className="text-sm font-medium text-ink">
        {t("web.scan.rules_label", "Rules")}
      </span>
      <p className="text-xs text-ink-muted">
        {t(
          "web.scan.rules_help",
          "Our defaults. Turn a rule off to let the scan judge those files like any other duplicate."
        )}
      </p>
      {RULE_SPECS.map((spec) => {
        const muted = spec.aggressiveOnly && !aggressiveOn;
        return (
          <div key={spec.id} className="flex flex-col gap-1">
            <label className="flex items-center gap-2 text-sm select-none">
              <Checkbox
                checked={rules[spec.id]}
                disabled={disabled}
                onCheckedChange={(checked) =>
                  onChange({ ...rules, [spec.id]: checked === true })
                }
                data-testid={spec.testid}
                aria-label={t(spec.label, spec.labelDefault)}
              />
              <span className={muted ? "text-ink-muted" : "text-ink"}>
                {t(spec.label, spec.labelDefault)}
              </span>
            </label>
            <p className="pl-6 text-xs text-ink-muted">
              {t(spec.help, spec.helpDefault)}
            </p>
          </div>
        );
      })}
    </div>
  );
}
