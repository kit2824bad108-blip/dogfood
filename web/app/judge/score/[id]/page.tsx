"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { JudgeSubmission } from "@/lib/types";
import { cn } from "@/lib/utils";

type Presentation = {
  submission_id: number;
  title: string;
  team_name: string | null;
  demo_url: string | null;
  video_url: string | null;
  summary: string | null;
};

function ScorePicker({
  value,
  onChange,
  disabled,
}: {
  value: number | null;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {Array.from({ length: 10 }, (_, index) => index + 1).map((score) => (
        <button
          key={score}
          type="button"
          disabled={disabled}
          onClick={() => onChange(score)}
          className={cn(
            "h-9 w-9 rounded-md border text-sm font-medium transition-colors disabled:opacity-50",
            value === score
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-background/60 hover:bg-secondary/60",
          )}
        >
          {score}
        </button>
      ))}
    </div>
  );
}

function ScoreContent() {
  const params = useParams<{ id: string }>();
  const submissionId = Number(params.id);

  const [submission, setSubmission] = useState<JudgeSubmission | null>(null);
  const [presentation, setPresentation] = useState<Presentation | null>(null);
  const [presentationError, setPresentationError] = useState<string | null>(null);

  const [technicalScore, setTechnicalScore] = useState<number | null>(null);
  const [technicalComment, setTechnicalComment] = useState("");
  const [presentationScore, setPresentationScore] = useState<number | null>(null);
  const [presentationComment, setPresentationComment] = useState("");

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const data = await api.get<{ submission: JudgeSubmission }>(`/judging/submissions/${submissionId}`);
    const found = data.submission;
    setSubmission(found);
    setTechnicalScore(found.score?.technical_score ?? null);
    setTechnicalComment(found.score?.technical_comment ?? "");
    setPresentationScore(found.score?.presentation_score ?? null);
    setPresentationComment(found.score?.presentation_comment ?? "");

    if (found.presentation_unlocked) {
      try {
        const unlocked = await api.get<{ presentation: Presentation }>(
          `/judging/submissions/${submissionId}/presentation`,
        );
        setPresentation(unlocked.presentation);
        setPresentationError(null);
      } catch (caught) {
        setPresentationError(errorMessage(caught));
      }
    }
  }, [submissionId]);

  useEffect(() => {
    load()
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, [load]);

  async function saveTechnical(event: React.FormEvent) {
    event.preventDefault();
    if (technicalScore === null) {
      setError("Choose a technical score from 1 to 10.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.post("/judging/scores", {
        submission_id: submissionId,
        technical_score: technicalScore,
        technical_comment: technicalComment || null,
      });
      await load();
      setMessage("Technical verdict recorded. The presentation tier is now unlocked for you.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function savePresentation(event: React.FormEvent) {
    event.preventDefault();
    if (presentationScore === null) {
      setError("Choose a presentation score from 1 to 10.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.post("/judging/scores", {
        submission_id: submissionId,
        presentation_score: presentationScore,
        presentation_comment: presentationComment || null,
      });
      await load();
      setMessage("Presentation verdict recorded.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading submission…</p>;
  if (!submission) return <p className="text-sm text-destructive">{error ?? "Submission not found."}</p>;

  const unlocked = submission.presentation_unlocked;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3">
            {submission.title}
            {submission.commit_integrity.flagged && <Badge variant="warning">commit review</Badge>}
            <Badge variant={unlocked ? "success" : "secondary"}>
              {unlocked ? "presentation unlocked" : "presentation locked"}
            </Badge>
          </CardTitle>
          <CardDescription>{submission.team_name}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Repository</p>
              <a
                href={submission.repo_url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-xs text-primary underline-offset-4 hover:underline"
              >
                {submission.repo_url}
              </a>
            </div>
            {submission.docs_url && (
              <div>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Documentation</p>
                <a
                  href={submission.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                >
                  {submission.docs_url}
                </a>
              </div>
            )}
          </div>
          {submission.summary && <p className="text-muted-foreground">{submission.summary}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Tier 1 — Technical evaluation</CardTitle>
          <CardDescription>
            Code quality, architecture and documentation. Scored before any demo is visible.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={saveTechnical} className="space-y-4">
            <div className="space-y-2">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Technical score</p>
              <ScorePicker value={technicalScore} onChange={setTechnicalScore} disabled={busy} />
            </div>
            <Textarea
              value={technicalComment}
              onChange={(event) => setTechnicalComment(event.target.value)}
              placeholder="What is technically strong or weak about this repository?"
            />
            <Button type="submit" disabled={busy}>
              {submission.score?.technical_score ? "Update technical verdict" : "Submit technical verdict"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card className={cn(!unlocked && "border-dashed")}>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Tier 2 — Presentation evaluation
            {!unlocked && <Badge variant="secondary">locked</Badge>}
          </CardTitle>
          <CardDescription>
            {unlocked
              ? "The demo and video are visible because your technical verdict is on file."
              : "Submit your technical verdict to unlock the demo and video."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!unlocked ? (
            <div aria-hidden className="locked-blur space-y-2 rounded-md border border-border bg-background/40 p-4">
              <p className="font-mono text-sm">https://hidden.axion-demo.dev</p>
              <p className="font-mono text-sm">https://youtu.be/hidden-demo-video</p>
              <p className="text-sm">Demo video and live deployment withheld until Tier 1 is scored.</p>
            </div>
          ) : presentation ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2 text-sm">
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Live demo</p>
                  {presentation.demo_url ? (
                    <a
                      href={presentation.demo_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                    >
                      {presentation.demo_url}
                    </a>
                  ) : (
                    <p className="text-muted-foreground">Not provided</p>
                  )}
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Demo video</p>
                  {presentation.video_url ? (
                    <a
                      href={presentation.video_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                    >
                      {presentation.video_url}
                    </a>
                  ) : (
                    <p className="text-muted-foreground">Not provided</p>
                  )}
                </div>
              </div>

              <form onSubmit={savePresentation} className="space-y-4">
                <div className="space-y-2">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Presentation score</p>
                  <ScorePicker value={presentationScore} onChange={setPresentationScore} disabled={busy} />
                </div>
                <Textarea
                  value={presentationComment}
                  onChange={(event) => setPresentationComment(event.target.value)}
                  placeholder="How convincing was the demo, given the code you just read?"
                />
                <Button type="submit" disabled={busy}>
                  {submission.score?.presentation_score
                    ? "Update presentation verdict"
                    : "Submit presentation verdict"}
                </Button>
              </form>
            </>
          ) : (
            <p className="text-sm text-destructive">{presentationError ?? "Presentation unavailable."}</p>
          )}

          {message && <p className="text-sm text-success">{message}</p>}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>

      <Link href="/judge" className="inline-block text-sm text-muted-foreground hover:text-foreground">
        ← Back to assignments
      </Link>
    </div>
  );
}

export default function ScorePage() {
  return (
    <RequireAuth roles={["judge", "admin"]}>
      <ScoreContent />
    </RequireAuth>
  );
}
