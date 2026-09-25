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
import type { JudgeSubmission, Rubric } from "@/lib/types";
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
  label,
}: {
  value: number | null;
  onChange: (next: number) => void;
  disabled?: boolean;
  /** Announced by screen readers; a bare "7" button is meaningless on its own. */
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {Array.from({ length: 10 }, (_, index) => index + 1).map((score) => (
        <button
          key={score}
          type="button"
          disabled={disabled}
          onClick={() => onChange(score)}
          aria-pressed={value === score}
          aria-label={`${label}: ${score} out of 10`}
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

/**
 * The same weight-normalised mean the API applies, previewed as you click.
 *
 * Computed from `percent` rather than `weight`: a weighted mean is unaffected by
 * the scale of the weights, percentages are always present in every rubric
 * payload, and deriving it from the field that is guaranteed to exist is what
 * stops a partial payload from turning this into NaN.
 */
function weightedScore(rubric: Rubric, values: Record<string, number>): number | null {
  const entries = rubric.criteria.filter(
    (criterion) => values[criterion.key] !== undefined && Number.isFinite(criterion.percent),
  );
  if (entries.length === 0) return null;
  const total = entries.reduce((sum, criterion) => sum + criterion.percent, 0);
  if (total <= 0) return null;
  const blended =
    entries.reduce((sum, criterion) => sum + criterion.percent * values[criterion.key], 0) / total;
  if (!Number.isFinite(blended)) return null;
  return Math.max(1, Math.min(10, Math.round(blended)));
}

function ScoreContent() {
  const params = useParams<{ id: string }>();
  const submissionId = Number(params.id);

  const [submission, setSubmission] = useState<JudgeSubmission | null>(null);
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [presentation, setPresentation] = useState<Presentation | null>(null);
  const [presentationError, setPresentationError] = useState<string | null>(null);

  const [criteria, setCriteria] = useState<Record<string, number>>({});
  const [fallbackScore, setFallbackScore] = useState<number | null>(null);
  const [technicalComment, setTechnicalComment] = useState("");
  const [presentationScore, setPresentationScore] = useState<number | null>(null);
  const [presentationComment, setPresentationComment] = useState("");

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const data = await api.get<{ submission: JudgeSubmission; rubric: Rubric }>(
      `/judging/submissions/${submissionId}`,
    );
    const found = data.submission;
    setSubmission(found);
    setRubric(data.rubric);
    setCriteria(found.score?.criteria ?? {});
    setFallbackScore(found.score?.technical_score ?? null);
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
    if (!rubric) return;
    const supplied = rubric.criteria.filter((criterion) => criteria[criterion.key] !== undefined);
    const missing = rubric.criteria.filter((criterion) => criteria[criterion.key] === undefined);
    if (missing.length > 0) {
      setError(`Score every criterion first: ${missing.map((c) => c.label).join(", ")}.`);
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.post<{ score: { technical_score: number | null } }>(
        "/judging/scores",
        {
          submission_id: submissionId,
          criteria: supplied.map((criterion) => ({
            key: criterion.key,
            value: criteria[criterion.key],
          })),
          technical_comment: technicalComment || null,
        },
      );
      await load();
      setMessage(
        `Technical verdict recorded — weighted score ${result.score.technical_score}/10. The presentation tier is now unlocked for you.`,
      );
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
  const preview = rubric ? weightedScore(rubric, criteria) : null;
  const tagged = rubric ? rubric.criteria.every((c) => criteria[c.key] !== undefined) : false;

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
            Code quality, architecture and documentation. Scored before any demo is visible, against
            the organiser&apos;s weighted rubric.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={saveTechnical} className="space-y-5">
            {rubric?.criteria.map((criterion) => (
              <div key={criterion.key} className="space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">
                    {criterion.label}
                  </p>
                  <span className="font-mono text-xs text-primary">{criterion.percent}%</span>
                </div>
                <ScorePicker
                  label={criterion.label}
                  value={criteria[criterion.key] ?? null}
                  onChange={(next) => setCriteria((previous) => ({ ...previous, [criterion.key]: next }))}
                  disabled={busy}
                />
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-background/40 px-4 py-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">
                  Weighted technical score
                </p>
                <p className="text-2xl font-semibold tracking-tight">
                  {preview === null ? "—" : `${preview}/10`}
                </p>
              </div>
              <p className="max-w-md text-xs text-muted-foreground">
                {preview === null
                  ? "Score each criterion and the weighted mean appears here. This is the single number the Z-score engine normalizes."
                  : "This is the number the Z-score engine will normalize against your own grading distribution."}
              </p>
            </div>

            <Textarea
              value={technicalComment}
              onChange={(inputEvent) => setTechnicalComment(inputEvent.target.value)}
              placeholder="What is technically strong or weak about this repository?"
            />
            <Button type="submit" disabled={busy || !tagged}>
              {submission.score?.technical_score ? "Update technical verdict" : "Submit technical verdict"}
            </Button>
            {!tagged && (
              <p className="text-xs text-muted-foreground">
                Every criterion is required — {fallbackScore === null ? "nothing filed yet" : `currently ${fallbackScore}/10`}.
              </p>
            )}
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
                  <ScorePicker
                    label="Presentation score"
                    value={presentationScore}
                    onChange={setPresentationScore}
                    disabled={busy}
                  />
                </div>
                <Textarea
                  value={presentationComment}
                  onChange={(inputEvent) => setPresentationComment(inputEvent.target.value)}
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
