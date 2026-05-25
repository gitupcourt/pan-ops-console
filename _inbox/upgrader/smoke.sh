#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PW=$(grep '^INITIAL_ADMIN_PASSWORD' .env | cut -d= -f2-)

echo "--- POST /api/auth/login"
RESP=$(curl -sf -X POST http://localhost:8000/api/auth/login \
  -d "username=admin&password=${PW}")
TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Got token (length: ${#TOKEN})"

AUTH="Authorization: Bearer ${TOKEN}"

echo ""
echo "--- GET /api/auth/me"
curl -sf -H "$AUTH" http://localhost:8000/api/auth/me; echo

echo ""
echo "--- GET /api/devices (expect [])"
curl -sf -H "$AUTH" http://localhost:8000/api/devices; echo

echo ""
echo "--- GET /api/jobs (expect [])"
curl -sf -H "$AUTH" http://localhost:8000/api/jobs; echo

echo ""
echo "--- GET /api/panoramas (expect [])"
curl -sf -H "$AUTH" http://localhost:8000/api/panoramas; echo

echo ""
echo "All smoke checks passed."
