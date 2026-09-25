"use client";

import { useEffect, useMemo, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, downloadText, errorMessage } from "@/lib/api";
import type { ArchiveBundle, AuditEntry, Leaderboard, Overview, Submission } from "@/lib/types";
import { cn, formatScore, movementLabel } from "@/lib/utils";

type Tab = "leaderboard" | "integrity" | "audit" | "archive" | "event";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "leaderboard", label: "Leaderboard" },
  { id: "integrity", label: "Commit review" },
  { id: "audit", label: "Audit trail" },
  { id: "event", label: "Event setup" },
  { id: "archive", label: "Archive" },
];

function AdminContent() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  const [leaderboard, setLeaderboard] = useState<Leaderboard | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [flagged, setFlagged] = useState<Submission[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [archive, setArchive] = useState<ArchiveBundle | null>(null);
  const [view, setView] = useState<"axion" | "raw">("axion");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [judgeForm, setJudgeForm] = useState({ email: "", name: "", password: "" });
  const [loading, setLoading] = useState(true);

  async function refresh() {
    const [board, counts, flags, log] = await Promise.all([
      api.get<Leaderboard>("/admin/leaderboard"),
      api.get<Overview>("/admin/overview"),
      api.get<{ flagged: Submission[] }>("/admin/flagged"),
      api.get<{ entries: AuditEntry[] }>("/admin/audit?limit=100"),
    ]);
    setLeaderboard(board);
    setOverview(counts);
    setFlagged(flags.flagged);
    setAudit(log.entries);
  }

  useEffect(() => {
    refresh()
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, []);

  const rows = useMemo(() => {
    if (!leaderboard) return [];
    const copy = [...leaderboard.leaderboard];
    copy.sort((a, b) => (view === "axion" ? a.axion_rank - b.axion_rank : a.raw_rank - b.raw_rank));
    return copy;
  }, [leaderboard, view]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading console…</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-1.5">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors",
              tab === entry.id
                ? "bg-primary text-primary-foreground"
                : "bg-secondary/60 text-muted-foreground hover:text-foreground",
            )}
          >
            {entry.label}
            {entry.id === "integrity" && flagged.length > 0 && (
              <span className="ml-2 rounded-full bg-amber-500/25 px-1.5 text-xs text-amber-300">
                {flagged.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {notice && <p className="text-sm text-success">{notice}</p>}

      {tab === "leaderboard" && leaderboard && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center justify-between gap-3">
                <span>Leaderboard</span>
                <span className="flex gap-1.5">
                  <Button
                    size="sm"
                    variant={view === "axion" ? "default" : "outline"}
                    onClick={() => setView("axion")}
                  >
                    Axion normalized
                  </Button>
                  <Button
                    size="sm"
                    variant={view === "raw" ? "default" : "outline"}
                    onClick={() => setView("raw")}
                  >
                    Naive average
                  </Button>
                </span>
              </CardTitle>
              <CardDescription>
                Toggle between the naive average and Z-score normalization. Rank ± shows how far a project
                moves under Axion.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="py-2 pr-3">#</th>
                      <th className="py-2 pr-3">Project</th>
                      <th className="py-2 pr-3">Team</th>
                      <th className="py-2 pr-3 text-right">Axion</th>
                      <th className="py-2 pr-3 text-right">z</th>
                      <th className="py-2 pr-3 text-right">Raw avg</th>
                      <th className="py-2 pr-3 text-right">Rank ±</th>
                      <th className="py-2 text-right">Judges</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const rank = view === "axion" ? row.axion_rank : row.raw_rank;
                      const moved = row.rank_movement !== 0;
                      return (
                        <tr
                          key={row.submission_id}
                          className={cn(
                            "border-b border-border/50",
                            moved && "bg-primary/5",
                          )}
                        >
                          <td className="py-2 pr-3 font-mono">{rank}</td>
                          <td className="py-2 pr-3 font-medium">{row.title}</td>
                          <td className="py-2 pr-3 text-muted-foreground">{row.team}</td>
                          <td className="py-2 pr-3 text-right font-mono">{formatScore(row.axion_score)}</td>
                          <td className="py-2 pr-3 text-right font-mono text-muted-foreground">
                            {row.z_score >= 0 ? "+" : ""}
                            {formatScore(row.z_score, 3)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">{formatScore(row.raw_average)}</td>
                          <td
                            className={cn(
                              "py-2 pr-3 text-right font-mono",
                              row.rank_movement > 0 && "text-success",
                              row.rank_movement < 0 && "text-destructive",
                            )}
                          >
                            {movementLabel(row.rank_movement)}
                          </td>
                          <td className="py-2 text-right font-mono">{row.judges}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-4 text-xs text-muted-foreground">
                {leaderboard.methodology.display_mapping} · shrinkage prior{" "}
                {leaderboard.methodology.prior_strength} · {leaderboard.verdict_count} technical verdicts
              </p>
              {Object.keys(leaderboard.coverage_warnings).length > 0 && (
                <p className="mt-2 text-xs text-amber-300">
                  Thin coverage (fewer than 2 judges):{" "}
                  {Object.entries(leaderboard.coverage_warnings)
                    .map(([id, count]) => `#${id} (${count})`)
                    .join(", ")}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Grader calibration</CardTitle>
              <CardDescription>
                Each judge&apos;s own mean and spread. A narrow spread makes a judge&apos;s deviations more
                informative under Z-score normalization.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="py-2 pr-3">Judge</th>
                    <th className="py-2 pr-3 text-right">Verdicts</th>
                    <th className="py-2 pr-3 text-right">Mean</th>
                    <th className="py-2 pr-3 text-right">Sigma</th>
                    <th className="py-2 text-right">Discriminating</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.judges.map((judge) => (
                    <tr key={judge.id} className="border-b border-border/50">
                      <td className="py-2 pr-3">{judge.name}</td>
                      <td className="py-2 pr-3 text-right font-mono">{judge.verdicts}</td>
                      <td className="py-2 pr-3 text-right font-mono">{formatScore(judge.raw_mean)}</td>
                      <td className="py-2 pr-3 text-right font-mono">{formatScore(judge.raw_sigma)}</td>
                      <td className="py-2 text-right">
                        <Badge variant={judge.discriminative ? "success" : "secondary"}>
                          {judge.discriminative ? "yes" : "flat"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "integrity" && (
        <Card>
          <CardHeader>
            <CardTitle>Commit Integrity review queue</CardTitle>
            <CardDescription>
              Flagged only when less than 50% of sampled commits fall inside the event window. A signal, not
              a verdict.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {flagged.length === 0 && (
              <p className="text-sm text-muted-foreground">No submissions are flagged.</p>
            )}
            {flagged.map((submission) => (
              <div
                key={submission.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-3 last:border-0"
              >
                <div className="space-y-1">
                  <p className="font-medium">{submission.title}</p>
                  <a
                    href={submission.repo_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                  >
                    {submission.repo_url}
                  </a>
                  <p className="text-xs text-muted-foreground">{submission.commit_integrity.reason}</p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge variant="warning">
                    {submission.commit_integrity.pct_in_window}% in window
                  </Badge>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.post(`/submissions/${submission.id}/recheck`);
                        await refresh();
                        setNotice(`Re-checked ${submission.title}.`);
                      })
                    }
                  >
                    Re-check
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {tab === "audit" && (
        <Card>
          <CardHeader>
            <CardTitle>Audit trail</CardTitle>
            <CardDescription>
              Append-only. A Postgres trigger rejects UPDATE and DELETE on this table.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="max-h-[32rem] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card">
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="py-2 pr-3">Action</th>
                    <th className="py-2 pr-3">Actor</th>
                    <th className="py-2 pr-3">Entity</th>
                    <th className="py-2 pr-3">IP</th>
                    <th className="py-2">When</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((entry) => (
                    <tr key={entry.id} className="border-b border-border/50">
                      <td className="py-2 pr-3 font-mono text-xs">{entry.action}</td>
                      <td className="py-2 pr-3 text-xs">{entry.actor ?? "—"}</td>
                      <td className="py-2 pr-3 text-xs text-muted-foreground">
                        {entry.entity ?? "—"}
                        {entry.entity_id ? `:${entry.entity_id}` : ""}
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs">{entry.ip ?? "—"}</td>
                      <td className="py-2 text-xs text-muted-foreground">
                        {entry.created_at ? new Date(entry.created_at).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {tab === "event" && overview && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>{overview.event.name}</CardTitle>
              <CardDescription>
                {new Date(overview.event.starts_at).toLocaleString()} →{" "}
                {new Date(overview.event.ends_at).toLocaleString()}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {Object.entries(overview.totals).map(([key, value]) => (
                  <div key={key} className="rounded-md border border-border bg-background/40 p-3">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">
                      {key.replace(/_/g, " ")}
                    </p>
                    <p className="text-xl font-semibold">{value}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Add a judge</CardTitle>
              <CardDescription>
                New judges are assigned every existing submission automatically, which keeps per-judge
                Z-scores comparable.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="judge-email">Email</Label>
                  <Input
                    id="judge-email"
                    value={judgeForm.email}
                    onChange={(event) => setJudgeForm({ ...judgeForm, email: event.target.value })}
                    placeholder="judge@example.dev"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="judge-name">Name</Label>
                  <Input
                    id="judge-name"
                    value={judgeForm.name}
                    onChange={(event) => setJudgeForm({ ...judgeForm, name: event.target.value })}
                    placeholder="Grace Hopper"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="judge-password">Password</Label>
                  <Input
                    id="judge-password"
                    type="password"
                    value={judgeForm.password}
                    onChange={(event) => setJudgeForm({ ...judgeForm, password: event.target.value })}
                    placeholder="At least 8 characters"
                  />
                </div>
              </div>
              <Button
                disabled={busy || judgeForm.password.length < 8 || !judgeForm.email}
                onClick={() =>
                  run(async () => {
                    const result = await api.post<{ assigned: number }>("/admin/judges", judgeForm);
                    setJudgeForm({ email: "", name: "", password: "" });
                    await refresh();
                    setNotice(`Judge created and assigned to ${result.assigned} submission(s).`);
                  })
                }
              >
                Create judge
              </Button>
              <div className="pt-2">
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const result = await api.post<{ created: number }>("/admin/assignments/backfill");
                      await refresh();
                      setNotice(`Backfilled ${result.created} assignment(s).`);
                    })
                  }
                >
                  Backfill assignments
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "archive" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Archive the event</CardTitle>
              <CardDescription>
                Produces a self-contained bundle: winners, Z-normalized scores, raw averages, judge
                calibration and submissions. Nothing is deleted — spin the database down when you are ready.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              <Button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const result = await api.post<ArchiveBundle>("/admin/archive");
                    setArchive(result);
                    setNotice("Archive bundle generated.");
                  })
                }
              >
                Generate archive
              </Button>
              {archive && (
                <>
                  <Button
                    variant="outline"
                    onClick={() =>
                      downloadText(
                        "axion-archive.json",
                        JSON.stringify(archive.bundle, null, 2),
                        "application/json",
                      )
                    }
                  >
                    Download JSON
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => downloadText("RESULTS.md", archive.markdown, "text/markdown")}
                  >
                    Download Markdown
                  </Button>
                </>
              )}
            </CardContent>
          </Card>

          {archive && (
            <Card>
              <CardHeader>
                <CardTitle>Preview — RESULTS.md</CardTitle>
                <CardDescription>
                  Generated {new Date(archive.bundle.generated_at).toLocaleString()}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="max-h-96 overflow-auto rounded-md border border-border bg-background/60 p-4 text-xs">
                  {archive.markdown}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminPage() {
  return (
    <RequireAuth roles={["admin"]}>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Organiser console</h1>
          <p className="text-sm text-muted-foreground">
            The normalized leaderboard, the commit review queue, the audit trail and the archive.
          </p>
        </div>
        <AdminContent />
      </div>
    </RequireAuth>
  );
}
