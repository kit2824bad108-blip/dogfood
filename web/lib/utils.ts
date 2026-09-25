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
