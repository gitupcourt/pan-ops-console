# Dev image — Vite dev server with HMR. Used by Tilt and by anyone running
# the source-level dev loop via docker compose. Hardened to match prod:
# runs as a non-root user (10001) so PodSecurity restricted accepts it.
FROM node:20-alpine

# Predictable non-root UID, matching the backend's runtime user. Even though
# vite dev is launched in dev contexts, we keep the hardening posture
# consistent across all images we publish.
RUN addgroup -S -g 10001 app \
 && adduser -S -u 10001 -G app -h /app -s /sbin/nologin app

WORKDIR /app

# Install deps as the runtime user so the resulting node_modules tree is
# owned correctly. (npm running as root produces a tree owned by root,
# which then trips up a non-root server at runtime.)
COPY --chown=app:app package.json package-lock.json* ./
USER 10001:10001
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Source files come in next. Volume-mounted in docker-compose / Tilt, baked
# in for one-off `docker run` use.
COPY --chown=app:app . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
