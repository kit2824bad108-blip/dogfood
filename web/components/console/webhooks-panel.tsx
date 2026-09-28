"use client";

/**
 * The webhook console (T4).
 *
 * Three things the panel refuses to hide, because each one is a decision a
 * subscriber has to be able to see:
 *
 * * **The queue is on screen at all times.** Nothing here is delivered by a
 *   background worker — there is no worker — so "pending: 4" is not a statistic,
 *   it is a to-do. `Dispatch now` is the only thing in the product that sends a
 *   webhook.
 * * **The secret is readable.** It is returned once at creation and on demand
 *   afterwards, because a secret nobody can read is a secret nobody can configure
 *   a receiver with. Rotating it is a repair action and says so.
 * * **Deleting deactivates.** The delivery history of a failing endpoint is the
 *   evidence of the failure, and it is kept unless the organiser explicitly purges.
 */
import { AlertTriangle, Check, Loader2, Play, Plus, RefreshCw, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import type { WebhookConsole, WebhookDelivery, WebhookEndpoint } from "@/lib/types";
import { cn } from "@/lib/utils";

const statusTone: Record<string, string> = {
  delivered: "border-transparent bg-primary/15 text-primary",
  pending: "border-transparent bg-secondary text-foreground",
  dead: "border-transparent bg-destructive/15 text-destructive",
  failed: "border-transparent bg-amber-500/15 text-amber-600",
};

export function WebhooksPanel() {
  const [console_, setConsole] = useState<WebhookConsole | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [form, setForm] = useState({ url: "", description: "", events: [] as string[], all: true });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [endpoints, recent] = await Promise.all([
        api.get<WebhookConsole>("/admin/webhooks"),
        api.get<{ deliveries: WebhookDelivery[] }>("/admin/webhooks/deliveries?limit=25"),
      ]);
      setConsole(endpoints);
      setDeliveries(recent.deliveries);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /**
   * Run one console action. The work function may return its own message, because
   * several of these have a result worth quoting ("attempted 3 deliveries") rather
   * than the standing description of what the button does.
   */
  async function act(work: () => Promise<string | void>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const message = await work();
      setNotice(typeof message === "string" ? message : done);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const catalogue = console_?.catalogue ?? [];

  return (
    <div className="space-y-6">
      {error && (
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" aria-hidden /> {error}
        </p>
      )}
      {notice && (
        <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Check className="h-4 w-4 text-primary" aria-hidden /> {notice}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Delivery queue</CardTitle>
          <CardDescription>
            {console_
              ? `${console_.queue.pending} pending · ${console_.queue.delivered} delivered · ${console_.queue.dead} dead. ${console_.delivery.worker}`
              : "Loading…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            disabled={busy}
            onClick={() =>
              act(async () => {
                const result = await api.post<{ attempted: number }>("/admin/webhooks/dispatch");
                return result.attempted
                  ? `Attempted ${result.attempted} deliveries.`
                  : "Nothing was due — the queue is empty or backing off.";
              }, "Flushed the queue.")
            }
          >
            <Play className="mr-2 h-4 w-4" aria-hidden /> Dispatch now
          </Button>
          <Badge variant="outline">retries {console_?.delivery.attempts ?? "—"}</Badge>
          <Badge variant="outline">
            backoff {(console_?.delivery.backoff_seconds ?? []).join(", ")}s
          </Badge>
          <Badge variant="outline">timeout {console_?.delivery.timeout_seconds ?? "—"}s</Badge>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Register a receiver</CardTitle>
          <CardDescription>
            Deliveries are signed with the endpoint&apos;s own secret:{" "}
            {console_?.signature.scheme ?? "sha256=<hex> over '<timestamp>.<body>'"}. Reject anything
            older than {console_?.signature.tolerance_seconds ?? 300}s.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="webhook-url">URL</Label>
              <Input
                id="webhook-url"
                value={form.url}
                onChange={(event) => setForm({ ...form, url: event.target.value })}
                placeholder="https://example.org/axion"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="webhook-description">Description</Label>
              <Input
                id="webhook-description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                placeholder="Results Slack bot"
              />
            </div>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Events</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.all}
                onChange={(event) => setForm({ ...form, all: event.target.checked })}
              />
              Everything the catalogue defines
            </label>
            {!form.all && (
              <div className="grid gap-1.5 sm:grid-cols-2">
                {catalogue.map((entry) => (
                  <label key={entry.event} className="flex items-start gap-2 text-sm" title={entry.description}>
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={form.events.includes(entry.event)}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          events: event.target.checked
                            ? [...form.events, entry.event]
                            : form.events.filter((name) => name !== entry.event),
                        })
                      }
                    />
                    <span className="font-mono text-xs">{entry.event}</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>

          <Button
            size="sm"
            disabled={busy || !form.url}
            onClick={() =>
              act(async () => {
                const payload = await api.post<{ endpoint: WebhookEndpoint }>("/admin/webhooks", {
                  url: form.url,
                  description: form.description || null,
                  events: form.all ? null : form.events,
                });
                setForm({ url: "", description: "", events: [], all: true });
                setRevealed(payload.endpoint.id);
              }, "Endpoint registered. Copy the secret into your receiver now.")
            }
          >
            <Plus className="mr-2 h-4 w-4" aria-hidden /> Register
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Endpoints</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {(console_?.endpoints ?? []).length === 0 && (
            <p className="text-sm text-muted-foreground">
              No receivers yet. Nothing is emitted until one is registered — a deployment with no
              subscribers simply records nothing.
            </p>
          )}
          {(console_?.endpoints ?? []).map((endpoint) => (
            <div key={endpoint.id} className="space-y-2 rounded-lg border border-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm">{endpoint.url}</p>
                  <p className="text-xs text-muted-foreground">
                    {endpoint.description || "no description"} ·{" "}
                    {Array.isArray(endpoint.events) ? endpoint.events.join(", ") : "every event"}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={endpoint.active ? "default" : "outline"}>
                    {endpoint.active ? "active" : "paused"}
                  </Badge>
                  {endpoint.failure_count > 0 && (
                    <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10">
                      {endpoint.failure_count} failures
                    </Badge>
                  )}
                </div>
              </div>

              {revealed === endpoint.id && endpoint.secret && (
                <p className="break-all rounded bg-secondary/60 p-2 font-mono text-xs">
                  {endpoint.secret}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setRevealed(revealed === endpoint.id ? null : endpoint.id)}
                >
                  {revealed === endpoint.id ? "Hide secret" : "Show secret"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api.post(`/admin/webhooks/${endpoint.id}/test`);
                    }, "Queued a webhook.test delivery. Dispatch to send it.")
                  }
                >
                  <Send className="mr-2 h-4 w-4" aria-hidden /> Test
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api.patch(`/admin/webhooks/${endpoint.id}`, {
                        active: !endpoint.active,
                      });
                    }, endpoint.active ? "Paused." : "Resumed.")
                  }
                >
                  {endpoint.active ? "Pause" : "Resume"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api.patch(`/admin/webhooks/${endpoint.id}`, { rotate_secret: true });
                      setRevealed(endpoint.id);
                    }, "Secret rotated. Update the receiver in the same breath.")
                  }
                >
                  <RefreshCw className="mr-2 h-4 w-4" aria-hidden /> Rotate
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api.del(`/admin/webhooks/${endpoint.id}`);
                    }, "Deactivated. Its delivery history is kept.")
                  }
                >
                  <Trash2 className="mr-2 h-4 w-4" aria-hidden /> Deactivate
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent deliveries</CardTitle>
          <CardDescription>
            Redelivery keeps the id and the signed bytes, so a receiver that dedupes on{" "}
            {console_?.signature.delivery_header ?? "X-Axion-Delivery"} will ignore the copy it already
            processed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {deliveries.length === 0 && (
            <p className="text-sm text-muted-foreground">Nothing has been emitted yet.</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3">Event</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Attempts</th>
                  <th className="py-2 pr-3">HTTP</th>
                  <th className="py-2 pr-3">When</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {deliveries.map((delivery) => (
                  <tr key={delivery.id} className="border-t border-border/60">
                    <td className="py-2 pr-3 font-mono text-xs">{delivery.event}</td>
                    <td className="py-2 pr-3">
                      <Badge
                        variant="outline"
                        className={cn("capitalize", statusTone[delivery.status] ?? "")}
                      >
                        {delivery.status}
                      </Badge>
                    </td>
                    <td className="py-2 pr-3">{delivery.attempts}</td>
                    <td className="py-2 pr-3">{delivery.response_status ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {delivery.created_at ? new Date(delivery.created_at).toLocaleTimeString() : "—"}
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            await api.post(`/admin/webhooks/deliveries/${delivery.id}/redeliver`);
                          }, "Requeued.")
                        }
                      >
                        Redeliver
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {busy && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> working…
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
