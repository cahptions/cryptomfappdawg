#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v node >/dev/null && command -v npm >/dev/null; then
  cd "$repo_root/webui"
  export npm_config_cache="${npm_config_cache:-${TMPDIR:-/tmp}/kestrel-npm-cache}"
  npm ci --no-audit --no-fund
  npm run build
elif command -v docker >/dev/null; then
  # Node is a build dependency only. The resulting app runs with Python alone.
  docker run --rm --user "$(id -u):$(id -g)" \
    --env npm_config_cache=/tmp/kestrel-npm-cache \
    --mount "type=bind,src=$repo_root,dst=/app" \
    --workdir /app/webui node:22.16.0-bookworm-slim \
    sh -c 'npm ci --no-audit --no-fund && npm run build'
else
  printf '%s\n' 'Build requires Node 22+ with npm, or Docker. No Node is needed to run the built console.' >&2
  exit 1
fi
