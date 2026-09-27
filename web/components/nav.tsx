"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Deadline } from "@/components/deadline";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { EventWindow, Me, PublicEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const linkClass =
  "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground";

export function Nav() {
  const [me, setMe] = useState<Me | null>(null);
  const [eventWindow, setEventWindow] = useState<EventWindow | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => setMe({ authenticated: false, user: null }));
    // The deadline is the one number every role needs, so it lives in the header.
    api
      .get<PublicEvent>("/event")
      .then((payload) => setEventWindow(payload.event.submission_window))
      .catch(() => setEventWindow(null));
  }, []);

  // `/judge/score/12` belongs to Judging, so matching the prefix keeps the nav
  // honest one level down from the section it is named after.
  function isActive(href: string) {
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  function goToSignIn() {
    const anchor = document.getElementById("sign-in");
    if (anchor) {
      anchor.scrollIntoView({ behavior: "smooth" });
      return;
    }
    // The form lives on the landing page: on any other route this button used to
    // do nothing at all, because the element it looks for is not on the page.
    window.location.href = "/#sign-in";
  }

  async function signOut() {
    try {
      await api.post("/auth/logout");
    } finally {
      window.location.href = "/";
    }
  }

  const user = me?.user ?? null;

  const links: Array<{ href: string; label: string }> = [{ href: "/gallery", label: "Gallery" }];
  if (user?.role === "participant") {
    links.push({ href: "/team", label: "Team" }, { href: "/submit", label: "Submission" });
  }
  if (user?.role === "judge" || user?.role === "admin") {
    links.push({ href: "/judge", label: "Judging" });
  }
  if (user?.role === "admin") {
    links.push({ href: "/admin", label: "Console" });
  }

  return (
    <header className="sticky top-0 z-40 border-b border-border/70 bg-background/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <Link href="/" className="flex items-center gap-2 text-[0.95rem] font-semibold tracking-tight">
          <span className="flex flex-col gap-[3px]" aria-hidden>
            <span className="block h-[3px] w-6 rounded-full bg-primary" />
            <span className="block h-[3px] w-4 rounded-full bg-primary/70" />
            <span className="block h-[3px] w-5 rounded-full bg-primary/40" />
          </span>
          Axion
        </Link>

        <nav aria-label="Primary" className="flex items-center gap-0.5">
          <div className="mr-1 hidden items-center gap-0.5 md:flex">
            {links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={isActive(link.href) ? "page" : undefined}
                className={cn(
                  linkClass,
                  isActive(link.href) && "bg-secondary/70 text-foreground",
                )}
              >
                {link.label}
              </Link>
            ))}
          </div>

          {eventWindow && <Deadline window={eventWindow} className="ml-1 hidden lg:inline-flex" />}

          <ThemeToggle />

          {user ? (
            <div className="ml-2 flex items-center gap-2">
              <Badge
                variant={
                  user.role === "admin" ? "accent" : user.role === "judge" ? "default" : "secondary"
                }
              >
                {user.role}
              </Badge>
              <Button size="sm" variant="outline" onClick={signOut}>
                Sign out
              </Button>
            </div>
          ) : (
            <Button size="sm" className="ml-1" onClick={goToSignIn}>
              Sign in
            </Button>
          )}
        </nav>
      </div>

      {/* Small screens get a second row rather than a hamburger: there are few
          links. The deadline rides along, because the screens least able to show
          it in the header are exactly the ones that still need to know. */}
      {/* Wraps rather than scrolls: a deadline that is clipped by the edge of its
          own row is a deadline nobody reads. */}
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 border-t border-border/60 px-4 py-1.5 lg:hidden">
        <div className="flex gap-1 md:hidden">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isActive(link.href) ? "page" : undefined}
              className={cn(
                linkClass,
                "shrink-0",
                isActive(link.href) && "bg-secondary/70 text-foreground",
              )}
            >
              {link.label}
            </Link>
          ))}
        </div>
        {eventWindow && <Deadline window={eventWindow} className="shrink-0" />}
      </div>
    </header>
  );
}
