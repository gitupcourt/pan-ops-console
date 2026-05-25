#!/bin/sh
# Wait until the bind-mounted source tree is actually populated before
# starting whatever was passed as args. Guards against the Docker-Desktop /
# WSL cold-boot race where the container starts before the host mount is
# ready (symptom: /app shows as empty inside the container, then uvicorn
# blows up with ModuleNotFoundError: No module named 'app').

set -e

MARKER="/app/app/main.py"
MAX_WAIT=60   # seconds; container exits and Docker restarts us if we wait longer

count=0
while [ ! -f "$MARKER" ]; do
    if [ "$count" -ge "$MAX_WAIT" ]; then
        echo "ERROR: $MARKER never appeared after ${MAX_WAIT}s — bind mount broken?" >&2
        exit 1
    fi
    echo "Waiting for $MARKER to appear (mount establishment)…  [${count}s]"
    sleep 2
    count=$((count + 2))
done

echo "Source tree ready; starting $*"
exec "$@"
