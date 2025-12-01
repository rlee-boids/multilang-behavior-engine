#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."

cd "$PROJECT_ROOT"

echo "=== [1/4] Loading .env (if present) ==="
if [ -f ".env" ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^\s*#' .env | sed 's/\r$//' | awk 'NF {print $1}')
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set; check backend/.env" >&2
  exit 1
fi

echo "=== [2/4] Resetting database ==="
# If you use a venv, uncomment:
# source .venv/bin/activate
python scripts/reset_db.py

echo "=== [3/4] Cleaning Podman containers/images/volumes for mlbe ==="

if podman ps -a --format '{{.Names}}' | grep -q '^mlbe-'; then
  podman ps -a --format '{{.Names}}' | grep '^mlbe-' | xargs -r podman rm -f
else
  echo "No mlbe-* containers found."
fi

if podman images --format '{{.Repository}}:{{.Tag}}' | grep -q '^localhost/mlbe-'; then
  podman images --format '{{.Repository}}:{{.Tag}}' | grep '^localhost/mlbe-' | xargs -r podman rmi -f
else
  echo "No localhost/mlbe-* images found."
fi

if podman volume ls --format '{{.Name}}' | grep -q '^mlbe-'; then
  podman volume ls --format '{{.Name}}' | grep '^mlbe-' | xargs -r podman volume rm -f || true
fi

echo "=== [4/4] Cleaning local workspaces (analyzer/runtime/services) ==="
rm -rf workspace/analyzer workspace/runtime workspace/services
rm -rf workspace

echo "Reset complete ✅"
