"use client";

import { BookOpen, Check, LogOut, Monitor, Moon, Settings, Sun, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type Theme, useTheme } from "@/components/theme-provider";
import { api } from "@/lib/api";
import type { AuthStatus, Me } from "@/lib/types";
import { cn } from "@/lib/utils";

const OPTIONS: Array<{ value: Theme; label: string; icon: typeof Sun }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-xs">
      <span className="uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="truncate text-right font-mono">{value}</span>
    </div>
  );
}

/**
 * The settings control in the bottom-left corner.
 *
 * It sits above the page in a fixed position so it is reachable from every
 * screen — including the public ones, where the nav has nothing to configure.
 * Theme, environment and session are all actionable here, and the panel closes
 * on outside click, on Escape, and after any action that navigates away.
 */
export function SettingsMenu() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<AuthStatus>("/auth/status").then(setStatus).catch(() => setStatus(null));
    api.get<Me>("/auth/me").then(setMe).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function signOut() {
    setOpen(false);
    try {
      await api.post("/auth/logout");
    } finally {
      window.location.href = "/";
    }
  }

  const window_ = status ? new Date(status.event_start).toLocaleString() : null;
  const windowEnd = status ? new Date(status.event_end).toLocaleString() : null;

  return (
    <div ref={containerRef} className="fixed bottom-4 left-4 z-50 print:hidden">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Settings"
        title="Settings"
        className={cn(
          "flex items-center gap-2 rounded-full border border-border bg-card/95 py-2 pl-2.5 pr-3.5",
          "text-xs font-medium shadow-sm backdrop-blur transition-colors hover:bg-secondary",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          open && "ring-2 ring-ring",
        )}
      >
        <Settings className="h-4 w-4 text-primary" aria-hidden />
        <span className="hidden sm:inline">Settings</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Settings"
          className={cn(
            "absolute bottom-full left-0 mb-2 w-72 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg",
            "border border-border bg-card text-card-foreground shadow-lg",
          )}
        >
          <div className="space-y-3 border-b border-border/70 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Appearance
            </p>
            <div className="grid grid-cols-3 gap-1.5">
              {OPTIONS.map((option) => {
                const Icon = option.icon;
                const active = theme === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTheme(option.value)}
                    aria-pressed={active}
                    className={cn(
                      "flex flex-col items-center gap-1 rounded-md border px-2 py-2 text-xs transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border text-muted-foreground hover:bg-secondary",
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2 border-b border-border/70 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Event
            </p>
            <Row label="Name" value={status?.event_name ?? "—"} />
            <Row label="Opens" value={window_ ?? "—"} />
            <Row label="Closes" value={windowEnd ?? "—"} />
            <Row
              label="Integrity"
              value={status ? (status.mock_github ? "mock (offline)" : "live GitHub API") : "—"}
            />
            {status?.local_dev_login && (
              <p className="flex items-center gap-1.5 pt-1 text-xs text-primary">
                <WifiOff className="h-3.5 w-3.5" aria-hidden />
                Offline mode: local dev login is available.
              </p>
            )}
          </div>

          <div className="space-y-1 p-2">
            <a
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <BookOpen className="h-4 w-4" aria-hidden />
              API reference (OpenAPI)
            </a>
            <a
              href="/gallery"
              className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <Check className="h-4 w-4" aria-hidden />
              Public gallery
            </a>
            {me?.authenticated && (
              <button
                type="button"
                onClick={signOut}
                className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              >
                <LogOut className="h-4 w-4" aria-hidden />
                Sign out ({me.user?.email})
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
