FROM python:3.11-slim
# pan-os-python (as of 1.12.x) still imports distutils.version, which was
# removed in Python 3.12. Stay on 3.11 until pan-os-python drops that.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg, building wheels, and SCP-style image transfer to firewalls.
# `locales` is needed because pan-os-upgrade-assurance calls locale.setlocale(),
# and python:3.11-slim ships with no locale data.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      openssh-client \
      curl \
      iproute2 \
      locales \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/* \
 # Non-root runtime user. Matches the convention used across the homelab (10001).
 && groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --no-create-home \
             --home-dir /app --shell /usr/sbin/nologin app

ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

COPY --chown=app:app requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Pre-create the images storage path with the right ownership so that
# (a) under docker-compose with a named volume, Docker populates the volume
#     from the image's permissions on first run;
# (b) under k8s with a PVC + fsGroup, the mount target already exists
#     with sane ownership before the PVC overlays it.
RUN install -d -o app -g app -m 0750 /var/lib/panfw /var/lib/panfw/images

# Bake the mount-wait script at a path OUTSIDE the bind mount so it survives
# the cold-boot race we're guarding against. /app gets overlaid by the host
# bind mount at runtime; /usr/local/bin/ is unaffected.
COPY wait-for-mount.sh /usr/local/bin/wait-for-mount.sh
RUN chmod +x /usr/local/bin/wait-for-mount.sh

USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
