"use client";

import { useEffect, useState } from "react";

import { RequireAuth } from "@/components/require-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import type { TeamSummary } from "@/lib/types";

function TeamContent() {
  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const data = await api.get<{ team: TeamSummary | null }>("/teams/me");
    setTeam(data.team);
    setLoading(false);
  }

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setLoading(false);
    });
  }, []);

  async function act(path: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api.post(path, body);
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading team…</p>;

  if (team) {
    return (
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>{team.name}</span>
              <Badge variant="success">{team.members.length} member{team.members.length === 1 ? "" : "s"}</Badge>
            </CardTitle>
            <CardDescription>
              Share the invite code so teammates can join. Every member may edit the submission.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div>
              <Label>Invite code</Label>
              <p className="mt-1 font-mono text-2xl tracking-[0.3em] text-primary">{team.invite_code}</p>
            </div>

            <div>
              <Label>Members</Label>
              <ul className="mt-2 space-y-1 text-sm">
                {team.members.map((member) => (
                  <li key={member.id} className="flex items-center justify-between border-b border-border/60 py-1.5">
                    <span>{member.name || member.email}</span>
                    <span className="font-mono text-xs text-muted-foreground">{member.email}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={() => (window.location.href = "/submit")}>
                {team.submission ? "Edit submission" : "Create submission"}
              </Button>
              {team.submission && (
                <Badge variant={team.submission.integrity_flagged ? "warning" : "success"}>
                  {team.submission.integrity_flagged ? "Commit review pending" : "Submission on file"}
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Create a team</CardTitle>
          <CardDescription>You become the first member. Others join with your invite code.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="team-name">Team name</Label>
            <Input
              id="team-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Compile & Conquer"
            />
          </div>
          <Button disabled={busy || name.trim().length < 2} onClick={() => act("/teams", { name })}>
            Create team
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Join a team</CardTitle>
          <CardDescription>Enter the invite code your captain shared.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="invite">Invite code</Label>
            <Input
              id="invite"
              value={invite}
              onChange={(event) => setInvite(event.target.value.toUpperCase())}
              placeholder="ABC123XY"
              className="font-mono tracking-widest"
            />
          </div>
          <Button
            variant="secondary"
            disabled={busy || invite.trim().length < 4}
            onClick={() => act("/teams/join", { invite_code: invite })}
          >
            Join team
          </Button>
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive md:col-span-2">{error}</p>}
    </div>
  );
}

export default function TeamPage() {
  return (
    <RequireAuth>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Your team</h1>
          <p className="text-sm text-muted-foreground">
            Teams are formed with GitHub or email accounts. One submission per team.
          </p>
        </div>
        <TeamContent />
      </div>
    </RequireAuth>
  );
}
