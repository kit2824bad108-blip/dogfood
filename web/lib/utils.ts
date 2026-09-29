import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatScore(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

export function movementLabel(value: number | null | undefined) {
  if (!value) return "0";
  return value > 0 ? `+${value}` : `${value}`;
}

/**
 * Fired on `window` when an organiser moves the event clock.
 *
 * The deadline is the one number every role reads, and it lives in the header —
 * which fetches it once, on mount. A console that moves the window and leaves a
 * stale banner three inches above the form would be showing two different
 * deadlines on one screen, so the console announces the change and the header
 * listens for it.
 */
export const WINDOW_MOVED_EVENT = "axion:window-moved";
