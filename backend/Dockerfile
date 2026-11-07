FROM python:3.12.12-alpine3.22

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app -H -s /sbin/nologin app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=root:root . .

USER 10001:10001
EXPOSE 8000

# Factory em backend/app/__init__.py (create_app). /tmp deve ser tmpfs (container read-only).
# 1 worker + 8 threads: o rate limit é em memória por processo; com 2+ workers o limite
# efetivo multiplicaria. Trade-off: sem paralelismo real de CPU (GIL); a carga é I/O (Supabase).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--worker-tmp-dir", "/tmp", "--timeout", "30", "--limit-request-line", "4094", \
     "--access-logfile", "-", \
     "--access-logformat", "%(h)s %(m)s %(U)s %(s)s %(b)s %(L)ss", \
     "app:create_app()"]
