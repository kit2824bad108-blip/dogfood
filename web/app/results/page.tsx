"use client";

/**
 * Community results (T3).
 *
 * The page has exactly two states, and the interesting one is the refusal: while
 * the voting window is open the API answers 403 and explains, and this page renders
 * that explanation instead of a table. Showing a partial tally would be the same
 * mistake as publishing a running total to voters — which is to say, it would let
 * the first votes decide the rest.
 *
 * The distribution bar is per project rather than a global chart on purpose: a
 * hackathon vote is a small sample, and an average hides whether a project was
 * loved by three people or tolerated by thirty.
 */
import { AlertTriangle, BarChart3, Loader2, Trophy } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import type { CommunityResults } from "@/lib/types";

export default function ResultsPage() {
  const [data, setData] = useState<CommunityResults | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<CommunityResults>("/vote/results")
      .then((payload) => {
        setData(payload);
        setReason(null);
      })
      .catch((caught) => {
        // The 403 body carries the window and the reason; the error message is the
        // only part the generic helper keeps, so the window is fetched separately.
        setReason(errorMessage(caught));
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="space-y-3 text-center">
        <Badge variant="outline" className="border-primary/40 bg-primary/5 text-primary">
          Community results
        </Badge>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          What the crowd <span className="text-gradient">scored</span>
        </h1>
        <p className="mx-auto max-w-2xl text-sm text-muted-foreground">
          Cast votes only. A struck vote is an organiser&apos;s decision and is reported separately —
          it never enters an average, because a tally that changes depending on who asks is not a
          tally.
        </p>
      </header>

      {loading && (
        <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading…
        </p>
      )}

      {!loading && !data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Not published yet</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-muted-foreground">
            <p className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden />
              <span>
                {reason ??
                  "Community results are hidden until the voting window closes — a running total would let the first votes decide the rest."}
              </span>
            </p>
            <div className="flex flex-wrap gap-3">
              <Link href="/vote" className="text-primary underline-offset-4 hover:underline">
                Cast a ballot
              </Link>
              <Link href="/gallery" className="text-primary underline-offset-4 hover:underline">
                Browse the projects
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {data.totals.votes} vote{data.totals.votes === 1 ? "" : "s"} across{" "}
              {data.totals.projects_with_votes} project
              {data.totals.projects_with_votes === 1 ? "" : "s"}
              {typeof data.totals.votes_struck === "number" &&
                data.totals.votes_struck > 0 &&
                ` · ${data.totals.votes_struck} struck and excluded`}
            </p>
            <p className="text-xs text-muted-foreground">
              {data.visibility.shown_to === "organiser" ? "Organiser view · " : ""}
              {data.visibility.reason}
            </p>
          </div>

          <ol className="space-y-3">
            {data.results.map((row, index) => (
              <li key={row.submission_id}>
                <Card>
                  <CardHeader className="gap-2 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <CardTitle className="text-base">
                        <span className="mr-2 inline-flex items-center gap-1 font-mono text-xs text-muted-foreground">
                          <Trophy className="h-3.5 w-3.5" aria-hidden />
                          {index + 1}
                        </span>
                        <Link
                          href={`/projects/${row.submission_id}`}
                          className="underline-offset-4 hover:underline"
                        >
                          {row.title ?? `Project ${row.submission_id}`}
                        </Link>
                      </CardTitle>
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">{row.votes} votes</Badge>
                        <Badge variant="accent">{row.average ?? "—"} avg</Badge>
                        {typeof row.struck_votes === "number" && row.struck_votes > 0 && (
                          <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10">
                            {row.struck_votes} struck
                          </Badge>
                        )}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 pb-4 pt-0">
                    <div className="flex items-end gap-1" aria-hidden>
                      {[1, 2, 3, 4, 5].map((score) => {
                        const count = row.distribution[String(score)] ?? 0;
                        const width = row.votes ? Math.max(4, (count / row.votes) * 100) : 0;
                        return (
                          <span
                            key={score}
                            title={`${score}: ${count}`}
                            className="h-2 rounded-full bg-primary/70"
                            style={{ width: `${width}%` }}
                          />
                        );
                      })}
                    </div>
                    <p className="flex items-center gap-3 text-xs text-muted-foreground">
                      <BarChart3 className="h-3.5 w-3.5" aria-hidden />
                      {[1, 2, 3, 4, 5]
                        .map((score) => `${score}★ ${row.distribution[String(score)] ?? 0}`)
                        .join(" · ")}
                      {row.lowest !== null && row.highest !== null && (
                        <span className="ml-auto">
                          range {row.lowest}–{row.highest}
                        </span>
                      )}
                    </p>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ol>

          {data.results.length === 0 && (
            <Card>
              <CardContent className="pt-6 text-sm text-muted-foreground">
                The window has closed and no votes were cast.
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
