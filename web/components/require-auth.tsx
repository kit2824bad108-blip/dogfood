"use client";

import { useEffect, useState } from "react";

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
    return (
      <Card>
        <CardContent className="pt-6">
          <CardTitle>Not authorized</CardTitle>
          <CardDescription className="mt-2">
            This page requires the {roles.join(" or ")} role. You are signed in as {me.user.role}.
          </CardDescription>
        </CardContent>
      </Card>
    );
  }

  return <>{children}</>;
}
