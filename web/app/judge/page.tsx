"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import type { Assignment, Rubric } from "@/lib/types";
import { cn } from "@/lib/utils";

type Progress = {
  total: number;
  technical_done: number;
  technical_pending: number;
  presentation_done: number;
  percent_technical: number;
};

type Filter = "all" | "pending" | "graded";

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "graded", label: "Graded" },
];

function Stat({ value, label, hint }: { value: string; label: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-background/40 p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function JudgeContent() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [progress, setProgress] = useState<Progress>({
    total: 0,
    technical_done: 0,
    technical_pending: 0,
    presentation_done: 0,
    percent_technical: 0,
  });
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ assignments: Assignment[]; progress: Progress; rubric: Rubric }>(
        "/judging/assignments",
      )
      .then((data) => {
        setAssignments(data.assignments);
        setProgress(data.progress);
        setRubric(data.rubric);
      })
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, []);

  const visible = useMemo(() => {
    if (filter === "pending") return assignments.filter((row) => !row.technical_submitted);
    if (filter === "graded") return assignments.filter((row) => row.technical_submitted);
    return assignments;
  }, [assignments, filter]);

  if (loading) return <p className="text-sm text-muted-foreground">Loading assignments…</p>;
  if (error) return <p className="text-sm text-destructive">{error}</p>;

  const percent = progress.total
    ? Math.round((progress.technical_done / progress.total) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>Your progress</span>
            <Badge variant={progress.technical_done === progress.total ? "success" : "default"}>
              {progress.technical_done}/{progress.total} graded
            </Badge>
          </CardTitle>
          <CardDescription>
            The presentation tier stays locked — server-side — until your technical verdict is filed. The UI
            blur is cosmetic; the API enforces it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat
              value={`${progress.technical_done}/${progress.total}`}
              label="Technically graded"
              hint={`${percent}% complete`}
            />
            <Stat
              value={String(progress.technical_pending)}
              label="Still to grade"
              hint="Technical tier only"
            />
            <Stat
              value={`${progress.presentation_done}/${progress.total}`}
              label="Presentation graded"
              hint="Unlocked per project"
            />
          </div>

          <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
            <div className="h-full bg-primary transition-all" style={{ width: `${percent}%` }} />
          </div>

          {rubric && rubric.criteria.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Scoring against <span className="text-foreground">{rubric.name}</span>:{" "}
              {rubric.criteria
                .map((criterion) => `${criterion.label} ${criterion.percent}%`)
                .join(" · ")}
              . Your technical score is the weighted mean of those criteria.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setFilter(entry.id)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs transition-colors",
              filter === entry.id
                ? "border-primary bg-primary/10 text-foreground"
                : "border-border text-muted-foreground hover:bg-secondary",
            )}
          >
            {entry.label}
            {entry.id === "pending" && progress.technical_pending > 0
              ? ` (${progress.technical_pending})`
              : ""}
          </button>
        ))}
      </div>

      <div className="grid gap-4">
        {visible.map((assignment) => (
          <Card key={assignment.submission_id}>
            <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-5">
              <div className="min-w-0 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{assignment.title}</span>
                  {assignment.commit_integrity_flagged && (
                    <Badge variant="warning">commit review</Badge>
                  )}
                </div>
                <p className="text-sm text-muted-foreground">{assignment.team_name}</p>
                <a
                  href={assignment.repo_url}
                  target="_blank"
                  rel="noreferrer"
                  className="block truncate font-mono text-xs text-primary underline-offset-4 hover:underline"
                >
                  {assignment.repo_url}
                </a>
              </div>

              <div className="flex items-center gap-2">
                <Badge variant={assignment.technical_submitted ? "success" : "secondary"}>
                  {assignment.technical_submitted
                    ? `technical ${assignment.technical_score}/10`
                    : "technical pending"}
                </Badge>
                <Badge variant={assignment.presentation_submitted ? "success" : "secondary"}>
                  {assignment.presentation_submitted
                    ? `presentation ${assignment.presentation_score}/10`
                    : "presentation locked"}
                </Badge>
                <Link href={`/judge/score/${assignment.submission_id}`}>
                  <Button size="sm">{assignment.technical_submitted ? "Review" : "Evaluate"}</Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        ))}

        {assignments.length === 0 && (
          <Card>
            <CardContent className="pt-6 text-sm text-muted-foreground">
              No assignments yet. An organiser needs to create submissions before judges are assigned.
            </CardContent>
          </Card>
        )}

        {assignments.length > 0 && visible.length === 0 && (
          <Card>
            <CardContent className="pt-6 text-sm text-muted-foreground">
              Nothing in this filter.
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

export default function JudgePage() {
  return (
    <RequireAuth roles={["judge", "admin"]}>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Judging</h1>
          <p className="text-sm text-muted-foreground">
            Judge the code and documentation first. The demo comes after.
          </p>
        </div>
        <JudgeContent />
      </div>
    </RequireAuth>
  );
}
