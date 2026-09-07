// Lock toggle — Radix Checkbox, checked = is_locked.

import { Checkbox } from "@/components/ui/checkbox";
import type { CheckedState } from "@radix-ui/react-checkbox";

interface LockToggleProps {
  checked: boolean;
  onChange: (locked: boolean) => void;
  "data-testid"?: string;
}

export function LockToggle({
  checked,
  onChange,
  "data-testid": testId,
}: LockToggleProps) {
  function handleCheckedChange(state: CheckedState) {
    // Radix delivers true | false | "indeterminate"; we only use boolean.
    onChange(state === true);
  }

  return (
    <Checkbox
      checked={checked}
      onCheckedChange={handleCheckedChange}
      data-testid={testId}
      aria-label="Lock row"
      className="mt-0.5"
    />
  );
}
