#!/usr/bin/env bash
# Install Rock Bed Visualizer to QGIS on macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="rock_bed_visualizer"

CANDIDATES=(
  "$HOME/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins"
  "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins"
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
echo "  1. Launch QGIS (restart it if already open)."
echo "  2. Plugins -> Manage and Install Plugins -> Installed tab."
echo "  3. Enable 'Rock Bed Visualizer'."
echo "  4. Click the new toolbar icon (orange/purple rock bed)."
