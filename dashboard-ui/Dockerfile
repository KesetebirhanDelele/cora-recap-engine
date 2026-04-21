FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --only=production=false

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Build-time env vars are baked into the Next.js bundle via next.config.js.
# Pass DASHBOARD_API_URL and WS_URL as build args when these differ per environment.
ARG DASHBOARD_API_URL=http://localhost:8001
ARG WS_URL=ws://localhost:8001
ENV DASHBOARD_API_URL=$DASHBOARD_API_URL
ENV WS_URL=$WS_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Copy only what next start needs
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
# public/ is optional — Next.js projects without static assets may not have it
RUN mkdir -p ./public
COPY --from=builder /app/public* ./public/
EXPOSE 3000
CMD ["npm", "run", "start"]
