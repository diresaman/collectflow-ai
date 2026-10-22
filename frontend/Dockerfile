# Next.js UI and the /api/backend proxy.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Downloaded packages are kept in a BuildKit cache between builds, and npm retries patiently, so a
# rebuild survives a slow or lossy connection (and usually needs no download at all).
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit --no-fund \
    --fetch-retries=6 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000
COPY . .
# Inlined into the browser bundle at build time (public health check only; never a secret).
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
# Inside the container the server must listen on all interfaces; Compose publishes it on
# 127.0.0.1 only.
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
