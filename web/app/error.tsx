"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * The client-side error boundary.
 *
 * Without it, a render error inside a page falls through to Next's default
 * screen, which is a stack trace with no way back into the app. The message is
 * shown because it is often the API's own `detail` (a closed window, a permission
 * refusal) and therefore worth reading; the digest is kept as a small reference
 * rather than a wall of text.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("axion: render error", error);
  }, [error]);

  return (
    <Card className="mx-auto max-w-xl">
      <CardHeader>
        <CardTitle>Something went wrong on this page</CardTitle>
        <CardDescription>
          The rest of the app is still running. Retrying re-renders this route; if it keeps failing,
          the API&apos;s answer is usually the reason below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 font-mono text-xs text-destructive">
          {error.message || "Unknown error"}
          {error.digest ? ` (${error.digest})` : ""}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button onClick={reset}>Try again</Button>
          <a href="/">
            <Button variant="outline">Back to the overview</Button>
          </a>
          <a href="/api/docs" target="_blank" rel="noreferrer">
            <Button variant="ghost">API reference</Button>
          </a>
        </div>
      </CardContent>
    </Card>
  );
}
