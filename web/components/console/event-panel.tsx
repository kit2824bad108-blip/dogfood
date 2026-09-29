"use client";

/**
 * The organiser's clock: the event's own dates, editable while it is running.
 *
 * The panel is shaped by three rules, and each one is visible in the interface
 * rather than buried in a doc:
 *
 * * **The default is not overwritten.** The effective window is shown beside the
 *   one the deployment was configured with, so "hand it back" is a promise the
 *   organiser can read before pressing it. Until they press save, this
 *   deployment is still the one `EVENT__*`/the imported dataset describes.
 * * **A change is previewed.** Every response carries the consequences in
 *   sentences — whether submissions are open before and after, whether community
 *   results and the signed-record key become public, how many projects already sit
 *   after the new deadline — and the panel shows them. An organiser is entitled to
 *   close the event; they are not entitled to be surprised by it.
 * * **A change is attributed.** It carries a revision, so two organisers editing
 *   at once get a conflict instead of the last save winning, and it is written to
 *   the append-only audit trail, read back in the history below the form.
 *
 * Times are `<input type="datetime-local">` and are converted through the
 * browser's own timezone handling (`new Date(...)` → `toISOString()`), so what an
 * organiser sees is their local clock and what the API receives is UTC. The
 * server reads a naive value as UTC rather than as local time, which is the
 * failure mode that matters for a `curl` user who sends no offset at all.
 */
import { AlertTriangle, Clock, History, Loader2, RotateCcw, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { WINDOW_MOVED_EVENT } from "@/lib/utils";
import type { EventClock, EventHistoryEntry, EventSettings, EventStatus } from "@/lib/types";

type Draft = {
  name: string;
  starts_at: string;
  ends_at: string;
  voting_opens_at: string;
  voting_closes_at: string;
  note: string;
};

/** An ISO instant as the local wall-clock string a datetime-local input wants. */
function toLocalInput(value: string): string {
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return (
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}` +
    `T${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  );
}

function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const moment = new Date(value);
  return Number.isNaN(moment.getTime()) ? null : moment.toISOString();
}

function draftOf(clock: EventClock): Draft {
  return {
    name: clock.name,
    starts_at: toLocalInput(clock.starts_at),
    ends_at: toLocalInput(clock.ends_at),
    voting_opens_at: toLocalInput(clock.voting_opens_at),
    voting_closes_at: toLocalInput(clock.voting_closes_at),
    note: clock.note ?? "",
  };
}

function stamp(value: string | null): string {
  if (!value) return "not recorded";
  return new Date(value).toLocaleString();
}

type BadgeTone = "success" | "secondary" | "warning";

function statusBadge(status: EventStatus): { label: string; tone: BadgeTone } {
  if (status.submissions_closed) return { label: "Submissions closed", tone: "secondary" };
  if (status.submissions_upcoming) return { label: "Submissions scheduled", tone: "warning" };
  return { label: "Submissions open", tone: "success" };
}

