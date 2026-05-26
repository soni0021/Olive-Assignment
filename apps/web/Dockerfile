# Multi-stage build for the Next.js chat UI.
#
# Key gotcha: pnpm workspaces install zod into the per-package node_modules of
# shared-schema, not into apps/web. The earlier Dockerfile copied only the
# top-level + apps/web node_modules and the Next.js TS check then failed on
# `import { z } from "zod"` in shared-schema. Fix: copy the entire /app tree
# (including workspace package node_modules) between stages, and use
# `pnpm install --filter <pkg>...` (trailing `...`) so transitive workspace
# deps are fully installed.

FROM node:20-alpine AS base
RUN apk add --no-cache libc6-compat
RUN corepack enable && corepack prepare pnpm@9.12.3 --activate
WORKDIR /app

FROM base AS deps
COPY pnpm-workspace.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY packages/shared-schema/typescript/package.json packages/shared-schema/typescript/
RUN pnpm install --filter "@llm-observe/web..."

FROM base AS builder
# Copy the entire /app from deps so every workspace package's node_modules
# (including pnpm's symlinks) is preserved.
COPY --from=deps /app /app
COPY apps/web apps/web
COPY packages/shared-schema/typescript packages/shared-schema/typescript
WORKDIR /app/apps/web
RUN pnpm build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
