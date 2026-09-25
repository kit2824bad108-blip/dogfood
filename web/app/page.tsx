"use client";

import { Braces, Container, Database, ShieldCheck, WifiOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import type { AuthStatus, Me, PublicEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const PILLARS = [
  {
    title: "Trustless intake",
    body: "GitHub-native teams and submissions, plus a Commit Integrity check that flags repositories whose history predates the event — as a signal for organisers, never an automatic disqualification.",
  },
  {
    title: "The Axion Core",
    body: "Blind technical evaluation enforced in the API, and Z-score normalization so a strict grader's 6 can outrank a generous grader's 8.",
  },
  {
    title: "The ephemeral archive",
    body: "One click produces a self-contained JSON + Markdown results bundle for GitHub Pages, so the database can be spun down after judging.",
  },
];

const STACK = [
  { icon: Braces, label: "REST API + OpenAPI" },
  { icon: Database, label: "Postgres 16" },
  { icon: Container, label: "One-command Compose" },
  { icon: WifiOff, label: "Offline judging" },
];

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="text-center">
      <p className="text-3xl font-semibold tracking-tight sm:text-4xl">{value}</p>
      <p className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
    </div>
  );
}

export default function LandingPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [event, setEvent] = useState<PublicEvent | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Me>("/auth/me").then(setMe).catch(() => setMe({ authenticated: false, user: null }));
    api.get<AuthStatus>("/auth/status").then(setStatus).catch(() => setStatus(null));
    api.get<PublicEvent>("/event").then(setEvent).catch(() => setEvent(null));
  }, []);

  async function submit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") {
        await api.post("/auth/register", { email, password, name });
      } else {
        await api.post("/auth/login", { email, password });
      }
      window.location.href = "/";
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function devLogin(role: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/dev-login", { role });
      window.location.href = role === "participant" ? "/team" : role === "judge" ? "/judge" : "/admin";
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const user = me?.user ?? null;
  const stats = event?.stats;
  const phase = event?.event.phase ?? "open";
  const closed = phase === "closed";

  return (
    <div className="space-y-14">
      <section className="hero-glow grid-bg overflow-hidden rounded-2xl border border-border px-6 py-16 text-center sm:px-10">
        <div className="mx-auto max-w-3xl space-y-6">
          <Badge variant="outline" className="border-primary/40 bg-primary/5 text-primary">
            <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-primary align-middle" />
            {closed
              ? "Judging in progress — submissions are closed"
              : `${stats?.submissions ?? 0} projects entered and ${
                  stats?.verdicts ?? 0
                } verdicts filed`}
          </Badge>

          <h1 className="text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">
            Hackathon judging is broken.
            <span className="text-gradient mt-1 block">Axion makes it provable.</span>
          </h1>

          <p className="mx-auto max-w-2xl text-base text-muted-foreground sm:text-lg">
            Opaque averaging rewards the flashiest demo. Axion enforces blind technical evaluation,
            then normalizes every judge against their own grading distribution — so the best code
            wins, not the best pitch. Every score lands in an append-only audit trail.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3 pt-1">
            {user ? (
              <>
                {user.role === "participant" && (
                  <Button size="lg" onClick={() => (window.location.href = "/team")}>
                    Set up your team
                  </Button>
                )}
                {(user.role === "judge" || user.role === "admin") && (
                  <Button size="lg" onClick={() => (window.location.href = "/judge")}>
                    Open judging ({stats?.verdicts ?? 0} verdicts)
                  </Button>
                )}
                {user.role === "admin" && (
                  <Button
                    size="lg"
                    variant="outline"
                    onClick={() => (window.location.href = "/admin")}
                  >
                    Open console
                  </Button>
                )}
              </>
            ) : (
              <>
                <Button
                  size="lg"
                  onClick={() =>
                    document.getElementById("sign-in")?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  Get started
                </Button>
                <Link href="/gallery">
                  <Button size="lg" variant="outline">
                    Browse the gallery
                  </Button>
                </Link>
              </>
            )}
          </div>
        </div>

        <div className="mx-auto mt-14 grid max-w-3xl grid-cols-2 gap-8 border-t border-border/70 pt-8 sm:grid-cols-4">
          <Stat value={String(stats?.submissions ?? "—")} label="Projects" />
          <Stat value={String(stats?.judges ?? "—")} label="Judges" />
          <Stat value={String(stats?.verdicts ?? "—")} label="Verdicts" />
          <Stat
            value={
              stats ? `${Math.round((stats.verdicts / Math.max(stats.judges, 1)) * 10) / 10}` : "—"
            }
            label="Per judge"
          />
        </div>
      </section>

      <section className="flex flex-wrap items-center justify-center gap-2">
        {STACK.map((item) => {
          const Icon = item.icon;
          return (
            <span
              key={item.label}
              className="flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-1.5 text-xs text-muted-foreground"
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {item.label}
            </span>
          );
        })}
      </section>

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <div className="space-y-4">
          {PILLARS.map((pillar, index) => (
            <Card key={pillar.title}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span className="text-primary">{index + 1}.</span> {pillar.title}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription>{pillar.body}</CardDescription>
              </CardContent>
            </Card>
          ))}

          {event && event.tracks.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Tracks and prizes</CardTitle>
                <CardDescription>
                  Configured by the organiser, not hard-coded. Each submission declares its track.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {event.tracks.map((track) => (
                  <div key={track.id} className="flex items-start justify-between gap-4 text-sm">
                    <div>
                      <p className="font-medium">{track.name}</p>
                      <p className="text-xs text-muted-foreground">{track.description}</p>
                    </div>
                    <div className="shrink-0 text-right">
                      {track.prize_pool && (
                        <p className="font-mono text-xs text-primary">{track.prize_pool}</p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {track.prizes.length} prize{track.prizes.length === 1 ? "" : "s"}
                      </p>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <div id="sign-in" className="scroll-mt-24">
            {user ? (
              <Card>
                <CardHeader>
                  <CardTitle>Welcome back, {user.name}</CardTitle>
                  <CardDescription>
                    Signed in as <span className="font-mono text-xs">{user.email}</span> (
                    {user.role}).
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  <Link href="/gallery">
                    <Button variant="outline">Public gallery</Button>
                  </Link>
                  <a href="/api/docs" target="_blank" rel="noreferrer">
                    <Button variant="ghost">API reference</Button>
                  </a>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle>{mode === "login" ? "Sign in" : "Create an account"}</CardTitle>
                  <CardDescription>
                    Participants can register with email, or use GitHub. Judges and organisers use
                    the credentials issued by the organiser.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <form onSubmit={submit} className="space-y-3">
                    {mode === "register" && (
                      <div className="space-y-1.5">
                        <Label htmlFor="name">Name</Label>
                        <Input
                          id="name"
                          value={name}
                          onChange={(inputEvent) => setName(inputEvent.target.value)}
                          placeholder="Ada Lovelace"
                          autoComplete="name"
                        />
                      </div>
                    )}
                    <div className="space-y-1.5">
                      <Label htmlFor="email">Email</Label>
                      <Input
                        id="email"
                        type="email"
                        required
                        value={email}
                        onChange={(inputEvent) => setEmail(inputEvent.target.value)}
                        placeholder="you@team.dev"
                        autoComplete="email"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="password">Password</Label>
                      <Input
                        id="password"
                        type="password"
                        required
                        minLength={8}
                        value={password}
                        onChange={(inputEvent) => setPassword(inputEvent.target.value)}
                        placeholder="At least 8 characters"
                        autoComplete={mode === "login" ? "current-password" : "new-password"}
                      />
                    </div>
                    {error && <p className="text-sm text-destructive">{error}</p>}
                    <Button type="submit" disabled={busy} className="w-full">
                      {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
                    </Button>
                  </form>

                  {status?.github_oauth_enabled && (
                    <a href="/api/auth/github/login" className="block">
                      <Button variant="outline" className="w-full">
                        Continue with GitHub
                      </Button>
                    </a>
                  )}

                  {status?.local_dev_login && (
                    <div className="space-y-2 rounded-md border border-dashed border-primary/40 bg-primary/5 p-3">
                      <p className="flex items-center gap-1.5 text-xs font-medium text-primary">
                        <WifiOff className="h-3.5 w-3.5" aria-hidden />
                        Offline mode — local dev login
                      </p>
                      <p className="text-xs text-muted-foreground">
                        No network calls, no OAuth. Seeded accounts also work in the form above with
                        the password <span className="font-mono">password</span>.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() => devLogin("admin")}
                        >
                          Login as Admin
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() => devLogin("judge")}
                        >
                          Login as Judge
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() => devLogin("participant")}
                        >
                          Login as Hacker
                        </Button>
                      </div>
                      {status.demo_accounts.length > 0 && (
                        <ul className="space-y-0.5 pt-1 font-mono text-[11px] text-muted-foreground">
                          {status.demo_accounts.map((account) => (
                            <li key={account.role}>
                              {account.role}: {account.email}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>{mode === "login" ? "New here?" : "Already registered?"}</span>
                    <button
                      type="button"
                      className="underline underline-offset-4 hover:text-foreground"
                      onClick={() => {
                        setMode(mode === "login" ? "register" : "login");
                        setError(null);
                      }}
                    >
                      {mode === "login" ? "Create a participant account" : "Sign in instead"}
                    </button>
                  </div>

                  <p className="text-xs text-muted-foreground">
                    Demo organiser: <span className="font-mono">admin@axion.dev / axion-admin</span>
                    <br />
                    Demo judges: <span className="font-mono">disciplined@axion.dev / axion-judge</span>
                  </p>
                </CardContent>
              </Card>
            )}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-primary" aria-hidden />
                How the math works
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              <p className="font-mono text-xs text-foreground">z = (raw − judge_mean) / judge_sigma</p>
              <p>
                Each judge&apos;s mean and spread are estimated from their own verdicts, with small
                samples shrunk toward the pool. A judge who only ever grades 4 or 5 is telling you far
                more with a 6 than a judge who ranges from 3 to 10.
              </p>
              {event && (
                <p className="pt-1 text-xs">
                  Weighted rubric:{" "}
                  {event.rubric.criteria
                    .map((criterion) => `${criterion.label} ${criterion.percent}%`)
                    .join(" · ")}
                </p>
              )}
              <button
                type="button"
                onClick={() => (window.location.href = "/gallery")}
                className={cn(
                  "pt-1 text-xs text-primary underline underline-offset-4 hover:text-foreground",
                )}
              >
                See the projects in the public gallery →
              </button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
