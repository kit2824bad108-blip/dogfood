// NOTE: rewrite destinations are resolved at BUILD time and serialised into
// .next/routes-manifest.json — changing API_INTERNAL_URL at runtime has no
// effect. Locally the default (http://localhost:8000) is correct; the Docker
// image passes `--build-arg API_INTERNAL_URL=http://api:8000` so the container
// reaches the API service rather than itself.
const API_INTERNAL_URL = process.env.API_INTERNAL_URL || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Every browser call goes to /api/* on this origin, which Next proxies to the
  // FastAPI service. That keeps the session cookie same-origin (no CORS, no
  // cross-site cookie headaches) and hides the API port from the client.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_INTERNAL_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
