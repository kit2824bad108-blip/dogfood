import type { Metadata } from "next";

import { Nav } from "@/components/nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "Axion — trustless hackathon execution",
  description:
    "Open-source hackathon lifecycle platform: blind technical evaluation, Z-score normalized judging, and a cryptographically audited scoring trail.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <Nav />
        <main className="mx-auto w-full max-w-6xl px-6 py-8">{children}</main>
        <footer className="mx-auto w-full max-w-6xl px-6 pb-10 pt-4 text-xs text-muted-foreground">
          Axion · submissions are judged on repository, documentation and craft first.
        </footer>
      </body>
    </html>
  );
}
