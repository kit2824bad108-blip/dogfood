"use client";

import { Github, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import type { Gallery as GalleryPayload, PublicEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

function useDebounced(value: string, delay = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export default function GalleryPage() {
  const [query, setQuery] = useState("");
  const [track, setTrack] = useState<string | null>(null);
  const [data, setData] = useState<GalleryPayload | null>(null);
  const [event, setEvent] = useState<PublicEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const debounced = useDebounced(query);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (debounced.trim()) params.set("q", debounced.trim());
      if (track) params.set("track", track);
      const suffix = params.toString();
      const payload = await api.get<GalleryPayload>(`/gallery${suffix ? `?${suffix}` : ""}`);
      setData(payload);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [debounced, track]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.get<PublicEvent>("/event").then(setEvent).catch(() => setEvent(null));
  }, []);

  const tracks = useMemo(() => data?.tracks ?? [], [data]);

  return (
    <div className="space-y-8">
      <header className="space-y-3 text-center">
        <Badge variant="outline" className="border-primary/40 bg-primary/5 text-primary">
          {event?.event.name ?? "Public gallery"}
        </Badge>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Every project that made it to <span className="text-gradient">judging</span>
        </h1>
        <p className="mx-auto max-w-2xl text-sm text-muted-foreground">
          Drafts stay private until their team submits them. Demo and video links are deliberately
          withheld here — judges only see those after filing a technical verdict, so this page cannot
          be used to bypass blind evaluation.
        </p>
      </header>

      <div className="space-y-3">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(changeEvent) => setQuery(changeEvent.target.value)}
            placeholder="Search by project, team or summary…"
            aria-label="Search projects"
            className="h-11 pl-9"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setTrack(null)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs transition-colors",
              track === null
                ? "border-primary bg-primary/10 text-foreground"
                : "border-border text-muted-foreground hover:bg-secondary",
            )}
          >
            All tracks
          </button>
          {tracks.map((entry) => (
            <button
              key={entry.slug}
              type="button"
              onClick={() => setTrack(entry.slug)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition-colors",
                track === entry.slug
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:bg-secondary",
              )}
            >
              {entry.name}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {loading ? "Searching…" : `${data?.count ?? 0} project${data?.count === 1 ? "" : "s"}`}
        {data?.query ? ` matching “${data.query}”` : ""}
      </p>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {(data?.projects ?? []).map((project) => (
          <Card key={project.id} className="flex flex-col">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="leading-snug">{project.title}</CardTitle>
                {project.track && (
                  <Badge variant="secondary" className="shrink-0">
                    {project.track.name}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground">{project.team}</p>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col justify-between gap-4">
              <p className="text-sm text-muted-foreground">
                {project.summary || "No summary provided."}
              </p>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <a
                  href={project.repo_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 font-mono text-primary underline-offset-4 hover:underline"
                >
                  <Github className="h-3.5 w-3.5" aria-hidden />
                  repository
                </a>
                {project.docs_url && (
                  <a
                    href={project.docs_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    docs
                  </a>
                )}
                <span className="ml-auto text-muted-foreground">
                  {project.submitted_at
                    ? new Date(project.submitted_at).toLocaleDateString()
                    : "—"}
                </span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {!loading && data?.count === 0 && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Nothing matches that search yet.{" "}
            <button
              type="button"
              className="text-primary underline underline-offset-4"
              onClick={() => {
                setQuery("");
                setTrack(null);
              }}
            >
              Clear the filters
            </button>
            .
          </CardContent>
        </Card>
      )}

      <p className="text-center text-sm text-muted-foreground">
        Competing?{" "}
        <Link href="/submit" className="text-primary underline-offset-4 hover:underline">
          Submit your project
        </Link>
        .
      </p>
    </div>
  );
}
