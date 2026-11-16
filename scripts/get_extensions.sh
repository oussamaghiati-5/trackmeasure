#!/usr/bin/env bash
# Portable on macOS/Linux. Versions and hashes are pinned in extensions.lock.json.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO_ROOT/scripts/get_extensions.py" "$@"
