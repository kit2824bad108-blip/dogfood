"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/**
 * A status message pinned to the viewport rather than the document.
 *
 * The scoring page files a verdict from the bottom of a long form, so a
 * confirmation rendered inline below the form appeared off-screen: a judge
 * clicked "Submit technical verdict" and saw nothing change. This says it where
 * they are looking.
 *
 * Confirmations fade on their own, because the work is done. Errors do not:
 * they name something the judge still has to fix, and a message that disappears
 * while it is being read is worse than no message.
 */
export function Toast({
  tone,
  children,
  onDismiss,
}: {
  tone: "success" | "error";
  children: React.ReactNode;
  onDismiss: () => void;
}) {
  // Held in a ref so an inline arrow from the caller cannot restart the timer on
  // every parent render, which is what makes auto-dismiss flaky.
  const dismiss = useRef(onDismiss);
  useEffect(() => {
    dismiss.current = onDismiss;
  });

  useEffect(() => {
    if (tone !== "success") return;
    const timer = setTimeout(() => dismiss.current(), 8_000);
    return () => clearTimeout(timer);
  }, [tone]);

  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
      className={cn(
        "fixed bottom-4 right-4 z-50 flex max-w-sm items-start gap-3 rounded-lg border bg-card p-4 text-sm shadow-lg",
        tone === "error" ? "border-destructive/40" : "border-success/40",
      )}
    >
      <p className={cn("flex-1", tone === "error" ? "text-destructive" : "text-success")}>
        {children}
      </p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss message"
        className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