export function EventPanel({ onSaved }: { onSaved?: () => void | Promise<void> }) {
  const [data, setData] = useState<EventSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [history, setHistory] = useState<EventHistoryEntry[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const payload = await api.get<EventSettings>("/admin/event");
      setData(payload);
      setDraft(draftOf(payload.event));
      const log = await api.get<{ entries: EventHistoryEntry[] }>("/admin/event/history?limit=20");
      setHistory(log.entries);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    if (!draft || !data) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setWarnings([]);
    try {
      const result = await api.patch<{
        event: EventClock;
        changed: string[];
        warnings: string[];
        notice: string;
      }>("/admin/event", {
        name: draft.name,
        starts_at: fromLocalInput(draft.starts_at),
        ends_at: fromLocalInput(draft.ends_at),
        voting_opens_at: fromLocalInput(draft.voting_opens_at),
        voting_closes_at: fromLocalInput(draft.voting_closes_at),
        note: draft.note.trim() ? draft.note.trim() : null,
        // The revision this form was rendered from. If somebody else saved in the
        // meantime this is a 409 rather than a silent overwrite of their edit.
        expected_revision: data.event.revision,
      });
      setWarnings(result.warnings);
      setNotice(
        result.changed.length
          ? `Saved: ${result.changed.join(", ")}. Recorded in the audit trail.`
          : "Saved. Nothing actually differed from the window already in force.",
      );
      announceMove();
      await load();
      await onSaved?.();
    } catch (caught) {
      setError(errorMessage(caught));
      // A conflict means the form is stale, not that the input was wrong: reload
      // so the organiser is looking at what is actually in force.
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    setNotice(null);
    setWarnings([]);
    try {
      const result = await api.del<{ notice: string; changed: string[] }>("/admin/event");
      setNotice(result.notice);
      announceMove();
      await load();
      await onSaved?.();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!data || !draft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-4 w-4" /> Event window
          </CardTitle>
          <CardDescription>{error ?? "Loading the event clock…"}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const badge = statusBadge(data.status);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span className="flex items-center gap-2">
              <Clock className="h-4 w-4" /> Event window
            </span>
            <span className="flex flex-wrap items-center gap-2">
              <Badge variant={badge.tone}>{badge.label}</Badge>
              <Badge variant={data.overridden ? "warning" : "secondary"}>
                {data.overridden
                  ? `set by an organiser · revision ${data.event.revision}`
                  : "from the deployment's configuration"}
              </Badge>
            </span>
          </CardTitle>
          <CardDescription>
            The submission deadline and the community ballot window are separate clocks. Every
            endpoint resolves both from here, so moving a date moves it for the submit form, the
            gallery, the ballot and every certificate at once.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Submissions open" value={stamp(data.event.starts_at)} />
            <Fact label="Submissions close" value={stamp(data.event.ends_at)} />
            <Fact label="Voting opens" value={stamp(data.event.voting_opens_at)} />
            <Fact label="Voting closes" value={stamp(data.event.voting_closes_at)} />
            <Fact
              label="Community results"
              value={data.status.results_visible ? "public" : "hidden until voting closes"}
            />
            <Fact
              label="Record signing key"
              value={data.status.record_key_published ? "published" : "held until the event closes"}
            />
            <Fact label="Last changed by" value={data.event.updated_by ?? "nobody yet"} />
            <Fact label="Last changed at" value={stamp(data.event.updated_at)} />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="event-name">Event name</Label>
              <Input
                id="event-name"
                value={draft.name}
                maxLength={data.validation.name_max}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="event-note">Why it changed (recorded in the audit trail)</Label>
              <Textarea
                id="event-note"
                value={draft.note}
                maxLength={data.validation.note_max}
                rows={2}
                placeholder="Extended by 24 hours: the venue flooded."
                onChange={(event) => setDraft({ ...draft, note: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="event-start">Submissions open (local time)</Label>
              <Input
                id="event-start"
                type="datetime-local"
                value={draft.starts_at}
                onChange={(event) => setDraft({ ...draft, starts_at: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="event-end">Submissions close (local time)</Label>
              <Input
                id="event-end"
                type="datetime-local"
                value={draft.ends_at}
                onChange={(event) => setDraft({ ...draft, ends_at: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="voting-open">Community voting opens (local time)</Label>
              <Input
                id="voting-open"
                type="datetime-local"
                value={draft.voting_opens_at}
                onChange={(event) => setDraft({ ...draft, voting_opens_at: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="voting-close">Community voting closes (local time)</Label>
              <Input
                id="voting-close"
                type="datetime-local"
                value={draft.voting_closes_at}
                onChange={(event) => setDraft({ ...draft, voting_closes_at: event.target.value })}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={save} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save the window
            </Button>
            <Button variant="outline" onClick={reset} disabled={busy || !data.can_reset}>
              <RotateCcw className="h-4 w-4" />
              Hand it back to the configuration
            </Button>
            <Button variant="ghost" onClick={load} disabled={busy}>
              Reload
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">
            The configured default for this deployment is {stamp(data.deployment_default.starts_at)} →{" "}
            {stamp(data.deployment_default.ends_at)}. Times are {data.validation.timezone}; your
            browser shows them in its own zone. {data.validation.rules.join(" ")}
          </p>

          {notice && <p className="text-sm text-success">{notice}</p>}
          {error && <p className="text-sm text-destructive">{error}</p>}
          {warnings.length > 0 && (
            <ul className="space-y-1 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
              {warnings.map((warning) => (
                <li key={warning} className="flex gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <History className="h-4 w-4" /> Changes to the clock
          </CardTitle>
          <CardDescription>
            Read from the append-only audit trail — the console keeps no second record that could
            disagree with it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This deployment has never taken the clock over: the window is the one it was
              configured with.
            </p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {history.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                  <span>
                    <span className="font-medium">
                      {entry.action === "event.settings_reset" ? "Handed back" : "Moved"}
                    </span>{" "}
                    <span className="text-muted-foreground">
                      {entry.changed.length ? entry.changed.join(", ") : "nothing"}
                    </span>
                    {entry.note && <span className="text-muted-foreground"> — “{entry.note}”</span>}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {entry.actor_email ?? "unknown actor"} · {stamp(entry.at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Tell the rest of the page that the deadline moved.
 *
 * The header fetches the window once, on mount, because it is not this panel's
 * child; without this it would keep showing the old deadline directly above a form
 * that has just been saved. Announcing the change is smaller and more honest than
 * reloading the page out from under the organiser.
 */
function announceMove() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(WINDOW_MOVED_EVENT));
  }
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background/40 p-3">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  );
}
