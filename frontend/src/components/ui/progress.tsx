// Thin wrapper around @radix-ui/react-progress.
// The `value` prop (0–100) drives the indicator width via inline style
// because Radix does not apply a CSS variable in all environments.

import * as React from "react";
import * as ProgressPrimitive from "@radix-ui/react-progress";
import { cn } from "@/lib/utils";

interface ProgressProps
  extends React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> {
  "data-testid"?: string;
}

const Progress = React.forwardRef<
  React.ElementRef<typeof ProgressPrimitive.Root>,
  ProgressProps
>(({ className, value, ...props }, ref) => (
  <ProgressPrimitive.Root
    ref={ref}
    className={cn(
      "relative h-2 w-full overflow-hidden rounded-full bg-neutral-200",
      className
    )}
    {...props}
  >
    <ProgressPrimitive.Indicator
      className="h-full bg-neutral-900 transition-all"
      style={{ width: `${value ?? 0}%` }}
    />
  </ProgressPrimitive.Root>
));
Progress.displayName = ProgressPrimitive.Root.displayName;

export { Progress };
