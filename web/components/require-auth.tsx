"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Me, Role } from "@/lib/types";

export function RequireAuth({
  children,
  roles,
}: {
  children: React.ReactNode;
  roles?: Role[];
}) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => setMe({ authenticated: false, user: null }));
  }, []);

  useEffect(() => {
    if (me !== undefined && !me?.authenticated) {
      window.location.href = "/";
    }
  }, [me]);

  if (me === undefined) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (!me?.authenticated || !me.user) {
    return <p className="text-sm text-muted-foreground">Redirecting to sign in…</p>;
  }

  if (roles && !roles.includes(me.user.role)) {
    // A wall with no door: the page said "not authorized" and left the user to
    // find their own way back, which they had to do with the browser chrome.
    return (
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div>
            <CardTitle>Not authorized</CardTitle>
            <CardDescription className="mt-2">
              This page requires the {roles.join(" or ")} role. You are signed in as {me.user.role}.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/gallery" className={buttonVariants({ size: "sm" })}>
              Browse the gallery
            </Link>
            <Link href="/" className={buttonVariants({ size: "sm", variant: "outline" })}>
              Back to the event page
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  }

  return <>{children}</>;
}
