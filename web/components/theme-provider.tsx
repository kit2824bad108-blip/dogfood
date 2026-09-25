"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  isTheme,
  THEME_COOKIE,
  THEME_COOKIE_MAX_AGE,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type Theme,
} from "@/lib/theme";

/**
 * The choice is stored twice on purpose:
 *
 *   localStorage — the durable preference, including "system".
 *   cookie       — read by the server component in layout.tsx so the <html>
 *                  class is already correct in the first paint.
 *
 * The cookie is the reason there is no inline theme script: a host that rewrites
 * or blocks inline scripts cannot break the theme, and no blocking JavaScript is
 * needed to avoid a flash of the wrong theme.
 */
export { THEME_COOKIE, THEME_STORAGE_KEY, type ResolvedTheme, type Theme };

type ThemeContextValue = {
  /** What the user chose, including "system". */
  theme: Theme;
  /** What is actually being displayed right now. */
  resolved: ResolvedTheme;
  setTheme: (theme: Theme) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemPreference(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedPreference(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (isTheme(stored)) return stored;
  } catch {
    // Storage unavailable (private mode, blocked cookies): fall through to system.
  }
  return "system";
}

function apply(theme: Theme, resolved: ResolvedTheme) {
  const root = document.documentElement;
  // "system" intentionally leaves both classes off: the media query in
  // globals.css then applies the OS preference, and keeps applying it if the OS
  // preference changes while the page is open.
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  // Keep the cookie in step so the next server render is already correct.
  const stored = theme === "system" ? "system" : resolved;
  document.cookie = `${THEME_COOKIE}=${stored}; path=/; max-age=${THEME_COOKIE_MAX_AGE}; samesite=lax`;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");
  const [resolved, setResolved] = useState<ResolvedTheme>("light");

  // Sync with what the user chose last time. The server already rendered the
  // matching class, so this is a no-op visually.
  useEffect(() => {
    const preference = storedPreference();
    setThemeState(preference);
  }, []);

  useEffect(() => {
    const effective = theme === "system" ? systemPreference() : theme;
    setResolved(effective);
    apply(theme, effective);

    // Only needed for the toggle's label while the OS drives the theme: the
    // colours themselves follow the media query.
    if (theme !== "system") return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(systemPreference());
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Preference not persisted; the theme still applies for this page.
    }
  }, []);

  const value = useMemo(() => ({ theme, resolved, setTheme }), [theme, resolved, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === null) {
    throw new Error("useTheme must be used inside <ThemeProvider>");
  }
  return context;
}
