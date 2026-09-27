"use client";

import { useEffect, useState } from "react";

import { Deadline } from "@/components/deadline";
import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { EventWindow, PublicEvent, Submission, SubmissionStatus, TeamSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type FormState = {
  title: string;
  repo_url: string;
  docs_url: string;
  demo_url: string;
  video_url: string;
  summary: string;
  track_id: string;
};

const EMPTY: FormState = {
  title: "",
  repo_url: "",
  docs_url: "",
  demo_url: "",
  video_url: "",
  summary: "",
  track_id: "",
};

function SubmissionContent() {
  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [eventWindow, setEventWindow] = useState<EventWindow | null>(null);
  const [event, setEvent] = useState<PublicEvent | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // What the server last acknowledged, so "unsaved changes" is a fact about the
  // form rather than a guess about whether the user typed something.
  const [snapshot, setSnapshot] = useState<string>(JSON.stringify(EMPTY));
  const dirty = JSON.stringify(form) !== snapshot;

  // Closes the tab and loses a half-written submission: the one navigation a
  // draft cannot survive, so the browser is asked to confirm it.
  useEffect(() => {
    if (!dirty) return;
    const warn = (navigationEvent: BeforeUnloadEvent) => {
      navigationEvent.preventDefault();
      navigationEvent.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function refresh() {
    const data = await api.get<{
      submission: Submission | null;
      team: TeamSummary | null;
    }>("/submissions/me");
    setSubmission(data.submission);
    setTeam(data.team);
    if (data.submission) {
      const loaded: FormState = {
        title: data.submission.title,
        repo_url: data.submission.repo_url,
        docs_url: data.submission.docs_url ?? "",
        demo_url: data.submission.demo_url ?? "",
        video_url: data.submission.video_url ?? "",
        summary: data.submission.summary ?? "",
        track_id: data.submission.track_id ? String(data.submission.track_id) : "",
      };
      setForm(loaded);
      setSnapshot(JSON.stringify(loaded));
    }
    setLoading(false);
  }

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setLoading(false);
    });
    // The window comes from /event, not from /submissions/me: that endpoint omits
    // it precisely when the caller has no team yet, which is the branch where a
    // participant most needs to know how long is left.
    api
      .get<PublicEvent>("/event")
      .then((payload) => {
        setEvent(payload);
        setEventWindow(payload.event.submission_window);
      })
      .catch(() => {
        setEvent(null);
        setEventWindow(null);
      });
  }, []);

  async function save(status: SubmissionStatus) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const data = await api.post<{ submission: Submission }>("/submissions", {
        title: form.title,
        repo_url: form.repo_url,
        docs_url: form.docs_url || null,
        demo_url: form.demo_url || null,
        video_url: form.video_url || null,
        summary: form.summary || null,
        track_id: form.track_id ? Number(form.track_id) : null,
        status,
      });
      setSubmission(data.submission);
      setSnapshot(JSON.stringify(form));
      setSaved(
        status === "draft"
          ? "Draft saved. It stays invisible to judges until you submit it."
          : "Submission entered. Judges have been assigned.",
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function update(key: keyof FormState, value: string) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading submission…</p>;

  if (!team) {
    // The deadline still matters here: the team has to exist *before* the window
    // closes, so the countdown is the reason this screen should feel urgent.
    return (
      <div className="space-y-6">
        <Deadline window={eventWindow} variant="card" withBar />
        <Card>
          <CardHeader>
            <CardTitle>Join a team first</CardTitle>
            <CardDescription>
              Submissions belong to a team, so create or join one before submitting. The window
              above is the same one your team will be judged on.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => (window.location.href = "/team")}>Go to team setup</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const integrity = submission?.commit_integrity;
  const isDraft = submission?.status === "draft";
  const closed = eventWindow?.closed ?? false;
  const tracks = event?.tracks ?? [];

  return (
    <div className="space-y-6">
      {/* One notice, not two: the deadline card already reports the closed window
          (and says what happens next), so the page does not repeat it. */}
      <Deadline window={eventWindow} variant="card" withBar />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3">
            Submission — {team.name}
            {submission && (
              <Badge variant={isDraft ? "warning" : "success"}>
                {isDraft ? "draft" : "submitted"}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            The repository and documentation are what judges see first. Demo and video links stay hidden
            until a judge has filed a technical verdict.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(formEvent) => {
              formEvent.preventDefault();
              save("submitted");
            }}
            className="space-y-4"
          >
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="title">Project title</Label>
                <Input
                  id="title"
                  required
                  value={form.title}
                  onChange={(inputEvent) => update("title", inputEvent.target.value)}
                  placeholder="Zero-Knowledge Vault"
                  disabled={closed}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="repo_url">GitHub repository</Label>
                <Input
                  id="repo_url"
                  required
                  value={form.repo_url}
                  onChange={(inputEvent) => update("repo_url", inputEvent.target.value)}
                  placeholder="https://github.com/your-org/your-repo"
                  disabled={closed}
                />
              </div>
              {tracks.length > 0 && (
                <div className="space-y-1.5">
                  <Label htmlFor="track_id">Track</Label>
                  <select
                    id="track_id"
                    value={form.track_id}
                    onChange={(changeEvent) => update("track_id", changeEvent.target.value)}
                    disabled={closed}
                    className={cn(
                      "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      "disabled:cursor-not-allowed disabled:opacity-50",
                    )}
                  >
                    <option value="">No track</option>
                    {tracks.map((track) => (
                      <option key={track.id} value={track.id}>
                        {track.name}
                        {track.prize_pool ? ` — ${track.prize_pool}` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="docs_url">Documentation URL</Label>
                <Input
                  id="docs_url"
                  value={form.docs_url}
                  onChange={(inputEvent) => update("docs_url", inputEvent.target.value)}
                  placeholder="README or docs link"
                  disabled={closed}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="demo_url">Live demo URL</Label>
                <Input
                  id="demo_url"
                  value={form.demo_url}
                  onChange={(inputEvent) => update("demo_url", inputEvent.target.value)}
                  placeholder="https://your-app.example.dev"
                  disabled={closed}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="video_url">Demo video URL</Label>
                <Input
                  id="video_url"
                  value={form.video_url}
                  onChange={(inputEvent) => update("video_url", inputEvent.target.value)}
                  placeholder="https://youtu.be/…"
                  disabled={closed}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="summary">Summary for judges</Label>
              <Textarea
                id="summary"
                value={form.summary}
                onChange={(inputEvent) => update("summary", inputEvent.target.value)}
                placeholder="What did you build, and what is technically interesting about it?"
                disabled={closed}
              />
            </div>

            {/* Announced, not just coloured: a status change a screen reader
                cannot hear is a status change half the users do not get. */}
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            {saved && (
              <p role="status" className="text-sm text-success">
                {saved}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {!submission || isDraft ? (
                <>
                  <Button type="submit" disabled={busy || closed}>
                    {busy ? "Saving…" : "Submit project"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy || closed}
                    onClick={() => save("draft")}
                  >
                    Save as draft
                  </Button>
                </>
              ) : (
                <Button type="submit" disabled={busy || closed}>
                  {busy ? "Saving…" : "Update submission"}
                </Button>
              )}
              {isDraft && (
                <span className="text-xs text-muted-foreground">
                  A draft is not judged, not listed in the gallery, and has no Commit Integrity check.
                </span>
              )}
              {dirty && !closed && (
                <span className="text-xs text-warning">Unsaved changes</span>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      {integrity && submission?.status === "submitted" && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Commit Integrity
              <Badge variant={integrity.flagged ? "warning" : "success"}>
                {integrity.flagged ? "Flagged for review" : "Within window"}
              </Badge>
            </CardTitle>
            <CardDescription>
              An advisory signal for organisers, not a disqualification. Commit dates are client-controlled,
              and squash merges hide history.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <Label>Commits in window</Label>
                <p className="text-lg font-semibold">
                  {integrity.pct_in_window === null ? "—" : `${integrity.pct_in_window}%`}
                </p>
                {integrity.pct_in_window !== null && (
                  <div
                    className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-secondary"
                    role="progressbar"
                    aria-label="Commits inside the event window"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(integrity.pct_in_window)}
                  >
                    <div
                      className={cn(
                        "h-full",
                        integrity.flagged ? "bg-warning" : "bg-success",
                      )}
                      style={{ width: `${Math.min(100, Math.max(0, integrity.pct_in_window))}%` }}
                    />
                  </div>
                )}
              </div>
              <div>
                <Label>Source</Label>
                <p className="font-mono text-sm">{integrity.source ?? "unavailable"}</p>
              </div>
              <div>
                <Label>Checked</Label>
                <p className="font-mono text-xs">
                  {integrity.checked_at ? new Date(integrity.checked_at).toLocaleString() : "—"}
                </p>
              </div>
            </div>
            {integrity.reason && <p className="text-muted-foreground">{integrity.reason}</p>}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function SubmitPage() {
  return (
    <RequireAuth>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Submit your project</h1>
          <p className="text-sm text-muted-foreground">
            Save a draft while you build, then submit before the server-side deadline. One submission
            per team, editable until the window closes.
          </p>
        </div>
        <SubmissionContent />
      </div>
    </RequireAuth>
  );
}
