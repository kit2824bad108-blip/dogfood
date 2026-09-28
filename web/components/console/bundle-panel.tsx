"use client";

/**
 * Whole-event export and import (T4): the operability door, in the console.
 *
 * The panel makes the two rules visible instead of leaving them to the API docs:
 *
 * * **Nothing is written by accident.** Choosing a file validates and dry-runs it
 *   and shows what *would* change; applying is a second, separate press. An import
 *   that half-applied would leave a deployment that is neither the old event nor
 *   the new one.
 * * **Nothing secret travels.** The bundle carries identities — addresses, names,
 *   roles — and no password hash and no session. The panel says so where the
 *   download button is, because that is the moment the question is asked.
 *
 * The checksum is shown from the response header rather than recomputed in the
 * browser: it identifies the bytes the server produced, which is what an incident
 * note needs to quote.
 */
import { AlertTriangle, Check, Download, FileJson, Loader2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, downloadFile, errorMessage, getWithHeaders } from "@/lib/api";
import type { BundleImportResult, EventBundle } from "@/lib/types";

export function BundlePanel() {
  const [preview, setPreview] = useState<{ checksum: string | null; counts: Record<string, number> } | null>(
    null,
  );
  const [bundle, setBundle] = useState<EventBundle | null>(null);
  const [result, setResult] = useState<BundleImportResult | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const { body, headers } = await getWithHeaders<EventBundle>("/admin/bundle/export");
      setPreview({ checksum: headers.get("X-Axion-Bundle-Checksum"), counts: body.counts });
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function readFile(file: File) {
    setBusy(true);
    setError(null);
    setNotice(null);
    setErrors([]);
    setResult(null);
    try {
      const document = JSON.parse(await file.text());
      setBundle(document);
      const checked = await api.post<{ valid: boolean; errors: string[]; counts: Record<string, number> }>(
        "/admin/bundle/validate",
        { bundle: document },
      );
      setErrors(checked.errors);
      if (checked.valid) {
        // A dry run first, always: the shape being valid is not the same fact as
        // the import being harmless.
        setResult(await api.post<BundleImportResult>("/admin/bundle/import", { bundle: document }));
        setNotice("Checked, and dry-run complete. Nothing has been written yet.");
      } else {
        setError("That bundle is not importable. Nothing was written.");
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!bundle) return;
    setBusy(true);
    setError(null);
    try {
      const applied = await api.post<BundleImportResult>("/admin/bundle/import", {
        bundle,
        mode: "apply",
      });
      setResult(applied);
      setNotice("Applied. Importing the same bundle again updates rather than duplicates.");
      setBundle(null);
      if (fileInput.current) fileInput.current.value = "";
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" aria-hidden /> {error}
        </p>
      )}
      {notice && (
        <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Check className="h-4 w-4 text-primary" aria-hidden /> {notice}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FileJson className="h-4 w-4 text-primary" aria-hidden /> Export this event
          </CardTitle>
          <CardDescription>
            One JSON document holding everything the event is: tracks and prizes, users and teams,
            submissions, assignments, verdicts, ballots and comments, plus every signed participation
            record. Identities travel — addresses, names, roles — and no credential does: no password
            hash, no session. An export is not a leak.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                downloadFile("/admin/bundle/export?download=true", "axion-event-bundle.json").catch(
                  (caught) => setError(errorMessage(caught)),
                )
              }
            >
              <Download className="mr-2 h-4 w-4" aria-hidden /> Download the bundle
            </Button>
            {preview?.checksum && (
              // The checksum comes from the response header, not from a local
              // recomputation: it identifies the bytes the server produced, which is
              // what an incident note needs to quote.
              <Badge variant="outline" className="font-mono text-xs">
                sha256 {preview.checksum.slice(0, 16)}…
              </Badge>
            )}
          </div>
          {preview && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(preview.counts).map(([table, count]) => (
                <Badge key={table} variant="secondary">
                  {table} {count}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Upload className="h-4 w-4 text-primary" aria-hidden /> Import a bundle
          </CardTitle>
          <CardDescription>
            Choosing a file validates it and shows what would change. Nothing is written until you
            press Apply — a bundle that names a team it does not contain is refused whole, because half
            an event is worse than none.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="bundle-file" className="text-sm font-medium">
              Bundle JSON
            </label>
            <input
              id="bundle-file"
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              className="block w-full text-sm"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) readFile(file);
              }}
            />
          </div>

          {errors.length > 0 && (
            <ul className="space-y-1 text-sm text-destructive">
              {errors.map((problem) => (
                <li key={problem} className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                  {problem}
                </li>
              ))}
            </ul>
          )}

          {result && (
            <div className="space-y-3 rounded-lg border border-border p-4 text-sm">
              <p>
                <Badge variant={result.mode === "dry_run" ? "outline" : "default"}>
                  {result.mode === "dry_run" ? "dry run" : "applied"}
                </Badge>{" "}
                {result.note}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Would create</p>
                  <p className="font-mono text-xs">
                    {Object.entries(result.created)
                      .map(([table, count]) => `${table} +${count}`)
                      .join(" · ") || "nothing"}
                  </p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Would update</p>
                  <p className="font-mono text-xs">
                    {Object.entries(result.updated)
                      .map(([table, count]) => `${table} ~${count}`)
                      .join(" · ") || "nothing"}
                  </p>
                </div>
              </div>
              {result.mode === "dry_run" && Object.keys(result.created).length > 0 && (
                <Button size="sm" disabled={busy} onClick={apply}>
                  {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                  Apply this bundle
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
