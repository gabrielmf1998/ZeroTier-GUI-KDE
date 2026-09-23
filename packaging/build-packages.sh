#!/usr/bin/env bash
# Builds every package into dist/:  .rpm (Fedora), .deb (Debian/Ubuntu), the
# zerotier-tray-kde-pyside6 .deb for the ones that package no PySide6,
# .pkg.tar.zst (Arch) and an .AppImage (the system's python3, and its PySide6
# when it has one - otherwise the copy inside).
set -euo pipefail

NAME=zerotier-tray-kde
BIN=zerotier-tray
VERSION=1.0.5
RELEASE=1
MAINT="Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com>"
URL="https://github.com/gabrielmf1998/ZeroTier-GUI-KDE"
SUMMARY="Unofficial tray icon to run and control ZeroTier One"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

rm -rf "$DIST"; mkdir -p "$DIST"

stage_tree() {
    local d="$1"
    install -d "$d/usr/share/$NAME/zerotiertray"
    install -m 0644 "$ROOT"/zerotiertray/*.py        "$d/usr/share/$NAME/zerotiertray/"
    install -Dm 0755 "$ROOT/packaging/$BIN"          "$d/usr/bin/$BIN"
    install -Dm 0755 "$ROOT/helper/$BIN-helper"      "$d/usr/libexec/$BIN-helper"
    install -Dm 0644 "$ROOT/polkit/io.github.gabrielmf1998.zerotiertray.policy" \
        "$d/usr/share/polkit-1/actions/io.github.gabrielmf1998.zerotiertray.policy"
    install -Dm 0644 "$ROOT/packaging/$BIN.desktop"  "$d/usr/share/applications/$BIN.desktop"
    install -Dm 0644 "$ROOT/systemd/$BIN.service"    "$d/usr/lib/systemd/user/$BIN.service"
    for s in 48 64 128 256 512; do
        install -Dm 0644 "$ROOT/assets/$BIN-${s}.png" \
            "$d/usr/share/icons/hicolor/${s}x${s}/apps/$BIN.png"
    done
    install -Dm 0644 "$ROOT/assets/$BIN.svg" \
        "$d/usr/share/icons/hicolor/scalable/apps/$BIN.svg"
    install -Dm 0644 "$ROOT/LICENSE"   "$d/usr/share/licenses/$NAME/LICENSE"
    install -Dm 0644 "$ROOT/README.md" "$d/usr/share/doc/$NAME/README.md"
}

# ── source tarball (for the RPM) ────────────────────────────
say "source tarball"
SRCDIR="$WORK/$NAME-$VERSION"; mkdir -p "$SRCDIR"
cp -r "$ROOT"/{zerotiertray,helper,polkit,assets,packaging,systemd,docs,LICENSE,README.md,install.sh,install-online.sh} "$SRCDIR/"
rm -rf "$SRCDIR/zerotiertray/__pycache__" "$SRCDIR/packaging/build-packages.sh" \
       "$SRCDIR/packaging/bundle-pyside6.py" "$SRCDIR/assets/gen_icons.py"
tar -C "$WORK" -czf "$WORK/$NAME-$VERSION.tar.gz" "$NAME-$VERSION"

# ── a PySide6 for distributions without one ─────────────────
# Ubuntu 24.04 and its derivatives, and Debian 12, package none. The official
# wheel, pinned and hash-checked, trimmed to what the tray uses; it goes into
# its own .deb and into the AppImage. See packaging/bundle-pyside6.py.
say "bundled PySide6"
PYSIDE="$WORK/pyside6"
python3 "$ROOT/packaging/bundle-pyside6.py" "$PYSIDE"
PYSIDE_VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/packaging/bundle-pyside6.py")"

# ── RPM ─────────────────────────────────────────────────────
if command -v rpmbuild >/dev/null; then
    say "RPM"
    TOP="$WORK/rpm"; mkdir -p "$TOP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    cp "$WORK/$NAME-$VERSION.tar.gz" "$TOP/SOURCES/"
    cp "$ROOT/packaging/fedora/$NAME.spec" "$TOP/SPECS/"
    rpmbuild --define "_topdir $TOP" -bb "$TOP/SPECS/$NAME.spec" >"$WORK/rpm.log" 2>&1 \
        || { tail -30 "$WORK/rpm.log"; exit 1; }
    find "$TOP/RPMS" -name '*.rpm' -exec cp {} "$DIST/" \;
fi

# ── DEB ─────────────────────────────────────────────────────
if command -v dpkg-deb >/dev/null; then
    say "DEB"
    DEB="$WORK/deb"; stage_tree "$DEB"
    mkdir -p "$DEB/DEBIAN"
    cat > "$DEB/DEBIAN/control" <<CONTROL
Package: $NAME
Version: $VERSION-$RELEASE
Architecture: all
Maintainer: $MAINT
Section: net
Priority: optional
Homepage: $URL
Depends: python3, python3-pyside6.qtwidgets | $NAME-pyside6, python3-pyside6.qtnetwork | $NAME-pyside6, policykit-1 | polkitd, systemd, zerotier-one
Recommends: firewalld, iproute2, iputils-ping
Description: $SUMMARY
 An unofficial, non-affiliated tray GUI for ZeroTier One. Not made, endorsed or
 supported by ZeroTier, Inc.; report bugs in the tray to its own project, never
 to them.
 .
 ZeroTier Tray puts ZeroTier One in the system tray. Join and leave networks,
 copy the address the controller gave you, watch who else on the network is
 reachable and how far away they are, flip the per-network switches, start the
 service, enable it at boot and open its UDP ports in firewalld - without ever
 opening a terminal.
 .
 Everything on screen comes from zerotier-one's own local API on 127.0.0.1.
 The four things that need root - the unit, the boot setting, the firewall and
 the one-time copy of the service auth token - go through a small polkit helper.
 .
 26 icon styles including ZeroTier's own logo, 21 animations, a colour and an
 animation per state, and a count badge for how many members are reachable.
CONTROL
    dpkg-deb --root-owner-group --build "$DEB" "$DIST/${NAME}_${VERSION}-${RELEASE}_all.deb" >/dev/null

    say "DEB (bundled PySide6 $PYSIDE_VERSION)"
    RT="$WORK/deb-pyside6"
    install -d "$RT/usr/lib/$NAME/pyside6" "$RT/usr/share/doc/$NAME-pyside6" "$RT/DEBIAN"
    cp -r "$PYSIDE/PySide6" "$PYSIDE/shiboken6" "$RT/usr/lib/$NAME/pyside6/"
    install -m 0644 "$PYSIDE/copyright" "$RT/usr/share/doc/$NAME-pyside6/copyright"
    cat > "$RT/DEBIAN/control" <<CONTROL
Package: $NAME-pyside6
Version: $PYSIDE_VERSION-1
Architecture: amd64
Maintainer: $MAINT
Installed-Size: $(du -sk "$RT/usr" | cut -f1)
Section: python
Priority: optional
Homepage: $URL
Depends: python3 (>= 3.9), libc6 (>= 2.34), libstdc++6, libgcc-s1, libglib2.0-0t64 | libglib2.0-0, libdbus-1-3, libgl1, libegl1, libfontconfig1, libfreetype6, libbrotli1, zlib1g, libzstd1, libgssapi-krb5-2, libxkbcommon0, libxkbcommon-x11-0, libx11-6, libx11-xcb1, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libwayland-client0, libwayland-cursor0
Recommends: libssl3t64 | libssl3
Description: PySide6 $PYSIDE_VERSION for ZeroTier Tray, where the distribution has none
 Ubuntu 24.04 and what is built on it (Kubuntu, KDE neon, Linux Mint 22,
 Pop!_OS 24.04), and Debian 12, do not package PySide6. This is the official
 Qt for Python $PYSIDE_VERSION wheel, unmodified, trimmed to what ZeroTier Tray
 uses: QtCore, QtGui, QtWidgets, QtNetwork and QtDBus, with the xcb and
 Wayland platform plugins.
 .
 It lives in /usr/lib/$NAME/pyside6, off Python's path. Only
 $NAME picks it up, and only when the distribution offers no PySide6
 of its own.
CONTROL
    dpkg-deb -Zxz --root-owner-group --build "$RT" \
        "$DIST/${NAME}-pyside6_${PYSIDE_VERSION}-1_amd64.deb" >/dev/null
fi

# ── Arch ────────────────────────────────────────────────────
say "Arch package"
PKG="$WORK/pkg"; stage_tree "$PKG"
SIZE=$(du -sb "$PKG" | cut -f1)
cat > "$PKG/.PKGINFO" <<PKGINFO
pkgname = $NAME
pkgbase = $NAME
pkgver = $VERSION-$RELEASE
pkgdesc = $SUMMARY
url = $URL
builddate = $(date +%s)
packager = $MAINT
size = $SIZE
arch = any
license = MIT
depend = python
depend = pyside6
depend = polkit
depend = systemd
depend = zerotier-one
optdepend = firewalld: open the ZeroTier ports from the tray
optdepend = iproute2: put an address on each member
optdepend = iputils: scan a network for members
PKGINFO
( cd "$PKG"
  TAROPTS=(--no-xattrs --no-fflags --uid 0 --gid 0 --uname root --gname root)
  LANG=C bsdtar "${TAROPTS[@]}" -czf .MTREE --format=mtree \
      --options='!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link' \
      .PKGINFO usr
  LANG=C bsdtar "${TAROPTS[@]}" -cf - .PKGINFO .MTREE usr |
      zstd -q -c -T0 -18 > "$DIST/$NAME-$VERSION-$RELEASE-any.pkg.tar.zst" )

# ── AppImage (thin: system python3 + PySide6) ───────────────
say "AppImage"
if AT="$(command -v appimagetool 2>/dev/null)"; then :; else
    AT="$WORK/appimagetool"
    curl -fsSL -o "$AT" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
        && chmod +x "$AT" || AT=""
fi
if [ -n "$AT" ]; then
    APPDIR="$WORK/AppDir"
    install -d "$APPDIR/usr/share/$NAME/zerotiertray"
    install -m 0644 "$ROOT"/zerotiertray/*.py "$APPDIR/usr/share/$NAME/zerotiertray/"
    install -Dm 0755 "$ROOT/helper/$BIN-helper" "$APPDIR/usr/libexec/$BIN-helper"
    install -d "$APPDIR/usr/lib/$NAME/pyside6"
    cp -r "$PYSIDE/PySide6" "$PYSIDE/shiboken6" "$PYSIDE/copyright" "$APPDIR/usr/lib/$NAME/pyside6/"
    install -Dm 0644 "$ROOT/assets/$BIN-256.png" "$APPDIR/$BIN.png"
    install -Dm 0644 "$ROOT/assets/$BIN-256.png" \
        "$APPDIR/usr/share/icons/hicolor/256x256/apps/$BIN.png"
    install -Dm 0644 "$ROOT/packaging/$BIN.desktop" "$APPDIR/$BIN.desktop"
    cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# ZeroTier Tray AppImage launcher: the system's python3, and its PySide6 when
# it has one - otherwise the copy inside this image (Qt for Python, trimmed).
HERE="$(dirname "$(readlink -f "$0")")"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    echo "ZeroTier Tray needs python3, 3.9 or newer, on the system." >&2
    exit 1
fi
# The privileged helper cannot live inside the image: pkexec will only run a
# real file on disk that a polkit policy names. Point at the packaged one.
if [ ! -x /usr/libexec/zerotier-tray-helper ]; then
    echo "ZeroTier Tray: /usr/libexec/zerotier-tray-helper is missing, so" >&2
    echo "starting the service and opening the firewall will not work." >&2
    echo "Install the .rpm/.deb/pkg, or run install.sh from the repository." >&2
fi
if ! command -v zerotier-one >/dev/null 2>&1 && ! command -v zerotier-cli >/dev/null 2>&1; then
    echo "ZeroTier Tray: zerotier-one is not installed. There is nothing to" >&2
    echo "control without it:  curl -s https://install.zerotier.com | sudo bash" >&2
fi
export PYTHONPATH="$HERE/usr/share/zerotier-tray-kde${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m zerotiertray "$@"
APPRUN
    chmod +x "$APPDIR/AppRun"
    ARCH=x86_64 "$AT" --appimage-extract-and-run "$APPDIR" \
        "$DIST/ZeroTier-Tray-KDE-x86_64.AppImage" >"$WORK/appimage.log" 2>&1 \
        && say "AppImage built" || { say "AppImage build failed:"; tail -15 "$WORK/appimage.log"; }
else
    say "appimagetool unavailable — skipping AppImage"
fi

# ── checksums ───────────────────────────────────────────────
( cd "$DIST" && sha256sum ./*.rpm ./*_all.deb ./*_amd64.deb ./*.pkg.tar.zst ./*.AppImage > SHA256SUMS 2>/dev/null || true )
say "done. Artifacts in dist/:"
ls -1sh "$DIST"
