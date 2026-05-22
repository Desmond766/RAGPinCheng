# Frontend image — Vite build, then nginx serving the static bundle and
# reverse-proxying /api/* to the backend container.

# Target platform is set in docker-compose.yml (build.platforms).
#
# --- Stage 1: build the static bundle ---
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# --- Stage 2: nginx serving the bundle ---
FROM nginx:1.27-alpine

# Replace the default config with our SSE-friendly + SPA-fallback one.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine default CMD already runs nginx in foreground.
