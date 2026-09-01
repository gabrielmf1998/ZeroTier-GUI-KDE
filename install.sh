#!/usr/bin/env bash
# Local install without a package manager. Installs to /usr, because the
# privileged helper and its polkit policy have to live at a fixed path that
# pkexec will accept. For Fedora, prefer the RPM in dist/.
set -euo pipefail

PREFIX=/usr
NAME=zerotier-tray-kde
BIN=zerotier-tray
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO=sudo

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c "import PySide6.QtWidgets, PySide6.QtNetwork" 2>/dev/null || {
    echo "PySide6 is required:  sudo dnf install python3-pyside6" >&2; exit 1; }
command -v pkexec >/dev/null || echo "warning: pkexec not found (polkit)" >&2
command -v zerotier-one >/dev/null || command -v zerotier-cli >/dev/null || \
    echo "warning: zerotier-one does not look installed" >&2

$SUDO install -d "$PREFIX/share/$NAME/zerotiertray"
$SUDO install -m 0644 "$ROOT"/zerotiertray/*.py "$PREFIX/share/$NAME/zerotiertray/"
$SUDO install -Dm 0755 "$ROOT/packaging/$BIN"      "$PREFIX/bin/$BIN"
$SUDO install -Dm 0755 "$ROOT/helper/$BIN-helper"  "$PREFIX/libexec/$BIN-helper"
$SUDO install -Dm 0644 "$ROOT/polkit/io.github.gabrielmf1998.zerotiertray.policy" \
    "$PREFIX/share/polkit-1/actions/io.github.gabrielmf1998.zerotiertray.policy"
$SUDO install -Dm 0644 "$ROOT/packaging/$BIN.desktop" \
    "$PREFIX/share/applications/$BIN.desktop"
$SUDO install -Dm 0644 "$ROOT/systemd/$BIN.service" \
    "$PREFIX/lib/systemd/user/$BIN.service"
for size in 48 64 128 256 512; do
    $SUDO install -Dm 0644 "$ROOT/assets/$BIN-${size}.png" \
        "$PREFIX/share/icons/hicolor/${size}x${size}/apps/$BIN.png"
done
$SUDO install -Dm 0644 "$ROOT/assets/$BIN.svg" \
    "$PREFIX/share/icons/hicolor/scalable/apps/$BIN.svg"

$SUDO gtk-update-icon-cache -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
$SUDO update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "Installed. Launch 'ZeroTier Tray' from the menu, or run: $BIN"
