#!/usr/bin/env bash
# Magick Studio installer.
#
# Installs for the current user into ~/.local (no root needed for the app).
# sudo is only used, after asking, to install missing system packages.
#
# Usage: ./install.sh [--skip-deps] [--with-avif] [--yes]
#   --skip-deps  Do not check or install system packages
#   --with-avif  Also install ImageMagick AVIF/HEIC support (Fedora: ImageMagick-heic)
#   --yes        Do not ask before installing packages

set -euo pipefail

APP_ID="io.github.magickstudio.MagickStudio"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/magick-studio"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/magick-studio"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"

SKIP_DEPS=0
WITH_AVIF=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --skip-deps) SKIP_DEPS=1 ;;
        --with-avif) WITH_AVIF=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
    fail "Run this installer as your normal user, not as root. It will ask for sudo only if packages are missing."
fi

for f in main.py image_processor.py assets/icon.svg; do
    [ -f "$SRC_DIR/$f" ] || fail "Missing $f — run install.sh from the Magick Studio source folder."
done

# Prefer the system interpreter: PyGObject comes from distribution packages.
find_python() {
    PYTHON="/usr/bin/python3"
    [ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
}
find_python

# --------------------------------------------------------------- dependencies

DISTRO_ID="unknown"; DISTRO_LIKE=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"; DISTRO_LIKE="${ID_LIKE:-}"
fi
info "Detected distribution: ${PRETTY_NAME:-$DISTRO_ID}"

# Prints the Libadwaita version, or nothing if GTK 4/Libadwaita bindings are missing.
adw_version() {
    [ -n "$PYTHON" ] || return 0
    "$PYTHON" - <<'PY' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
print(f"{Adw.get_major_version()}.{Adw.get_minor_version()}")
PY
}
have_gtk() { [ -n "$(adw_version)" ]; }
adw_new_enough() {
    local v; v="$(adw_version)"
    [ -n "$v" ] && [ "${v%%.*}" -ge 1 ] && { [ "${v%%.*}" -gt 1 ] || [ "${v#*.}" -ge 5 ]; }
}
have_magick() { command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1; }
have_avif()   {
    local bin; bin="$(command -v magick || command -v convert || true)"
    [ -n "$bin" ] && "$bin" -list format 2>/dev/null | grep -qE '^ *AVIF\*? +([A-Z0-9]+ +)?rw'
}

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    [ -t 0 ] || return 1
    read -r -p "$1 [Y/n] " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

install_packages() {
    local manager="$1"; shift
    info "Missing packages: $*"
    if ! confirm "Install them now with sudo $manager?"; then
        fail "Please install manually: sudo $manager install $*"
    fi
    if [ "$manager" = "apt" ]; then
        sudo apt-get update
        sudo apt-get install -y "$@"
    else
        sudo dnf install -y "$@"
    fi
}

if [ "$SKIP_DEPS" -eq 0 ]; then
    missing=()
    case " $DISTRO_ID $DISTRO_LIKE " in
        *" fedora "*|*" rhel "*|*" centos "*)
            [ -n "$PYTHON" ] || missing+=(python3)
            have_gtk    || missing+=(python3-gobject gtk4 libadwaita)
            have_magick || missing+=(ImageMagick)
            if [ "$WITH_AVIF" -eq 1 ] && ! have_avif; then missing+=(ImageMagick-heic); fi
            [ "${#missing[@]}" -eq 0 ] || install_packages dnf "${missing[@]}"
            ;;
        *" debian "*|*" ubuntu "*)
            [ -n "$PYTHON" ] || missing+=(python3)
            have_gtk    || missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)
            have_magick || missing+=(imagemagick)
            # cwebp: used when ImageMagick ignores WebP quality (Ubuntu 24.04).
            command -v cwebp >/dev/null 2>&1 || missing+=(webp)
            if [ "$WITH_AVIF" -eq 1 ] && ! have_avif; then missing+=(libheif-plugin-aomenc); fi
            [ "${#missing[@]}" -eq 0 ] || install_packages apt "${missing[@]}"
            ;;
        *)
            warn "Unsupported distribution; not installing packages automatically."
            ;;
    esac
    find_python
    [ -n "$PYTHON" ] || fail "python3 is not available."
    have_gtk    || fail "GTK 4 + Libadwaita Python bindings (PyGObject) are not available."
    adw_new_enough || fail "Libadwaita $(adw_version) is too old; Magick Studio needs 1.5 or newer (Fedora 40+, Ubuntu 24.04+, Debian 13+)."
    have_magick || fail "ImageMagick is not available."
    if have_avif; then info "AVIF output: available"; else info "AVIF output: not available (optional; use --with-avif)"; fi
    info "Dependencies OK"
fi

# --------------------------------------------------------------- install files

if [ -e "$LAUNCHER" ] && ! grep -q "Magick Studio launcher" "$LAUNCHER" 2>/dev/null; then
    fail "$LAUNCHER exists and was not created by Magick Studio; refusing to overwrite it."
fi

[ -n "$PYTHON" ] || fail "python3 is not installed."
info "Installing to $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")"
install -m 644 "$SRC_DIR/main.py" "$SRC_DIR/image_processor.py" "$APP_DIR/"
install -m 644 "$SRC_DIR/assets/icon.svg" "$ICON_FILE"
[ -f "$SRC_DIR/LICENSE" ] && install -m 644 "$SRC_DIR/LICENSE" "$APP_DIR/"

cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Magick Studio launcher (created by install.sh)
exec "$PYTHON" "$APP_DIR/main.py" "\$@"
EOF
chmod 755 "$LAUNCHER"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Magick Studio
GenericName=Image Optimizer
Comment=Resize, compress, convert and optimize images
Exec=$LAUNCHER %F
Icon=$APP_ID
Terminal=false
Categories=Graphics;2DGraphics;RasterGraphics;GTK;GNOME;
Keywords=image;resize;compress;convert;webp;jpeg;png;avif;optimize;imagemagick;
MimeType=image/jpeg;image/png;image/webp;image/avif;image/gif;image/bmp;image/tiff;image/heic;
StartupNotify=true
StartupWMClass=$APP_ID
DBusActivatable=false
EOF
chmod 644 "$DESKTOP_FILE"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE" || warn "Desktop file validation reported problems."
fi

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true
# Refresh the user icon cache only if one already exists.
if [ -f "$DATA_HOME/icons/hicolor/icon-theme.cache" ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true
fi

info "Magick Studio installed."
case ":$PATH:" in
    *":$BIN_DIR:"*) echo "Launch it from the GNOME application menu or run: magick-studio" ;;
    *) warn "$BIN_DIR is not in your PATH yet. Open a new terminal (or log out and back in); until then run $LAUNCHER" ;;
esac
