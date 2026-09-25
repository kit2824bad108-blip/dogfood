"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import type { AuthStatus, Me } from "@/lib/types";

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

export default function LandingPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Me>("/auth/me").then(setMe).catch(() => setMe({ authenticated: false, user: null }));
    api.get<AuthStatus>("/auth/status").then(setStatus).catch(() => setStatus(null));
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
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

  const user = me?.user ?? null;

  return (
    <div className="space-y-10">
      <section className="grid-bg overflow-hidden rounded-xl border border-border bg-card/40 p-8">
        <Badge variant="outline" className="mb-4">
          {status?.event_name ?? "Hackathon lifecycle engine"}
        </Badge>
        <h1 className="max-w-3xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
          Hackathon judging is broken. This replaces it with{" "}
          <span className="text-primary">mathematically defensible</span> scoring.
        </h1>
        <p className="mt-4 max-w-2xl text-muted-foreground">
          Opaque averaging rewards the flashiest demo. Axion enforces blind technical evaluation, then
          normalizes each judge against their own grading distribution — a strict grader&apos;s 6 can beat a
          generous grader&apos;s 8. Every score is written to an append-only audit trail.
        </p>

        {status && (
          <p className="mt-5 text-xs text-muted-foreground">
            Commit Integrity source:{" "}
            <span className="font-mono">{status.mock_github ? "mock (offline)" : "live GitHub API"}</span>
          </p>
        )}
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
        </div>

        <div className="space-y-4">
          {user ? (
            <Card>
              <CardHeader>
                <CardTitle>Welcome back, {user.name}</CardTitle>
                <CardDescription>
                  Signed in as <span className="font-mono text-xs">{user.email}</span> ({user.role}).
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {user.role === "participant" && (
                  <>
                    <Button onClick={() => (window.location.href = "/team")}>Set up your team</Button>
                    <Button variant="outline" onClick={() => (window.location.href = "/submit")}>
                      Submit a project
                    </Button>
                  </>
                )}
                {(user.role === "judge" || user.role === "admin") && (
                  <Button onClick={() => (window.location.href = "/judge")}>Open judging</Button>
                )}
                {user.role === "admin" && (
                  <Button variant="secondary" onClick={() => (window.location.href = "/admin")}>
                    Open console
                  </Button>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>{mode === "login" ? "Sign in" : "Create an account"}</CardTitle>
                <CardDescription>
                  Participants can register with email, or use GitHub. Judges and organisers use the
                  credentials issued by the organiser.
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
                        onChange={(event) => setName(event.target.value)}
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
                      onChange={(event) => setEmail(event.target.value)}
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
                      onChange={(event) => setPassword(event.target.value)}
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
                  Demo accounts:{" "}
                  <Link href="/" className="font-mono">
                    admin@axion.dev / axion-admin
                  </Link>{" "}
                  · judges use{" "}
                  <span className="font-mono">disciplined@axion.dev / axion-judge</span>
                </p>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>How the math works</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              <p className="font-mono text-xs text-foreground">z = (raw − judge_mean) / judge_sigma</p>
              <p>
                Each judge&apos;s mean and spread are estimated from their own verdicts, with small samples
                shrunk toward the pool. A judge who only ever grades 4 or 5 is telling you far more with a 6
                than a judge who ranges from 3 to 10.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
