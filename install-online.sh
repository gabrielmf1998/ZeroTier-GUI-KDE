#!/bin/sh
# ZeroTier Tray — online installer. Detects the distro and installs the matching
# package from the latest GitHub release. Unknown distros get the AppImage.
#
#   curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/ZeroTier-Tray-KDE/main/install-online.sh | sh
set -eu

REPO="gabrielmf1998/ZeroTier-Tray-KDE"
API="https://api.github.com/repos/$REPO/releases/latest"
info() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
err()  { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || err "curl is required"

fam=""
[ -r /etc/os-release ] && . /etc/os-release
for t in "${ID:-}" ${ID_LIKE:-}; do
    case "$t" in
        fedora|rhel|centos|rocky|almalinux) fam=rpm; break ;;
        debian|ubuntu|linuxmint|pop) fam=deb; break ;;
        arch|manjaro|endeavouros|cachyos) fam=arch; break ;;
    esac
done

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

fetch() {
    suffix="$1"
    url="$(curl -fsSL "$API" \
        | tr ',' '\n' | grep '"browser_download_url"' | cut -d'"' -f4 \
        | grep -- "$suffix\$" | head -1)"
    [ -n "$url" ] || err "no asset ending in '$suffix' in the latest release"
    out="$tmp/${url##*/}"
    info "downloading ${url##*/}"
    curl -fL --progress-bar "$url" -o "$out" || err "download failed: $url"
    printf '%s' "$out"
}

case "$fam" in
    rpm)
        pkg="$(fetch .noarch.rpm)"
        info "installing with dnf"
        $SUDO dnf install -y "$pkg" ;;
    deb)
        pkg="$(fetch _all.deb)"
        info "installing with apt"
        $SUDO apt-get update -qq || true
        $SUDO apt-get install -y "$pkg" \
            || { $SUDO dpkg -i "$pkg"; $SUDO apt-get -f install -y; } ;;
    arch)
        pkg="$(fetch .pkg.tar.zst)"
        info "installing with pacman"
        $SUDO pacman -U --noconfirm "$pkg" ;;
    *)
        info "unknown distro — installing the AppImage into ~/.local/bin"
        pkg="$(fetch .AppImage)"
        mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
        install -m 0755 "$pkg" "$HOME/.local/bin/zerotier-tray"
        cat > "$HOME/.local/share/applications/zerotier-tray.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ZeroTier Tray
Comment=Join networks and control ZeroTier from the system tray
Exec=$HOME/.local/bin/zerotier-tray
Icon=network-vpn
Terminal=false
Categories=Network;Utility;
DESKTOP
        info "the AppImage needs python3 + PySide6 on the system" ;;
esac

info "done — run it with:  zerotier-tray"
info "the tray needs zerotier-one installed and PySide6 on the system"
