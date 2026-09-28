"use client";

/**
 * One public project, and the conversation about it (T3).
 *
 * The page is careful about three things, each of which is a rule rather than a
 * layout choice:
 *
 * * **The presentation links are absent, not hidden.** `demo_url` and `video_url`
 *   are the tier the blind gate protects, so the API never sends them here and the
 *   page never has to remember not to render them.
 * * **The community tally appears only after the voting window closes.** While the
 *   window is open the page says so instead of showing a partial average, because a
 *   running total is how a crowd vote becomes a bandwagon.
 * * **A comment needs an identity.** Signed-in users comment as themselves;
 *   everyone else needs a verified ballot address. The API refuses the rest, and
 *   the page explains the refusal rather than showing a dead form.
 */
import { AlertTriangle, ArrowLeft, Check, Github, Loader2, MessageSquare, Send } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { Comment, CommentThread, Me, ProjectDetail } from "@/lib/types";

export default function ProjectPage() {
  // `useParams` rather than the `params` prop: this is a client component, and the
  // route is the same in the dev server and in a production build.
  const params = useParams<{ id: string }>();
  const submissionId = Number(params.id);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [thread, setThread] = useState<CommentThread | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [project, comments] = await Promise.all([
        api.get<ProjectDetail>(`/gallery/${submissionId}`),
        api.get<CommentThread>(`/submissions/${submissionId}/comments`),
      ]);
      setDetail(project);
      setThread(comments);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [submissionId]);

  useEffect(() => {
    load();
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => setMe({ authenticated: false, user: null }));
  }, [load]);

  async function submitComment(event: React.FormEvent) {
    event.preventDefault();
    setPosting(true);
    setError(null);
    setNotice(null);
    try {
      await api.post<{ comment: Comment }>(`/submissions/${submissionId}/comments`, { body });
      setBody("");
      setNotice("Posted.");
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPosting(false);
    }
  }

  if (loading && !detail) {
    return (
      <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading the project…
      </p>
    );
  }

  if (!detail) {
    return (
      <Card className="mx-auto max-w-2xl">
        <CardContent className="space-y-3 pt-6 text-sm">
          <p className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {error ?? "That project is not public."}
          </p>
          <Link href="/gallery" className="text-primary underline-offset-4 hover:underline">
            Back to the gallery
          </Link>
        </CardContent>
      </Card>
    );
  }

  const { project, voting_window: window } = detail;
  const comments = thread?.comments ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <Link
        href="/gallery"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground underline-offset-4 hover:underline"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden /> Gallery
      </Link>

      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {project.track && <Badge variant="secondary">{project.track.name}</Badge>}
          {detail.community && (
            <Badge variant="accent">
              {detail.community.votes} vote{detail.community.votes === 1 ? "" : "s"} ·{" "}
              {detail.community.average ?? "—"} average
            </Badge>
          )}
          {!window.results_visible && (
            <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10">
              community tally hidden until voting closes
            </Badge>
          )}
        </div>
        <h1 className="text-3xl font-semibold tracking-tight">{project.title}</h1>
        <p className="text-sm text-muted-foreground">
          {project.team}
          {project.submitted_at && ` · submitted ${new Date(project.submitted_at).toLocaleString()}`}
        </p>
        <p className="text-sm text-muted-foreground">{project.summary || "No summary provided."}</p>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <a
            href={project.repo_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 font-mono text-primary underline-offset-4 hover:underline"
          >
            <Github className="h-4 w-4" aria-hidden /> repository
          </a>
          {project.docs_url && (
            <a
              href={project.docs_url}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              documentation
            </a>
          )}
          <Link href="/vote" className="text-primary underline-offset-4 hover:underline">
            Vote on this project
          </Link>
        </div>
      </header>

      <section aria-labelledby="comments-heading" className="space-y-4">
        <h2 id="comments-heading" className="flex items-center gap-2 text-lg font-semibold">
          <MessageSquare className="h-4 w-4 text-primary" aria-hidden />
          Comments
          <span className="text-sm font-normal text-muted-foreground">({thread?.count ?? 0})</span>
        </h2>

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

        <form onSubmit={submitComment} className="space-y-3">
          <Textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder={
              me?.authenticated
                ? `Commenting as ${me.user?.name || me.user?.email}`
                : "Commenting as your verified ballot address — ask for a ballot link if you have not."
            }
            aria-label="Your comment"
            maxLength={2000}
          />
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {body.length}/2000 · repeated comments are refused, and everything is audited.
            </p>
            <Button type="submit" disabled={posting || body.trim().length === 0}>
              {posting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Send className="mr-2 h-4 w-4" aria-hidden />
              )}
              Post
            </Button>
          </div>
        </form>

        <ul className="space-y-3">
          {comments.map((comment) => (
            <li key={comment.id}>
              <Card>
                <CardHeader className="gap-1 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle className="text-sm">{comment.author}</CardTitle>
                    <span className="text-xs text-muted-foreground">
                      {comment.created_at ? new Date(comment.created_at).toLocaleString() : ""}
                    </span>
                  </div>
                </CardHeader>
                <CardContent className="pb-4 pt-0">
                  <p className="whitespace-pre-wrap text-sm">{comment.body}</p>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>

        {comments.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No comments yet. The thread is public — the address behind a comment is not.
          </p>
        )}
      </section>
    </div>
  );
}
