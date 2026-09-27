import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function NotFound() {
  return (
    <Card className="mx-auto max-w-xl">
      <CardHeader>
        <CardTitle>That page does not exist</CardTitle>
        <CardDescription>
          The link may be stale, or the route may be one only signed-in roles can reach.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        <Link href="/">
          <Button>Back to the overview</Button>
        </Link>
        <Link href="/gallery">
          <Button variant="outline">Browse the gallery</Button>
        </Link>
        <a href="/api/docs" target="_blank" rel="noreferrer">
          <Button variant="ghost">API reference</Button>
        </a>
      </CardContent>
    </Card>
  );
}
