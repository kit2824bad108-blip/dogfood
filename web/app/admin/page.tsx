"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, downloadFile, downloadText, errorMessage } from "@/lib/api";
import type {
  ArchiveBundle,
  AuditEntry,
  JudgingProgress,
  Leaderboard,
  Overview,
  Rubric,
  Submission,
  Track,
} from "@/lib/types";
import { cn, formatScore, movementLabel } from "@/lib/utils";

type Tab =
  | "leaderboard"
  | "judges"
  | "integrity"
  | "tracks"
  | "rubric"
  | "audit"
  | "event"
  | "archive";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "leaderboard", label: "Leaderboard" },
  { id: "judges", label: "Judging progress" },
  { id: "integrity", label: "Commit review" },
  { id: "tracks", label: "Tracks & prizes" },
  { id: "rubric", label: "Rubric" },
  { id: "audit", label: "Audit trail" },
  { id: "event", label: "Event setup" },
  { id: "archive", label: "Archive" },
];

type RubricRow = { key: string; label: string; weight: number };

function AdminContent() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  const [leaderboard, setLeaderboard] = useState<Leaderboard | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [flagged, setFlagged] = useState<Submission[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [progress, setProgress] = useState<JudgingProgress | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [overallPrizes, setOverallPrizes] = useState<
    Array<{ id: number; rank: number; title: string }>
  >([]);
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [rubricRows, setRubricRows] = useState<RubricRow[]>([]);
  const [archive, setArchive] = useState<ArchiveBundle | null>(null);
  const [view, setView] = useState<"axion" | "raw">("axion");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [judgeForm, setJudgeForm] = useState({ email: "", name: "", password: "" });
  const [trackForm, setTrackForm] = useState({ name: "", prize_pool: "", description: "" });
  const [prizeForm, setPrizeForm] = useState({ title: "", track_id: "", rank: "1" });
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const [board, counts, flags, log, judging, trackList, rubricPayload] = await Promise.all([
      api.get<Leaderboard>("/admin/leaderboard"),
      api.get<Overview>("/admin/overview"),
      api.get<{ flagged: Submission[] }>("/admin/flagged"),
      api.get<{ entries: AuditEntry[] }>("/admin/audit?limit=100"),
      api.get<JudgingProgress>("/admin/judging-progress"),
      api.get<{ tracks: Track[]; overall_prizes: Array<{ id: number; rank: number; title: string }> }>(
        "/admin/tracks",
      ),
      api.get<{ rubric: Rubric }>("/admin/rubric"),
    ]);
    setLeaderboard(board);
    setOverview(counts);
    setFlagged(flags.flagged);
    setAudit(log.entries);
    setProgress(judging);
    setTracks(trackList.tracks);
    setOverallPrizes(trackList.overall_prizes);
    setRubric(rubricPayload.rubric);
    setRubricRows(
      rubricPayload.rubric.criteria.map((criterion) => ({
        key: criterion.key,
        label: criterion.label,
        // Percentages are the same relative scale, so they are a safe fallback
        // if a payload ever arrives without explicit weights.
        weight: criterion.weight ?? criterion.percent,
      })),
    );
  }, []);

  useEffect(() => {
    refresh()
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, [refresh]);

  const rows = useMemo(() => {
    if (!leaderboard) return [];
    const copy = [...leaderboard.leaderboard];
    copy.sort((a, b) => (view === "axion" ? a.axion_rank - b.axion_rank : a.raw_rank - b.raw_rank));
    return copy;
  }, [leaderboard, view]);

  const weightTotal = rubricRows.reduce((sum, row) => sum + (Number(row.weight) || 0), 0);

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
              <span className="ml-2 rounded-full bg-warning/25 px-1.5 text-xs text-warning">
                {flagged.length}
              </span>
            )}
            {entry.id === "judges" && progress && progress.totals.outstanding > 0 && (
              <span className="ml-2 rounded-full bg-warning/25 px-1.5 text-xs text-warning">
                {progress.totals.outstanding}
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
                <span className="flex flex-wrap gap-1.5">
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
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await downloadFile("/admin/export/leaderboard.csv", "axion-leaderboard.csv");
                        setNotice("Exported the leaderboard as CSV.");
                      })
                    }
                  >
                    Export CSV
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await downloadFile("/admin/export/scores.csv", "axion-scores.csv");
                        setNotice("Exported every verdict as CSV.");
                      })
                    }
                  >
                    Export verdicts
                  </Button>
                </span>
              </CardTitle>
              <CardDescription>
                Toggle between the naive average and Z-score normalization. Rank ± shows how far a project
                moves under Axion. CSV exports log an audit entry with the row count.
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
                        <tr key={row.submission_id} className={cn("border-b border-border/50", moved && "bg-primary/5")}>
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
                <p className="mt-2 text-xs text-warning">
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

      {tab === "judges" && progress && (
        <Card>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center justify-between gap-3">
              <span>Judging progress</span>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await downloadFile("/admin/export/judging-progress.csv", "axion-judging-progress.csv");
                    setNotice("Exported judging progress as CSV.");
                  })
                }
              >
                Export CSV
              </Button>
            </CardTitle>
            <CardDescription>
              {progress.totals.technical_verdicts} of {progress.totals.expected_technical_verdicts}{" "}
              technical verdicts filed across {progress.judges.length} judges (
              {progress.totals.percent}%). {progress.totals.outstanding} still outstanding.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {progress.judges.map((judge) => (
              <div key={judge.judge_id} className="space-y-1.5 border-b border-border/60 pb-3 last:border-0">
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                  <span className="font-medium">{judge.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {judge.technical_done}/{judge.assigned} graded
                    {judge.technical_pending > 0 ? ` · ${judge.technical_pending} pending` : ""}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                  <div className="h-full bg-primary transition-all" style={{ width: `${judge.percent}%` }} />
                </div>
                <p className="text-xs text-muted-foreground">
                  presentation {judge.presentation_done}/{judge.assigned} ·{" "}
                  {judge.last_activity
                    ? `last activity ${new Date(judge.last_activity).toLocaleString()}`
                    : "no activity yet"}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
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

      {tab === "tracks" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Tracks</CardTitle>
              <CardDescription>
                Tracks are organiser configuration, held in the database and published through the
                public event endpoint. Participants pick one when they submit.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {tracks.map((track) => (
                <div key={track.id} className="border-b border-border/60 pb-3 last:border-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-medium">{track.name}</p>
                    <span className="font-mono text-xs text-muted-foreground">/{track.slug}</span>
                  </div>
                  {track.description && (
                    <p className="text-sm text-muted-foreground">{track.description}</p>
                  )}
                  {track.prize_pool && (
                    <p className="mt-0.5 font-mono text-xs text-primary">{track.prize_pool}</p>
                  )}
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {track.prizes.map((prize) => (
                      <li key={prize.id}>
                        #{prize.rank} {prize.title}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {overallPrizes.length > 0 && (
                <div className="pt-2">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">
                    Overall prizes
                  </p>
                  <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                    {overallPrizes.map((prize) => (
                      <li key={prize.id}>
                        #{prize.rank} {prize.title}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Add a track</CardTitle>
                <CardDescription>The slug is generated from the name.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="track-name">Name</Label>
                  <Input
                    id="track-name"
                    value={trackForm.name}
                    onChange={(inputEvent) => setTrackForm({ ...trackForm, name: inputEvent.target.value })}
                    placeholder="Climate Tech"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="track-pool">Prize pool</Label>
                  <Input
                    id="track-pool"
                    value={trackForm.prize_pool}
                    onChange={(inputEvent) =>
                      setTrackForm({ ...trackForm, prize_pool: inputEvent.target.value })
                    }
                    placeholder="$5,000"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="track-description">Description</Label>
                  <Input
                    id="track-description"
                    value={trackForm.description}
                    onChange={(inputEvent) =>
                      setTrackForm({ ...trackForm, description: inputEvent.target.value })
                    }
                    placeholder="Carbon-aware infrastructure"
                  />
                </div>
                <Button
                  disabled={busy || trackForm.name.trim().length < 2}
                  onClick={() =>
                    run(async () => {
                      await api.post("/admin/tracks", {
                        name: trackForm.name,
                        prize_pool: trackForm.prize_pool || null,
                        description: trackForm.description || null,
                      });
                      setTrackForm({ name: "", prize_pool: "", description: "" });
                      await refresh();
                      setNotice("Track created.");
                    })
                  }
                >
                  Create track
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Add a prize</CardTitle>
                <CardDescription>Leave the track empty for an overall prize.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="prize-title">Title</Label>
                  <Input
                    id="prize-title"
                    value={prizeForm.title}
                    onChange={(inputEvent) => setPrizeForm({ ...prizeForm, title: inputEvent.target.value })}
                    placeholder="Best use of Postgres"
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="prize-track">Track</Label>
                    <select
                      id="prize-track"
                      value={prizeForm.track_id}
                      onChange={(changeEvent) =>
                        setPrizeForm({ ...prizeForm, track_id: changeEvent.target.value })
                      }
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <option value="">Overall</option>
                      {tracks.map((track) => (
                        <option key={track.id} value={track.id}>
                          {track.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="prize-rank">Rank</Label>
                    <Input
                      id="prize-rank"
                      type="number"
                      min={1}
                      value={prizeForm.rank}
                      onChange={(inputEvent) => setPrizeForm({ ...prizeForm, rank: inputEvent.target.value })}
                    />
                  </div>
                </div>
                <Button
                  variant="secondary"
                  disabled={busy || prizeForm.title.trim().length < 2}
                  onClick={() =>
                    run(async () => {
                      await api.post("/admin/prizes", {
                        title: prizeForm.title,
                        track_id: prizeForm.track_id ? Number(prizeForm.track_id) : null,
                        rank: Number(prizeForm.rank) || 1,
                      });
                      setPrizeForm({ title: "", track_id: "", rank: "1" });
                      await refresh();
                      setNotice("Prize created.");
                    })
                  }
                >
                  Create prize
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {tab === "rubric" && (
        <Card>
          <CardHeader>
            <CardTitle>Weighted rubric</CardTitle>
            <CardDescription>
              Judges score every criterion 1–10 and the weighted mean becomes their technical verdict —
              the single number the Z-score engine normalizes. Weights are relative, so 30/70 and 3/7 are
              the same rubric. Historical verdicts keep the rubric they were filed against.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {rubricRows.map((row, index) => (
              <div key={index} className="flex flex-wrap items-end gap-3">
                <div className="w-28 space-y-1.5">
                  <Label htmlFor={`criterion-key-${index}`}>Key</Label>
                  <Input
                    id={`criterion-key-${index}`}
                    value={row.key}
                    onChange={(inputEvent) =>
                      setRubricRows((previous) =>
                        previous.map((entry, position) =>
                          position === index ? { ...entry, key: inputEvent.target.value } : entry,
                        ),
                      )
                    }
                  />
                </div>
                <div className="min-w-[10rem] flex-1 space-y-1.5">
                  <Label htmlFor={`criterion-label-${index}`}>Label</Label>
                  <Input
                    id={`criterion-label-${index}`}
                    value={row.label}
                    onChange={(inputEvent) =>
                      setRubricRows((previous) =>
                        previous.map((entry, position) =>
                          position === index ? { ...entry, label: inputEvent.target.value } : entry,
                        ),
                      )
                    }
                  />
                </div>
                <div className="w-28 space-y-1.5">
                  <Label htmlFor={`criterion-weight-${index}`}>Weight</Label>
                  <Input
                    id={`criterion-weight-${index}`}
                    type="number"
                    min={1}
                    value={row.weight}
                    onChange={(inputEvent) =>
                      setRubricRows((previous) =>
                        previous.map((entry, position) =>
                          position === index
                            ? { ...entry, weight: Number(inputEvent.target.value) }
                            : entry,
                        ),
                      )
                    }
                  />
                </div>
                <p className="pb-3 font-mono text-xs text-muted-foreground">
                  {weightTotal > 0 ? Math.round((row.weight / weightTotal) * 1000) / 10 : 0}%
                </p>
                <Button
                  variant="ghost"
                  className="mb-1"
                  disabled={rubricRows.length <= 1}
                  onClick={() =>
                    setRubricRows((previous) => previous.filter((_, position) => position !== index))
                  }
                >
                  Remove
                </Button>
              </div>
            ))}

            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  setRubricRows((previous) => [
                    ...previous,
                    { key: `criterion_${previous.length + 1}`, label: "New criterion", weight: 20 },
                  ])
                }
              >
                Add criterion
              </Button>
              <Button
                disabled={busy || rubricRows.some((row) => !row.key.trim() || !row.label.trim() || !row.weight)}
                onClick={() =>
                  run(async () => {
                    await api.post("/admin/rubric", {
                      name: rubric?.name ?? "Default technical rubric",
                      criteria: rubricRows.map((row) => ({
                        key: row.key.trim(),
                        label: row.label.trim(),
                        weight: Number(row.weight),
                      })),
                    });
                    await refresh();
                    setNotice("Rubric updated. New verdicts use these weights.");
                  })
                }
              >
                Save rubric
              </Button>
            </div>
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
                New judges are assigned every submitted project automatically, which keeps per-judge
                Z-scores comparable. Drafts are skipped.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="judge-email">Email</Label>
                  <Input
                    id="judge-email"
                    value={judgeForm.email}
                    onChange={(inputEvent) => setJudgeForm({ ...judgeForm, email: inputEvent.target.value })}
                    placeholder="judge@example.dev"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="judge-name">Name</Label>
                  <Input
                    id="judge-name"
                    value={judgeForm.name}
                    onChange={(inputEvent) => setJudgeForm({ ...judgeForm, name: inputEvent.target.value })}
                    placeholder="Grace Hopper"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="judge-password">Password</Label>
                  <Input
                    id="judge-password"
                    type="password"
                    value={judgeForm.password}
                    onChange={(inputEvent) =>
                      setJudgeForm({ ...judgeForm, password: inputEvent.target.value })
                    }
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
                calibration and submissions. Drafts are counted but never ranked. Nothing is deleted —
                spin the database down when you are ready.
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
            The normalized leaderboard, judging progress, tracks and prizes, the rubric, the commit
            review queue, the audit trail and the archive.
          </p>
        </div>
        <AdminContent />
      </div>
    </RequireAuth>
  );
}
