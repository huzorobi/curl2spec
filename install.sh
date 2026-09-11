#!/usr/bin/env bash
# curl2spec installer — creates a desktop icon (and applications-menu entry) so you can launch it with a click.
# Safe to re-run. Linux/XDG desktops (Kali/GNOME/XFCE).
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="$HOME/.local/share/applications"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
ICON="$HERE/icon-256.png"; [ -f "$ICON" ] || ICON="$HERE/icon.svg"

chmod +x "$HERE/curl2spec.sh"
mkdir -p "$APPS" "$DESKTOP"

make_entry() {
  cat > "$1" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=curl2spec
GenericName=Auth-spec generator
Comment=Turn a login into a 2-account BOLA/IDOR test spec — Manual (paste cURL) or Pro (auto-capture)
Exec=$HERE/curl2spec.sh
Icon=$ICON
Terminal=true
Categories=Development;Security;
Keywords=curl;bola;idor;auth;pentest;spec;
DESK
  chmod +x "$1"
}

make_entry "$APPS/curl2spec.desktop"
make_entry "$DESKTOP/curl2spec.desktop"

# mark the desktop icon trusted so double-click works without the "untrusted" prompt (GNOME/Nautilus)
gio set "$DESKTOP/curl2spec.desktop" metadata::trusted true 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

echo "✓ Installed curl2spec launcher:"
echo "    desktop icon : $DESKTOP/curl2spec.desktop"
echo "    apps menu    : $APPS/curl2spec.desktop"
echo "  Double-click the desktop icon, or run ./curl2spec.sh — it starts the server and opens the browser."
