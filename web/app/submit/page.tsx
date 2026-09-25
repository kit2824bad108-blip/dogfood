"use client";

import { useEffect, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { Submission, TeamSummary } from "@/lib/types";

type FormState = {
  title: string;
  repo_url: string;
  docs_url: string;
  demo_url: string;
  video_url: string;
  summary: string;
};

const EMPTY: FormState = {
  title: "",
  repo_url: "",
  docs_url: "",
  demo_url: "",
  video_url: "",
  summary: "",
};

function SubmissionContent() {
  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function refresh() {
    const data = await api.get<{ submission: Submission | null; team: TeamSummary | null }>(
      "/submissions/me",
    );
    setSubmission(data.submission);
    setTeam(data.team);
    if (data.submission) {
      setForm({
        title: data.submission.title,
        repo_url: data.submission.repo_url,
        docs_url: data.submission.docs_url ?? "",
        demo_url: data.submission.demo_url ?? "",
        video_url: data.submission.video_url ?? "",
        summary: data.submission.summary ?? "",
      });
    }
    setLoading(false);
  }

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setLoading(false);
    });
  }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const data = await api.post<{ submission: Submission }>("/submissions", {
        title: form.title,
        repo_url: form.repo_url,
        docs_url: form.docs_url || null,
        demo_url: form.demo_url || null,
        video_url: form.video_url || null,
        summary: form.summary || null,
      });
      setSubmission(data.submission);
      setSaved(true);
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
    return (
      <Card>
        <CardHeader>
          <CardTitle>Join a team first</CardTitle>
          <CardDescription>Submissions belong to a team, so create or join one before submitting.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => (window.location.href = "/team")}>Go to team setup</Button>
        </CardContent>
      </Card>
    );
  }

  const integrity = submission?.commit_integrity;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Submission — {team.name}</CardTitle>
          <CardDescription>
            The repository and documentation are what judges see first. Demo and video links stay hidden until
            a judge has filed a technical verdict.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={save} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="title">Project title</Label>
                <Input
                  id="title"
                  required
                  value={form.title}
                  onChange={(event) => update("title", event.target.value)}
                  placeholder="Zero-Knowledge Vault"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="repo_url">GitHub repository</Label>
                <Input
                  id="repo_url"
                  required
                  value={form.repo_url}
                  onChange={(event) => update("repo_url", event.target.value)}
                  placeholder="https://github.com/your-org/your-repo"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="docs_url">Documentation URL</Label>
                <Input
                  id="docs_url"
                  value={form.docs_url}
                  onChange={(event) => update("docs_url", event.target.value)}
                  placeholder="README or docs link"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="demo_url">Live demo URL</Label>
                <Input
                  id="demo_url"
                  value={form.demo_url}
                  onChange={(event) => update("demo_url", event.target.value)}
                  placeholder="https://your-app.example.dev"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="video_url">Demo video URL</Label>
                <Input
                  id="video_url"
                  value={form.video_url}
                  onChange={(event) => update("video_url", event.target.value)}
                  placeholder="https://youtu.be/…"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="summary">Summary for judges</Label>
              <Textarea
                id="summary"
                value={form.summary}
                onChange={(event) => update("summary", event.target.value)}
                placeholder="What did you build, and what is technically interesting about it?"
              />
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}
            {saved && <p className="text-sm text-success">Submission saved.</p>}

            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : submission ? "Update submission" : "Submit project"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {integrity && (
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
            One submission per team, editable until judging closes.
          </p>
        </div>
        <SubmissionContent />
      </div>
    </RequireAuth>
  );
}
