#!/usr/bin/env bash
# Install Claude AI Map Maker Pro to QGIS on macOS.
# Works for both QGIS 3.x and QGIS 4.x — picks the newest profile folder found.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="claude_qgis_pro"

# Search order: QGIS4 first, then QGIS (older layouts), then QGIS3.
CANDIDATES=(
  "$HOME/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins"
  "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins"
  "$HOME/Library/Application Support/QGIS/QGIS/profiles/default/python/plugins"
)

TARGET=""
for p in "${CANDIDATES[@]}"; do
  if [[ -d "$(dirname "$p")" ]]; then
    TARGET="$p"
    break
  fi
done

if [[ -z "$TARGET" ]]; then
  echo "Could not find a QGIS profile folder in ~/Library/Application Support/QGIS."
  echo "Open QGIS at least once, then re-run this script."
  exit 1
fi

mkdir -p "$TARGET"
DEST="$TARGET/$PLUGIN_NAME"

if [[ -d "$DEST" ]]; then
  echo "Removing existing install at $DEST"
  rm -rf "$DEST"
fi

echo "Copying plugin to: $DEST"
cp -R "$SCRIPT_DIR" "$DEST"

echo ""
echo "Installed. Next steps:"
echo "  1. Launch QGIS."
echo "  2. Plugins → Manage and Install Plugins → Installed tab."
echo "  3. Enable 'Claude AI Map Maker Pro'."
echo "  4. Click the new toolbar icon (blue/purple map pin)."
echo "  5. Open ⚙ Settings and paste your Anthropic API key."
