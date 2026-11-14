#!/usr/bin/env bash
# Download and unpack extensions into ext/.
# Run once before the first crawl, or whenever you want to update them.
#
# Extensions installed:
#   ext/ubol          – uBlock Origin Lite (MV3), from uBlockOrigin/uBOL-home releases
#   ext/privacybadger – Privacy Badger, from EFForg/privacybadger src/
#
# FILTERING MODE NOTE (uBOL):
#   The default mode on a fresh profile is "basic" (permissive).
#   "Complete" mode blocks significantly more trackers but requires explicit consent
#   in the extension UI on first run.  To set "Complete" mode in a persistent profile
#   before crawling, run the helper snippet in the browser console:
#     chrome.storage.local.set({ defaultFilteringMode: 3 })
#   Alternatively, patch background.js (see comment below) for headless use.
#   Document whichever mode you use in the paper so results are reproducible.
#
# VERIFICATION:
#   After running this script, verify that extensions are active by running:
#     python crawler.py --sites test/sites_test.csv --run ext_check \
#       --extension ext/ubol --out /tmp/ext_check.sqlite --workers 1 \
#       --chromium-arg '--host-resolver-rules=MAP *.test 127.0.0.1:8765' --dwell 5
#   Then check that blocked_requests > 0:
#     python -c "import sqlite3; c=sqlite3.connect('/tmp/ext_check.sqlite'); \
#       print(c.execute(\"SELECT COUNT(*) FROM requests WHERE failure LIKE '%BLOCKED%'\").fetchone())"
#   If 0, switch to headful mode: add --headful and wrap with xvfb-run -a.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$REPO_ROOT/ext"
mkdir -p "$EXT_DIR"

# ---------------------------------------------------------------------------
# uBlock Origin Lite (MV3)
# ---------------------------------------------------------------------------
UBOL_DIR="$EXT_DIR/ubol"
echo "==> Fetching uBlock Origin Lite ..."

UBOL_RELEASE_URL=$(curl -sSf \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/uBlockOrigin/uBOL-home/releases/latest" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for a in data.get('assets', []):
    if a['name'].endswith('.zip') and 'chromium' in a['name'].lower():
        print(a['browser_download_url'])
        break
")

if [ -z "$UBOL_RELEASE_URL" ]; then
    echo "ERROR: Could not find uBOL Chromium zip in latest release." >&2
    echo "Check https://github.com/uBlockOrigin/uBOL-home/releases manually." >&2
    exit 1
fi

UBOL_ZIP="$(mktemp --suffix=.zip)"
echo "  Downloading $UBOL_RELEASE_URL"
curl -sSfL "$UBOL_RELEASE_URL" -o "$UBOL_ZIP"

rm -rf "$UBOL_DIR"
mkdir -p "$UBOL_DIR"
unzip -q "$UBOL_ZIP" -d "$UBOL_DIR"
rm "$UBOL_ZIP"

# If the zip extracts into a subdirectory, move it up.
SUBDIR=$(find "$UBOL_DIR" -maxdepth 1 -type d | grep -v "^$UBOL_DIR$" | head -1)
if [ -n "$SUBDIR" ] && [ "$(ls -A "$UBOL_DIR")" = "$(basename "$SUBDIR")" ]; then
    mv "$SUBDIR"/* "$UBOL_DIR"/
    rmdir "$SUBDIR"
fi

# Verify manifest is present
if [ ! -f "$UBOL_DIR/manifest.json" ]; then
    echo "WARNING: $UBOL_DIR/manifest.json not found – check the zip structure." >&2
    ls "$UBOL_DIR"
fi

echo "  uBOL installed at $UBOL_DIR"
echo "  Default filtering mode: basic (see FILTERING MODE NOTE above to enable Complete)"

# ---------------------------------------------------------------------------
# Privacy Badger (MV2-compatible src/)
# ---------------------------------------------------------------------------
PB_DIR="$EXT_DIR/privacybadger"
echo "==> Fetching Privacy Badger ..."

PB_TAG=$(curl -sSf \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/EFForg/privacybadger/releases/latest" \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['tag_name'])")

echo "  Latest tag: $PB_TAG"
PB_TGZ="$(mktemp --suffix=.tar.gz)"
curl -sSfL \
    "https://github.com/EFForg/privacybadger/archive/refs/tags/${PB_TAG}.tar.gz" \
    -o "$PB_TGZ"

TMP_PB="$(mktemp -d)"
tar -xzf "$PB_TGZ" -C "$TMP_PB"
PB_SRC=$(find "$TMP_PB" -maxdepth 2 -type d -name "src" | head -1)
if [ -z "$PB_SRC" ]; then
    echo "ERROR: src/ not found in Privacy Badger tarball." >&2
    ls "$TMP_PB"
    rm -rf "$TMP_PB" "$PB_TGZ"
    exit 1
fi
rm -rf "$PB_DIR"
cp -r "$PB_SRC" "$PB_DIR"
rm -rf "$TMP_PB" "$PB_TGZ"

echo "  Privacy Badger $PB_TAG installed at $PB_DIR"

echo ""
echo "Done. Extensions:"
echo "  ext/ubol          – uBlock Origin Lite (MV3, mode: basic)"
echo "  ext/privacybadger – Privacy Badger $PB_TAG"
echo ""
echo "Run 'make smoke' to verify (or see VERIFICATION section above)."
