FROM node:20-alpine

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# The lockfile pins the exact tree (including the patched Next.js release).
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# Rewrite destinations are baked at build time, so the Compose service name has
# to be known *here*, not at runtime. Inside the compose network the API is
# reachable as http://api:8000; outside Docker the localhost default applies.
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL
RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
