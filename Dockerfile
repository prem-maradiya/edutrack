# ─────────────────────────────────────────────
# STAGE 1: BUILDER
# Purpose: Install dependencies in a separate stage
# Why: We don't want build tools (pip, compilers) in our final image
# This keeps the final image small and secure
# ─────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Set working directory inside builder stage
WORKDIR /install

# Copy only requirements first
# Why: Docker caches this layer — if requirements.txt doesn't change,
# Docker reuses the cached layer and skips pip install (faster builds)
COPY requirements.txt .

# Install dependencies into a custom folder /deps
# --no-cache-dir → don't store pip cache (saves space)
# --prefix=/deps → installs packages to /deps instead of system Python
RUN pip install --no-cache-dir --prefix=/deps -r requirements.txt


# ─────────────────────────────────────────────
# STAGE 2: FINAL IMAGE
# Purpose: Lean production image with only what we need
# Base: python:3.11-slim (~150MB vs ~900MB for full python:3.11)
# ─────────────────────────────────────────────
FROM python:3.11-slim AS final

# ── Labels (metadata about the image) ──
# Visible in: docker inspect studentmanagement-web
LABEL maintainer="premmaradiya"
LABEL version="2.0"
LABEL description="EduTrack - Student Management System"

# ── Environment Variables ──
# PYTHONDONTWRITEBYTECODE: Stops Python writing .pyc files (saves space)
# PYTHONUNBUFFERED: Forces stdout/stderr to flush immediately
#   Why important: Without this, Docker logs show nothing until app crashes
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/deps/lib/python3.11/site-packages

# ── Working Directory ──
WORKDIR /app

# ── Copy installed dependencies from builder stage ──
# This is the key multi-stage trick:
# We copy the compiled packages but NOT pip or build tools
COPY --from=builder /deps /usr/local

# ── Create non-root user ──
# Security best practice: never run containers as root
# If container is compromised, attacker gets limited user, not root
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# ── Copy application code ──
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# ── Set correct ownership ──
RUN chown -R appuser:appgroup /app

# ── Switch to non-root user ──
USER appuser

# ── Expose port ──
# Documents that the app listens on 5000
# Does NOT actually publish the port (that's done in docker-compose)
EXPOSE 5000

# ── Health Check ──
# Docker checks this every 30s to know if container is healthy
# --interval: check every 30 seconds
# --timeout: wait max 10 seconds for response
# --start-period: give app 40 seconds to start before checking
# --retries: mark unhealthy after 3 failed checks
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"

# ── Start Command ──
# gunicorn: production WSGI server (better than Flask dev server)
# -w 2: 2 worker processes (handles concurrent requests)
# -b 0.0.0.0:5000: listen on all interfaces, port 5000
# app:app → filename:Flask_app_variable
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app"]