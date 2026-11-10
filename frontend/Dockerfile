# syntax=docker/dockerfile:1
ARG NODE_VERSION=24
ARG NGINX_VERSION=1.30

# ---- deps: exact dependency tree from the lockfile -----------------------------------------------
FROM node:${NODE_VERSION}-alpine AS deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ---- dev: sources on top of the dependencies -----------------------------------------------------
# Used by `frontend-tests` (lint + unit tests) and by the optional `frontend-dev` Vite server.
FROM deps AS dev

COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# ---- build: static production bundle -------------------------------------------------------------
FROM dev AS build

# Baked into the bundle at build time: where the "Open Airflow" links point.
ARG VITE_AIRFLOW_URL=http://localhost:8080
ENV VITE_AIRFLOW_URL=${VITE_AIRFLOW_URL}
RUN npm run build

# ---- runtime: nginx serves the bundle and proxies /api to Django ---------------------------------
FROM nginx:${NGINX_VERSION}-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
