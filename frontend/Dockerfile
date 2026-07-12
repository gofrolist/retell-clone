# Frontend (Next.js dashboard) image. Mirrors the backend/worker discipline:
# multi-stage build, a minimal final image (Next.js standalone output), and a
# non-root runtime user. bun is the package manager / build tool; the
# standalone server itself runs under node.

# --- deps + build stage ---
# TODO: pin to an immutable digest (@sha256:...) once a trusted one is verified,
# to match the digest pinning used by backend/worker Dockerfiles.
FROM oven/bun:1-slim AS builder
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# Install dependencies first so this layer stays cached until the lockfile
# changes.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# Build the standalone output (requires output: "standalone" in next.config.ts).
COPY . .
RUN bun run build

# --- runtime stage ---
# TODO: pin to an immutable digest (@sha256:...) once a trusted one is verified.
FROM node:22-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Run as the non-root "node" user shipped in the official node image.
USER node

# Copy only the standalone bundle: server.js + traced node_modules, then the
# static assets and public files the minimal server does not copy itself.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
