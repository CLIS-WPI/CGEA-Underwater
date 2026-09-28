#!/usr/bin/env bash
# Build and verify the CGEA GPU Docker environment.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/docker"

echo "Building cgea-underwater:latest ..."
docker compose build

echo "Verifying environment ..."
docker compose run --rm cgea python /usr/local/bin/verify_cgea_env.py

echo "Running pytest inside Docker ..."
docker compose run --rm cgea pytest -q

echo "DONE"
