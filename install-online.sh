#!/bin/sh
# ZeroTier Tray — online installer. Puts zerotier-one in place first (a tray for
# a daemon that is not there is useless), then installs the matching package
# from the latest GitHub release. Unknown distros get the AppImage.
#
#   curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/ZeroTier-GUI-KDE/main/install-online.sh | sh
#
# Not affiliated with ZeroTier, Inc.
set -eu

REPO="gabrielmf1998/ZeroTier-GUI-KDE"
API="https://api.github.com/repos/$REPO/releases/latest"
ZT_INSTALLER="https://install.zerotier.com"
info() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
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

# ── zerotier-one first ──────────────────────────────────────
# Every package depends on it, so this has to succeed before anything else.
# Distro packages are preferred; ZeroTier's own signed installer is the
# fallback for the places that do not ship one.
zerotier_from_vendor() {
    warn "zerotier-one is not in your distribution's repositories."
    warn "Falling back to ZeroTier's own signed installer:"
    warn "  curl -s $ZT_INSTALLER | sudo bash"
    warn "It adds ZeroTier's repository and installs the daemon. It is their"
    warn "documented method; if you would rather not, press Ctrl-C, install"
    warn "zerotier-one yourself and run this again."
    curl -fsSL "$ZT_INSTALLER" > "$tmp/zt-install.sh" \
        || err "could not download $ZT_INSTALLER"
    $SUDO bash "$tmp/zt-install.sh" || err "ZeroTier's installer failed"
}

install_zerotier() {
    if command -v zerotier-cli >/dev/null 2>&1; then
        info "zerotier-one is already installed"
        return
    fi
    info "installing zerotier-one"
    case "$fam" in
        rpm)
            # Fedora carries it in RPM Fusion nonfree; not everyone has that.
            $SUDO dnf install -y zerotier-one 2>/dev/null \
                || zerotier_from_vendor ;;
        arch)
            $SUDO pacman -S --needed --noconfirm zerotier-one 2>/dev/null \
                || zerotier_from_vendor ;;
        *)
            # Debian and Ubuntu do not package it at all.
            zerotier_from_vendor ;;
    esac
    command -v zerotier-cli >/dev/null 2>&1 \
        || err "zerotier-one still is not installed; nothing to control"
}

install_zerotier

if command -v systemctl >/dev/null 2>&1; then
    info "starting zerotier-one and enabling it at boot"
    $SUDO systemctl enable --now zerotier-one 2>/dev/null \
        || warn "could not enable zerotier-one; start it from the tray"
fi

# ── the tray itself ─────────────────────────────────────────
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
        info "the AppImage needs python3 + PySide6 on the system"
        warn "the AppImage cannot ship the polkit helper, so starting the"
        warn "service and opening the firewall will not work from it" ;;
esac

info "done — run it with:  zerotier-tray"
info "first launch: use 'Grant access to ZeroTier' so it stops asking for a password"
