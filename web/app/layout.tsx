import type { Metadata } from "next";
import { cookies } from "next/headers";

import { Nav } from "@/components/nav";
import { SettingsMenu } from "@/components/settings-menu";
import { ThemeProvider } from "@/components/theme-provider";
import { isTheme, THEME_COOKIE } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "Axion — trustless hackathon execution",
  description:
    "Open-source hackathon lifecycle platform: blind technical evaluation, Z-score normalized judging, and a cryptographically audited scoring trail.",
};

/**
 * Reads the stored theme so the correct class is in the very first response.
 *
 * No inline bootstrap script: the class is server-rendered (so no flash), an
 * explicit choice is honoured exactly, and "system" is left for the
 * `prefers-color-scheme` media query in globals.css to resolve.
 */
async function initialThemeClass(): Promise<string> {
  const store = await cookies();
  const value = store.get(THEME_COOKIE)?.value;
  return isTheme(value) && value !== "system" ? value : "";
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const themeClass = await initialThemeClass();

  return (
    <html lang="en" className={themeClass} suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <ThemeProvider>
          <Nav />
          <main className="mx-auto w-full max-w-6xl px-6 py-10">{children}</main>
          {/* Bottom padding keeps the fixed settings control off the footer text. */}
          <footer className="mx-auto w-full max-w-6xl px-6 pb-24 pt-4 text-xs text-muted-foreground">
            Axion · submissions are judged on repository, documentation and craft first.
          </footer>
          <SettingsMenu />
        </ThemeProvider>
      </body>
    </html>
  );
}
