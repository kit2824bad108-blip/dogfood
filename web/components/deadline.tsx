"use client";

import { CalendarClock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { EventWindow } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * A live countdown to the submission deadline, measured against the server.
 *
 * The window payload carries the server's own `now` alongside `closes_at`, so the
 * component measures the offset between the two clocks once and then ticks
 * locally. That matters more than it looks: the deadline is enforced against the
 * server clock (`now > event_end` closes the window), so a laptop whose clock is
 * minutes out would otherwise show a countdown that disagrees with the API about
 * the only question anyone is asking. The API stays the authority; this is a
 * faithful reading of it.
 *
 * Screen readers get a summary instead of a ticker: the visible digits are hidden
 * from them and a polite status string changes only when the coarse remaining
 * time does, so it never announces a countdown once per second.
 */

type Variant = "badge" | "pill" | "card";

function coarseRemaining(ms: number): string {
  const totalMinutes = Math.max(0, Math.floor(ms / 60_000));
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days} day${days === 1 ? "" : "s"} and ${hours} hour${hours === 1 ? "" : "s"}`;
  if (hours > 0) return `${hours} hour${hours === 1 ? "" : "s"} and ${minutes} minute${minutes === 1 ? "" : "s"}`;
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

function preciseRemaining(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  if (days > 0) return `${days}d ${pad(hours)}h ${pad(minutes)}m`;
  if (hours > 0) return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
  return `${pad(minutes)}:${pad(seconds)}`;
}

function useCountdown(deadline: string | null, serverNow: string | null): number | null {
  // The offset between the server's clock and this browser's, captured when the
  // payload arrived. Recomputing it on every tick would drift with the fetch.
  const drift = useMemo(() => {
    if (!serverNow) return 0;
    const server = Date.parse(serverNow);
    return Number.isNaN(server) ? 0 : server - Date.now();
  }, [serverNow]);

  const [remaining, setRemaining] = useState<number | null>(null);

  useEffect(() => {
    if (!deadline) {
      setRemaining(null);
      return;
    }
    const target = Date.parse(deadline);
    if (Number.isNaN(target)) {
      setRemaining(null);
      return;
    }
    const compute = () => target - (Date.now() + drift);
    setRemaining(compute());
    // A minute is plenty while there are hours left; the last hour needs seconds.
    const interval = compute() > 3_600_000 ? 30_000 : 1_000;
    const timer = setInterval(() => setRemaining(compute()), interval);
    return () => clearInterval(timer);
  }, [deadline, drift]);

  return remaining;
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function Deadline({
  window: eventWindow,
  variant = "badge",
  withBar = false,
  className,
}: {
  window: EventWindow | null;
  variant?: Variant;
  withBar?: boolean;
  className?: string;
}) {
  const closed = eventWindow?.closed ?? false;
  const notYetOpen = eventWindow?.not_yet_open ?? false;
  const target = closed ? null : (eventWindow?.closes_at ?? null);
  const remaining = useCountdown(target, eventWindow?.now ?? null);

  if (!eventWindow) {
    return null;
  }

  if (closed) {
    const content = (
      <>
        <CalendarClock className="h-3.5 w-3.5" aria-hidden />
        Submissions closed
      </>
    );
    if (variant === "card") {
      return (
        <div
          className={cn(
            "rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm",
            className,
          )}
        >
          <p className="flex items-center gap-2 font-medium text-destructive">{content}</p>
          <p className="mt-1 text-muted-foreground">
            The window closed {formatDate(eventWindow.closes_at)}. Judging continues, and the API
            rejects any further write against the server clock rather than the browser&apos;s.
          </p>
        </div>
      );
    }
    return (
      <Badge
        variant="destructive"
        className={cn("gap-1.5 font-normal", variant === "pill" && "px-3 py-1", className)}
        title={`Closed ${formatDate(eventWindow.closes_at)}`}
      >
        {content}
      </Badge>
    );
  }

  const label = notYetOpen ? "Opens in" : "Closes in";
  const spoken = remaining === null ? "" : coarseRemaining(remaining);
  const urgent = remaining !== null && remaining < 3_600_000;

  if (variant === "card") {
    return (
      <div
        className={cn(
          "rounded-lg border p-4 text-sm",
          urgent ? "border-warning/40 bg-warning/5" : "border-border bg-background/40",
          className,
        )}
      >
        <p className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-muted-foreground">
            <CalendarClock className="h-3.5 w-3.5" aria-hidden />
            {label}
          </span>
          <span
            className={cn(
              "font-mono text-base font-semibold tabular-nums",
              urgent ? "text-warning" : "text-foreground",
            )}
            aria-hidden
          >
            {remaining === null ? "—" : preciseRemaining(remaining)}
          </span>
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Deadline {formatDate(eventWindow.closes_at)} — enforced by the API against its own clock,
          not this browser&apos;s.
        </p>
        {withBar && <Elapsed window={eventWindow} className="mt-3" />}
        <span className="sr-only" role="status" aria-live="polite">
          {notYetOpen ? "Submissions open in" : "Submissions close in"} {spoken}
        </span>
      </div>
    );
  }

  return (
    <span className={cn("inline-flex items-center", className)}>
      <Badge
        variant={urgent ? "warning" : "outline"}
        className={cn(
          "gap-1.5 font-normal tabular-nums",
          variant === "pill" && "px-3 py-1",
          !urgent && !closed && "border-primary/40 bg-primary/5 text-primary",
        )}
        title={`${label} ${spoken} — closes ${formatDate(eventWindow.closes_at)}`}
      >
        <CalendarClock className="h-3.5 w-3.5" aria-hidden />
        <span aria-hidden>{label} {remaining === null ? "—" : preciseRemaining(remaining)}</span>
        <span className="sr-only" role="status" aria-live="polite">
          {label} {spoken}
        </span>
      </Badge>
    </span>
  );
}

/** How much of the submission window has already elapsed. */
export function Elapsed({ window: eventWindow, className }: { window: EventWindow; className?: string }) {
  const opens = Date.parse(eventWindow.opens_at);
  const closes = Date.parse(eventWindow.closes_at);
  const now = Date.parse(eventWindow.now);
  const span = closes - opens;
  const percent =
    !Number.isFinite(span) || span <= 0 || !Number.isFinite(now)
      ? 0
      : Math.min(100, Math.max(0, ((now - opens) / span) * 100));

  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-secondary", className)}
      role="progressbar"
      aria-label="Submission window elapsed"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
    >
      <div className="h-full bg-primary" style={{ width: `${percent}%` }} />
    </div>
  );
}
