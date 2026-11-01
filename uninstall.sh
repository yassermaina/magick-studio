#!/usr/bin/env bash
# Magick Studio uninstaller.
#
# Removes only the files install.sh created. Your images, processed output
# folders and system packages (ImageMagick, GTK, ...) are left untouched.

set -euo pipefail

APP_ID="io.github.magickstudio.MagickStudio"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/magick-studio"
LAUNCHER="$HOME/.local/bin/magick-studio"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"

removed=0
remove_file() {
    if [ -f "$1" ] || [ -L "$1" ]; then
        rm -f -- "$1"
        echo "Removed $1"
        removed=$((removed + 1))
    fi
}

# Only remove the launcher if it is ours.
if [ -f "$LAUNCHER" ] && ! grep -q "Magick Studio launcher" "$LAUNCHER"; then
    echo "Leaving $LAUNCHER alone: it was not created by Magick Studio." >&2
else
    remove_file "$LAUNCHER"
fi

remove_file "$DESKTOP_FILE"
remove_file "$ICON_FILE"
for f in main.py image_processor.py LICENSE; do
    remove_file "$APP_DIR/$f"
done
rm -rf -- "$APP_DIR/__pycache__"
# Remove the app folder only if it is now empty (never deletes unknown files).
rmdir -- "$APP_DIR" 2>/dev/null || true

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true
# Refresh the user icon cache only if one already exists.
if [ -f "$DATA_HOME/icons/hicolor/icon-theme.cache" ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true
fi

if [ "$removed" -eq 0 ]; then
    echo "Magick Studio does not appear to be installed."
else
    echo "Magick Studio has been uninstalled. Your images were not touched."
fi
