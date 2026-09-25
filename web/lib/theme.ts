/**
 * Theme constants and types, deliberately in a plain module.
 *
 * These live outside `theme-provider.tsx` because that file is a `"use client"`
 * module: a server component importing a *value* from a client module receives a
 * client reference proxy rather than the value, so `layout.tsx` would have read
 * `cookies().get(undefined)` and silently rendered no theme class.
 */
export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/** The durable preference, including "system". */
export const THEME_STORAGE_KEY = "axion-theme";

/** Written alongside it so the server can render the right class first paint. */
export const THEME_COOKIE = "axion-theme";

export const THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export function isTheme(value: string | null | undefined): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}
