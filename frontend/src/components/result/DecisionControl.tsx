// 3-way decision control: "" (None) | "delete" | "ignore"
// Uses Radix Select wrapped in the ui/select primitives.

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DecisionValue } from "@/api/types";

interface DecisionControlProps {
  value: DecisionValue;
  onChange: (value: DecisionValue) => void;
  disabled?: boolean;
  "data-testid"?: string;
}

export function DecisionControl({
  value,
  onChange,
  disabled = false,
  "data-testid": testId,
}: DecisionControlProps) {
  function handleChange(raw: string) {
    // Radix Select always gives us a string; narrow to DecisionValue.
    if (raw === "" || raw === "delete" || raw === "ignore") {
      onChange(raw);
    }
  }

  // Radix Select requires a non-empty string for value; map "" → "__none__"
  const selectValue = value === "" ? "__none__" : value;

  return (
    <Select
      value={selectValue}
      onValueChange={(v) => handleChange(v === "__none__" ? "" : v)}
      disabled={disabled}
    >
      <SelectTrigger
        className="h-7 w-24 text-xs px-2"
        data-testid={testId}
        aria-label="Decision"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="__none__">None</SelectItem>
        <SelectItem value="delete">Delete</SelectItem>
        <SelectItem value="ignore">Ignore</SelectItem>
      </SelectContent>
    </Select>
  );
}
