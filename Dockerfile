FROM python:3.11-slim AS builder

WORKDIR /install

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/deps -r requirements.txt


FROM python:3.11-slim AS final

LABEL maintainer="premmaradiya"
LABEL version="2.0"
LABEL description="EduTrack - Student Management System"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/deps/lib/python3.11/site-packages

WORKDIR /app

COPY --from=builder /deps /usr/local

RUN groupadd -r appgroup && useradd -r -g appgroup appuser

COPY app.py .
COPY templates/ templates/
COPY static/ static/

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app"]
