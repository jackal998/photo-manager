// TypeFilter — select widget for filtering ExecuteTree rows by decision type.

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EXECUTE_TYPE_FILTER } from "@/testids";
import { useT } from "@/i18n/useT";

export type TypeFilterValue = "all" | "delete" | "ignore";

interface TypeFilterProps {
  value: TypeFilterValue;
  onChange: (value: TypeFilterValue) => void;
}

export function TypeFilter({ value, onChange }: TypeFilterProps) {
  // Copy audit E1 — the aria-label and all three options were hardcoded
  // English, and the third read "Remove only". They now carry the one
  // decision vocabulary (Keep / Delete / Skip, owner decision 2026-09-18);
  // the option VALUES ("all" / "delete" / "ignore") are the wire values and
  // are untouched.
  const t = useT();

  function handleChange(raw: string) {
    if (raw === "all" || raw === "delete" || raw === "ignore") {
      onChange(raw);
    }
  }

  return (
    <Select value={value} onValueChange={handleChange}>
      <SelectTrigger
        className="h-8 w-36 text-xs"
        data-testid={EXECUTE_TYPE_FILTER}
        aria-label={t("web.execute_dialog.filter_aria", "Filter by decision type")}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">
          {t("web.execute_dialog.filter_all", "All decided")}
        </SelectItem>
        <SelectItem value="delete">
          {t("web.execute_dialog.filter_delete_only", "Delete only")}
        </SelectItem>
        <SelectItem value="ignore">
          {t("web.execute_dialog.filter_skip_only", "Skip only")}
        </SelectItem>
      </SelectContent>
    </Select>
  );
}
