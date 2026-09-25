"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import type { Assignment } from "@/lib/types";

function JudgeContent() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [progress, setProgress] = useState({ total: 0, technical_done: 0, presentation_done: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ assignments: Assignment[]; progress: typeof progress }>("/judging/assignments")
      .then((data) => {
        setAssignments(data.assignments);
        setProgress(data.progress);
      })
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-sm text-muted-foreground">Loading assignments…</p>;
  if (error) return <p className="text-sm text-destructive">{error}</p>;

  const percent = progress.total ? Math.round((progress.technical_done / progress.total) * 100) : 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Blind evaluation progress</CardTitle>
          <CardDescription>
            The presentation tier stays locked — server-side — until your technical verdict is filed. The UI
            blur is cosmetic; the API enforces it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
            <div className="h-full bg-primary transition-all" style={{ width: `${percent}%` }} />
          </div>
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span>
              Technical: <span className="text-foreground">{progress.technical_done}</span> / {progress.total}
            </span>
            <span>
              Presentation: <span className="text-foreground">{progress.presentation_done}</span> /{" "}
              {progress.total}
            </span>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4">
        {assignments.map((assignment) => (
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
