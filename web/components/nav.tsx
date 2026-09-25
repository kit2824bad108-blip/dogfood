"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

const linkClass =
  "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground";

export function Nav() {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => setMe({ authenticated: false, user: null }));
  }, []);

  async function signOut() {
    try {
      await api.post("/auth/logout");
    } finally {
      window.location.href = "/";
    }
  }

  const user = me?.user ?? null;

  return (
    <header className="sticky top-0 z-40 border-b border-border/70 bg-background/80 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-3">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
            Ax
          </span>
          Axion
        </Link>

        <nav className="flex items-center gap-1">
          {user?.role === "participant" && (
            <>
              <Link href="/team" className={linkClass}>
                Team
              </Link>
              <Link href="/submit" className={linkClass}>
                Submission
              </Link>
            </>
          )}
          {(user?.role === "judge" || user?.role === "admin") && (
            <Link href="/judge" className={linkClass}>
              Judging
            </Link>
          )}
          {user?.role === "admin" && (
            <Link href="/admin" className={linkClass}>
              Console
            </Link>
          )}

          {user ? (
            <div className="ml-2 flex items-center gap-2">
              <Badge variant={user.role === "admin" ? "accent" : user.role === "judge" ? "default" : "secondary"}>
                {user.role}
              </Badge>
              <span className="hidden text-sm text-muted-foreground sm:inline">{user.name}</span>
              <Button size="sm" variant="outline" onClick={signOut}>
                Sign out
              </Button>
            </div>
          ) : (
            <Link href="/" className={linkClass}>
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
