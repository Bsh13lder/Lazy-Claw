/** Polished circular checkbox — replaces raw lucide Square/CheckSquare.
 *
 *  Three visual states:
 *  - empty: thin ring, hover lifts to accent-soft
 *  - busy:  pulsing ring while the API call is in flight
 *  - done:  filled solid in the given color with an animated check
 *
 *  Designed to be self-contained so it can sit inside list rows, the
 *  context strip's next-steps panel, or any markdown checkbox surface
 *  without restyling.
 */
import { Check } from "lucide-react";

interface Props {
  checked: boolean;
  busy?: boolean;
  /** Color of the filled state. Defaults to the LazyBrain accent. */
  color?: string;
  /** Visual size in px (square). Default 16. */
  size?: number;
  /** Title on hover — describes the action. */
  title?: string;
  onClick?: (e: React.MouseEvent) => void;
  /** Disable interaction (still renders state). */
  disabled?: boolean;
}

export function Checkbox({
  checked,
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
      aria-checked={checked}
      role="checkbox"
      className="lb-checkbox"
      style={
        {
          width: size,
          height: size,
          ["--lb-cb-color" as string]: color,
        } as React.CSSProperties
      }
      data-state={checked ? "checked" : busy ? "busy" : "empty"}
    >
      {checked && (
        <Check
          size={Math.max(10, size - 4)}
          strokeWidth={3}
          className="lb-checkbox-tick"
        />
      )}
    </button>
  );
}
