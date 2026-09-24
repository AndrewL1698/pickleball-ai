# The web app, built once and served by Next's standalone server.
#
# NEXT_PUBLIC_ values are inlined into the browser bundle at build time, so the
# API URL is a build argument rather than a runtime environment variable. The
# consequence is real: one image per environment, and changing the API URL
# means rebuilding. It is passed as a build arg rather than hardcoded so the
# compose file stays the single place that decides it.
FROM node:24-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./

# The browser resolves this, not the container, so it is localhost even though
# the API is reachable as `api` on the compose network.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_MAX_UPLOAD_BYTES=2147483648
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_MAX_UPLOAD_BYTES=$NEXT_PUBLIC_MAX_UPLOAD_BYTES \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# `output: "standalone"` emits a server plus only the modules it actually
# needs, so node_modules is not copied.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
USER node
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
