"use client";

/**
 * Participation records (T4): the console half.
 *
 * The panel is built around one fact that is easy to get wrong in a UI: **issuing
 * is idempotent and revocation is not an edit.** So there is no "re-issue" button —
 * running issue twice reports how many records already existed — and revoking asks
 * for a reason, because a revocation without one is a record someone cannot
 * explain to the person holding the certificate.
 *
 * The key block is the other half of the story. Records are signed with HMAC, which
 * is symmetric, so the key that lets a stranger verify also lets them forge. It is
 * published when the event window closes and not before, and the panel states that
 * rather than presenting a blank field.
 */
import { AlertTriangle, Check, Copy, Download, KeyRound, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, downloadFile, errorMessage } from "@/lib/api";
import type { RecordConsole, ParticipationRecord } from "@/lib/types";

export function RecordsPanel() {
  const [data, setData] = useState<RecordConsole | null>(null);
  const [form, setForm] = useState({ judges: true, teams: true, winners: "0" });
  const [revoking, setRevoking] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.get<RecordConsole>("/admin/records"));
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function issue() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<{
        totals: { issued: number; already_issued: number };
      }>("/admin/records/issue", {
        judges: form.judges,
        teams: form.teams,
        winners: Number(form.winners) || 0,
      });
      setNotice(
        result.totals.issued
          ? `Issued ${result.totals.issued} records (${result.totals.already_issued} already existed).`
          : `Nothing new to issue: ${result.totals.already_issued} records already cover this event.`,
      );
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(code: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/admin/records/${code}/revoke`, { reason });
      setRevoking(null);
      setReason("");
      setNotice(`${code} revoked. The signature is untouched; the revocation is a new, dated fact.`);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const key = data?.key;

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
            <ShieldCheck className="h-4 w-4 text-primary" aria-hidden /> Verification key
          </CardTitle>
          <CardDescription>{key?.reason ?? "Reading the key state…"}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3 text-sm">
          <Badge variant={key?.published ? "default" : "outline"}>
            {key?.published ? "published" : "held by the organiser"}
          </Badge>
          <span className="font-mono text-xs">{key?.algorithm ?? "hmac-sha256"}</span>
          <span className="font-mono text-xs text-muted-foreground">
            fingerprint {key?.fingerprint ?? "—"}
          </span>
          {key?.key && (
            <code className="break-all rounded bg-secondary/60 p-2 font-mono text-xs">{key.key}</code>
          )}
          <p className="w-full text-xs text-muted-foreground">{key?.scheme ?? ""}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Issue records</CardTitle>
          <CardDescription>
            A judge&apos;s record carries their verdict count, coverage and effective σ from the same
            engine the leaderboard uses. A winner&apos;s record carries its placement from that same
            ranking, duplicates excluded. Issuing twice issues nothing the second time: a signed
            statement about a moment cannot be silently replaced.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.judges}
              onChange={(event) => setForm({ ...form, judges: event.target.checked })}
            />
            Judges who filed verdicts
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.teams}
              onChange={(event) => setForm({ ...form, teams: event.target.checked })}
            />
            Teams that submitted
          </label>
          <div className="space-y-1">
            <Label htmlFor="winner-count">Podium places</Label>
            <Input
              id="winner-count"
              className="w-24"
              inputMode="numeric"
              value={form.winners}
              onChange={(event) => setForm({ ...form, winners: event.target.value })}
            />
          </div>
          <Button size="sm" disabled={busy} onClick={issue}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
            Issue
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Records{" "}
            <span className="text-sm font-normal text-muted-foreground">
              ({data?.counts.total ?? 0} issued, {data?.counts.revoked ?? 0} revoked)
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {(data?.records ?? []).length === 0 && (
            <p className="text-sm text-muted-foreground">
              No records yet. Nothing is attested until the organiser issues it.
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3">Code</th>
                  <th className="py-2 pr-3">Subject</th>
                  <th className="py-2 pr-3">Role</th>
                  <th className="py-2 pr-3">Issued</th>
                  <th className="py-2 pr-3">State</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {(data?.records ?? []).map((record: ParticipationRecord) => (
                  <tr key={record.code} className="border-t border-border/60 align-top">
                    <td className="py-2 pr-3 font-mono text-xs">{record.code}</td>
                    <td className="py-2 pr-3">
                      {record.subject_name}
                      {record.subject_ref && (
                        <span className="ml-2 font-mono text-xs text-muted-foreground">
                          {record.subject_ref}
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3 capitalize">{record.role ?? record.subject_kind}</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {record.issued_at ? new Date(record.issued_at).toLocaleDateString() : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {record.revoked ? (
                        <Badge variant="outline" className="border-destructive/40 bg-destructive/10 text-destructive">
                          revoked
                        </Badge>
                      ) : (
                        <Badge variant="default">valid</Badge>
                      )}
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            downloadFile(
                              `/records/${record.code}/certificate`,
                              `axion-certificate-${record.code}.html`,
                            ).catch((caught) => setError(errorMessage(caught)))
                          }
                        >
                          <Download className="mr-1 h-3.5 w-3.5" aria-hidden /> Certificate
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            navigator.clipboard
                              ?.writeText(`${window.location.origin}${record.verify_url.replace(/^https?:\/\/[^/]+/, "")}`)
                              .then(() => setNotice(`Copied the verification link for ${record.code}.`))
                              .catch(() => setError("The clipboard refused the write."))
                          }
                        >
                          <Copy className="mr-1 h-3.5 w-3.5" aria-hidden /> Verify link
                        </Button>
                        {!record.revoked && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => {
                              setRevoking(record.code);
                              setReason("");
                            }}
                          >
                            <XCircle className="mr-1 h-3.5 w-3.5" aria-hidden /> Revoke
                          </Button>
                        )}
                      </div>

                      {revoking === record.code && (
                        <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-destructive/30 bg-destructive/5 p-2">
                          <Input
                            aria-label={`Reason for revoking ${record.code}`}
                            placeholder="Why is this record revoked?"
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                          />
                          <Button
                            size="sm"
                            disabled={busy || reason.trim().length < 3}
                            onClick={() => revoke(record.code)}
                          >
                            Revoke
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setRevoking(null)}>
                            Cancel
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="flex items-center gap-2 pt-2 text-xs text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5" aria-hidden />
            A record is never edited. Revocation is a dated fact beside the signature, so a verifier
            sees both what was claimed and what the organiser later decided.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
