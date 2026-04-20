/** Round task symbol — what users actually expect on a "task" row.
 *
 *  Distinct from `Checkbox` (square) on purpose: a square checkbox reads
 *  as "field with a value" (like a form), while a round symbol reads as
 *  "actionable task" (like Things 3, Linear, Todoist). LazyBrain uses
 *  squares only for inline markdown `- [ ]` items in note bodies.
 *
 *  Three states:
 *    empty: thin ring, hover lifts to accent-soft + checkmark ghost
 *    busy:  ring pulses while the API call is in flight
 *    done:  filled solid in the given color with an animated check
 */
import { Check } from "lucide-react";

interface Props {
  done: boolean;
  busy?: boolean;
  /** Color of the filled "done" state. Defaults to LazyBrain accent. */
  color?: string;
  /** Visual size in px (square). Default 16. */
  size?: number;
  title?: string;
  onClick?: (e: React.MouseEvent) => void;
  disabled?: boolean;
}

export function TaskSymbol({
  done,
  busy = false,
  color = "var(--color-accent)",
  size = 16,
  title,
  onClick,
  disabled = false,
}: Props) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        if (!disabled && !busy) onClick?.(e);
      }}
      disabled={disabled || busy}
      title={title}
      aria-checked={done}
      role="checkbox"
      className="lb-task-symbol"
      style={
        {
          width: size,
          height: size,
          ["--lb-ts-color" as string]: color,
        } as React.CSSProperties
      }
      data-state={done ? "done" : busy ? "busy" : "empty"}
    >
      <Check
        size={Math.max(9, size - 6)}
        strokeWidth={3}
        className="lb-task-symbol-tick"
      />
    </button>
  );
}
