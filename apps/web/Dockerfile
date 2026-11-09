# WorkMeta AI web — production static build served by nginx.
# Build from the repository root:
#   docker build -f apps/web/Dockerfile -t workmeta-web:VERSION .
FROM node:24-alpine AS build

WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
COPY packages/design-tokens packages/design-tokens
RUN npm ci --ignore-scripts

COPY apps/web apps/web
RUN npm run build:web

FROM nginx:1.27-alpine AS runtime

COPY infra/deploy/nginx/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
