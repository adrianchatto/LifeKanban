# LifeKanban — containerised personal Kanban board.
# Pure Python standard library (no pip dependencies), so the image is tiny.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="LifeKanban" \
      org.opencontainers.image.description="Personal Kanban board served by a stdlib Python HTTP server" \
      org.opencontainers.image.source="https://github.com/adrianchatto/LifeKanban"

# Container defaults: bind to all interfaces and keep data on a mounted volume.
ENV KANBAN_HOST=0.0.0.0 \
    KANBAN_PORT=8787 \
    KANBAN_DATA=/data \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Application code and static assets (no third-party dependencies).
COPY server.py auth.py kanban.py notify.py ./
COPY index.html login.html settings.html admin.html manifest.webmanifest ./
COPY icon-192.png icon-512.png icon-maskable-512.png ./
COPY skills ./skills
# Seed data baked into the image; copied to the volume on first run only.
COPY board.json ./board.json
COPY results ./results
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data

EXPOSE 8787
VOLUME ["/data"]

# Liveness check against a public, unauthenticated endpoint (the board API now
# requires a login, so we ping the login page instead).
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('KANBAN_PORT','8787')+'/login.html').read()" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
