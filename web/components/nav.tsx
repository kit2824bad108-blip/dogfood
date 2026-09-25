"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";

const linkClass =
  "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground";

export function Nav() {
  const [me, setMe] = useState<Me | null>(null);
  const pathname = usePathname();

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

        <nav className="flex items-center gap-0.5">
          <div className="mr-1 hidden items-center gap-0.5 md:flex">
            {links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  linkClass,
                  pathname === link.href && "text-foreground",
                )}
              >
                {link.label}
              </Link>
            ))}
          </div>

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
            <Button
              size="sm"
              className="ml-1"
              onClick={() => {
                document.getElementById("sign-in")?.scrollIntoView({ behavior: "smooth" });
              }}
            >
              Sign in
            </Button>
          )}
        </nav>
      </div>

      {/* Small screens get a second row rather than a hamburger: there are few links. */}
      <div className="flex gap-1 overflow-x-auto border-t border-border/60 px-4 py-1.5 md:hidden">
        {links.map((link) => (
          <Link key={link.href} href={link.href} className={cn(linkClass, "shrink-0")}>
            {link.label}
          </Link>
        ))}
      </div>
    </header>
  );
}
