"use client";

/**
 * The community ballot (T3).
 *
 * Deliberately a four-step machine rather than one big form, because the rules are
 * four separate facts and the page should tell you which one you are standing on:
 *
 *   1. ask for a link          — an address, nothing else
 *   2. prove the address       — the link comes back here and sets the cookie
 *   3. vote                    — one score per project, final
 *   4. read the result         — only after the window closes
 *
 * Two behaviours are worth knowing before reading the code:
 *
 * * **Nothing is tallied on screen while the window is open.** `results_visible`
 *   comes from the server, and when it is false the page says why instead of
 *   showing a partial total. The API refuses the tally as well; this is the same
 *   rule stated twice on purpose, because a UI that showed one would be lying
 *   about the API.
 * * **A vote is final.** The button says so before you press it, the API answers
 *   409 if you try twice, and there is no edit control anywhere — an organiser
 *   strikes a vote, a voter does not withdraw one.
 */
import { AlertTriangle, Check, Link2, Loader2, Vote as VoteIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import type { Ballot, BallotEntry, PublicEvent, VotingWindow } from "@/lib/types";
import { cn } from "@/lib/utils";

type Stage = "loading" | "unregistered" | "awaiting-link" | "unverified" | "ballot" | "closed";

export default function VotePage() {
  const [stage, setStage] = useState<Stage>("loading");
  const [window, setWindow] = useState<VotingWindow | null>(null);
  const [ballot, setBallot] = useState<Ballot | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [query, setQuery] = useState("");

  const loadBallot = useCallback(async () => {
    try {
      const payload = await api.get<Ballot>("/vote/ballot");
      setBallot(payload);
      setWindow(payload.window);
      setStage(payload.window.open ? "ballot" : "closed");
    } catch (caught) {
      const status = (caught as { status?: number }).status;
      if (status === 403) {
        // The token is known but the address is not proven: the link has not been
        // followed yet on this browser.
        setStage("unverified");
        setError(errorMessage(caught));
      } else if (status === 401) {
        setStage("unregistered");
      } else {
        setStage("unregistered");
        setError(errorMessage(caught));
      }
    }
  }, []);

  useEffect(() => {
    // The window is read from `/event` as well as from the ballot, so a visitor who
    // has no ballot yet is still told when voting closes rather than being left to
    // guess from a bare form.
    api
      .get<PublicEvent>("/event")
      .then((payload) => setWindow((current) => current ?? payload.event.voting_window))
      .catch(() => undefined);
    loadBallot();
  }, [loadBallot]);

  async function requestLink(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const payload = await api.post<{ token: string; verify_url: string; delivery: { note: string } }>(
        "/vote/register",
        { email, name },
      );
      setLink(payload.verify_url);
      setStage("awaiting-link");
      setMessage(payload.delivery.note);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function followLink() {
    if (!link) return;
    setBusy(true);
    setError(null);
    try {
      const token = new URLSearchParams(link.split("?")[1] ?? "").get("token") ?? "";
      await api.post("/vote/verify", { token });
      setMessage("Address verified. Your ballot is below.");
      await loadBallot();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function cast(entry: BallotEntry, score: number) {
    setSaving(entry.submission_id);
    setError(null);
    try {
      await api.post("/vote", { submission_id: entry.submission_id, score });
      await loadBallot();
      setMessage(`Recorded for ${entry.title}. A vote is final.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(null);
    }
  }

  const entries = useMemo(() => {
    const rows = ballot?.ballot ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) =>
      [row.title, row.team, row.track?.name ?? ""].join(" ").toLowerCase().includes(needle),
    );
  }, [ballot, query]);

  const progress = ballot?.progress;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="space-y-3 text-center">
        <Badge variant="outline" className="border-primary/40 bg-primary/5 text-primary">
          Community voting
        </Badge>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Anyone with an address gets a <span className="text-gradient">ballot</span>
        </h1>
        <p className="mx-auto max-w-2xl text-sm text-muted-foreground">
          Email-gated, one vote per project, final once cast. Ballots are ordered per voter so no
          project is permanently first, and no tally is published until the window closes.
        </p>
        {window && (
          <p className="text-xs text-muted-foreground">
            Voting {window.phase === "open" ? "closes" : "closed"}{" "}
            <time dateTime={window.closes_at}>{new Date(window.closes_at).toLocaleString()}</time>
            {" · "}
            {window.results_visible ? "results are public" : "results stay hidden until it closes"}
          </p>
        )}
      </header>

      {error && (
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" aria-hidden />
          {error}
        </p>
      )}
      {message && !error && (
        <p role="status" aria-live="polite" className="flex items-start gap-2 text-sm text-muted-foreground">
          <Check className="mt-0.5 h-4 w-4 text-primary" aria-hidden />
          <span>{message}</span>
        </p>
      )}

      {stage === "loading" && (
        <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Checking your ballot…
        </p>
      )}

      {(stage === "unregistered" || stage === "awaiting-link") && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Link2 className="h-4 w-4 text-primary" aria-hidden /> Ask for a ballot link
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Axion has no mail server and must run with the network off, so the link is returned
              here and listed in the organiser console. Wire a mailer to{" "}
              <code className="font-mono text-xs">POST /api/vote/register</code> to send it instead.
            </p>
            <form onSubmit={requestLink} className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="flex-1 space-y-1.5">
                <Label htmlFor="voter-email">Email</Label>
                <Input
                  id="voter-email"
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.org"
                />
              </div>
              <div className="flex-1 space-y-1.5">
                <Label htmlFor="voter-name">Display name (optional)</Label>
                <Input
                  id="voter-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="How you appear on comments"
                />
              </div>
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />}
                Send the link
              </Button>
            </form>

            {stage === "awaiting-link" && link && (
              <div className="space-y-3 rounded-lg border border-border bg-secondary/40 p-4">
                <p className="text-sm">
                  Following the link is what proves the address. It sets a cookie and casts nothing.
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button onClick={followLink} disabled={busy}>
                    Follow the link
                  </Button>
                  <code className="break-all font-mono text-xs text-muted-foreground">{link}</code>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {stage === "unverified" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Follow the link to open your ballot</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-muted-foreground">
            <p>
              Your address is not verified on this browser yet. Ask for a new link and follow it —
              the token is rotated on every request, so an old link stops working.
            </p>
            <Button variant="outline" onClick={() => setStage("unregistered")}>
              Ask for a new link
            </Button>
          </CardContent>
        </Card>
      )}

      {stage === "closed" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">The voting window is closed</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>
              Votes are still recorded from this page up to the deadline, but the window has passed.
              Results are published at{" "}
              <Link href="/results" className="text-primary underline-offset-4 hover:underline">
                /results
              </Link>
              .
            </p>
          </CardContent>
        </Card>
      )}

      {stage === "ballot" && ballot && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p aria-live="polite" className="text-sm text-muted-foreground">
              {progress?.cast ?? 0} of {progress?.votable ?? 0} projects scored
              {progress?.remaining ? ` · ${progress.remaining} left` : " · complete"}
            </p>
            <Link href="/results" className="text-sm text-primary underline-offset-4 hover:underline">
              Results
            </Link>
          </div>

          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={progress?.votable ?? 0}
            aria-valuenow={progress?.cast ?? 0}
            aria-label="Ballot progress"
            className="h-2 w-full overflow-hidden rounded-full bg-secondary"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{
                width: `${progress && progress.votable ? (progress.cast / progress.votable) * 100 : 0}%`,
              }}
            />
          </div>

          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter this ballot by title, team or track"
            aria-label="Filter the ballot"
          />

          <ul className="space-y-3">
            {entries.map((entry) => {
              const voted = entry.my_score !== null;
              return (
                <li key={entry.submission_id}>
                  <Card className={cn("transition-colors", voted && "border-primary/50 bg-primary/5")}>
                    <CardHeader className="gap-1">
                      <div className="flex items-start justify-between gap-3">
                        <CardTitle className="text-base leading-snug">
                          <span className="mr-2 font-mono text-xs text-muted-foreground">
                            #{entry.position}
                          </span>
                          <Link
                            href={`/projects/${entry.submission_id}`}
                            className="underline-offset-4 hover:underline"
                          >
                            {entry.title}
                          </Link>
                        </CardTitle>
                        <div className="flex shrink-0 items-center gap-2">
                          {entry.track && <Badge variant="secondary">{entry.track.name}</Badge>}
                          {voted && (
                            <Badge variant="accent" className="gap-1">
                              <Check className="h-3 w-3" aria-hidden /> {entry.my_score}/5
                            </Badge>
                          )}
                        </div>
                      </div>
                      <p className="text-sm text-muted-foreground">{entry.team}</p>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <p className="text-sm text-muted-foreground">
                        {entry.summary || "No summary provided."}
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        {[1, 2, 3, 4, 5].map((score) => (
                          <Button
                            key={score}
                            size="sm"
                            variant={entry.my_score === score ? "default" : "outline"}
                            disabled={voted || saving === entry.submission_id}
                            aria-label={`Score ${entry.title} ${score} out of 5`}
                            onClick={() => cast(entry, score)}
                          >
                            {saving === entry.submission_id && entry.my_score === null ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                            ) : (
                              score
                            )}
                          </Button>
                        ))}
                        <span className="text-xs text-muted-foreground">
                          {voted ? "Recorded. Final." : "One vote, and it cannot be changed."}
                        </span>
                      </div>
                    </CardContent>
                  </Card>
                </li>
              );
            })}
          </ul>

          {entries.length === 0 && (
            <Card>
              <CardContent className="pt-6 text-sm text-muted-foreground">
                Nothing on your ballot matches that filter.
              </CardContent>
            </Card>
          )}

          <p className="text-center text-xs text-muted-foreground">
            <VoteIcon className="mr-1 inline h-3.5 w-3.5" aria-hidden />
            Ordering: {ballot.ordering.method} — {ballot.ordering.properties.join(", ")}.
          </p>
        </>
      )}
    </div>
  );
}
