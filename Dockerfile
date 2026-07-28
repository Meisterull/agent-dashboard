# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: Frontend bauen (Node nur im Build, nicht im Runtime-Image)
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python-Abhängigkeiten in venv bauen (build-essential nur hier)
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS python-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 3: schlankes Runtime-Image (kein build-essential, kein sshpass, kein npm)
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Nur, was zur Laufzeit wirklich gebraucht wird.
# Key-Only-SSH => sshpass entfällt bewusst (siehe Security-Kapitel).
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        certbot \
        openssh-client \
        gettext-base \
        supervisor \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Nicht-root App-User. nginx-Master läuft als root und droppt selbst
# auf die Worker; alle Python-Prozesse laufen als 'app'.
RUN useradd --create-home --uid 10001 app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=python-build /opt/venv /opt/venv

WORKDIR /app
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY backend /app/backend
COPY nginx /app/nginx
COPY scripts /app/scripts
COPY supervisord.conf /app/supervisord.conf
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 80 443

# entrypoint macht das root-Setup (SSL, Verzeichnisse, chown, nginx render)
# und startet danach supervisord, das die Einzelprozesse beaufsichtigt.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
