#!/usr/bin/env bash
# Install Claude AI Map Maker Pro to QGIS on Linux.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="claude_qgis_pro"

CANDIDATES=(
  "$HOME/.local/share/QGIS/QGIS4/profiles/default/python/plugins"
  "$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins"
)

TARGET=""
for p in "${CANDIDATES[@]}"; do
  if [[ -d "$(dirname "$p")" ]]; then
    TARGET="$p"
    break
  fi
done

if [[ -z "$TARGET" ]]; then
  echo "Could not find a QGIS profile folder under ~/.local/share/QGIS."
  echo "Launch QGIS once, then re-run."
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
echo "Installed. Restart QGIS, then: Plugins → Manage and Install Plugins →"
echo "Installed tab → enable 'Claude AI Map Maker Pro'."
