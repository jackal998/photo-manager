// TypeFilter — select widget for filtering ExecuteTree rows by decision type.

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EXECUTE_TYPE_FILTER } from "@/testids";

export type TypeFilterValue = "all" | "delete" | "ignore";

interface TypeFilterProps {
  value: TypeFilterValue;
  onChange: (value: TypeFilterValue) => void;
}

export function TypeFilter({ value, onChange }: TypeFilterProps) {
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
        aria-label="Filter by decision type"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">All decided</SelectItem>
        <SelectItem value="delete">Delete only</SelectItem>
        <SelectItem value="ignore">Remove only</SelectItem>
      </SelectContent>
    </Select>
  );
}
