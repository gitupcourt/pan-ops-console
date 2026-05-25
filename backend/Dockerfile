FROM python:3.11-slim

# Don't write .pyc next to source — saves disk and survives a read-only root FS
# when we eventually toggle that on. Don't buffer stdout/stderr so pod logs are
# real-time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
 && rm -rf /var/lib/apt/lists/* \
 # Non-root runtime user. Matches the convention used across the homelab
 # (10001:10001). Created with no login shell and no home dir bloat.
 && groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --no-create-home \
             --home-dir /app --shell /usr/sbin/nologin app

COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app ./app

# /app/data is the SQLite mount point in prod. Pre-create it so the dir
# exists even if the volume is empty, owned by the runtime user.
RUN install -d -o app -g app -m 0750 /app/data

USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
