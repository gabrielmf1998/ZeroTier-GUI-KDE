#!/usr/bin/env bash
# Builds every package into dist/:  .rpm (Fedora), .deb (Debian/Ubuntu),
# .pkg.tar.zst (Arch) and an .AppImage (thin: uses the system python3+PySide6).
set -euo pipefail

NAME=zerotier-tray-kde
BIN=zerotier-tray
VERSION=1.0.0
RELEASE=1
MAINT="Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com>"
URL="https://github.com/gabrielmf1998/ZeroTier-Tray-KDE"
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
       "$SRCDIR/assets/gen_icons.py"
tar -C "$WORK" -czf "$WORK/$NAME-$VERSION.tar.gz" "$NAME-$VERSION"

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
Depends: python3, python3-pyside6.qtwidgets, python3-pyside6.qtnetwork, policykit-1 | polkitd, systemd, zerotier-one
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
    install -Dm 0644 "$ROOT/assets/$BIN-256.png" "$APPDIR/$BIN.png"
    install -Dm 0644 "$ROOT/assets/$BIN-256.png" \
        "$APPDIR/usr/share/icons/hicolor/256x256/apps/$BIN.png"
    install -Dm 0644 "$ROOT/packaging/$BIN.desktop" "$APPDIR/$BIN.desktop"
    cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# ZeroTier Tray AppImage launcher: a thin wrapper around system python3+PySide6.
HERE="$(dirname "$(readlink -f "$0")")"
if ! python3 -c "import PySide6.QtWidgets" 2>/dev/null; then
    echo "ZeroTier Tray needs PySide6 installed on the system:" >&2
    echo "  Fedora: sudo dnf install python3-pyside6" >&2
    echo "  Debian/Ubuntu: sudo apt install python3-pyside6.qtwidgets" >&2
    echo "  Arch: sudo pacman -S pyside6" >&2
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
( cd "$DIST" && sha256sum ./*.rpm ./*.deb ./*.pkg.tar.zst ./*.AppImage > SHA256SUMS 2>/dev/null || true )
say "done. Artifacts in dist/:"
ls -1sh "$DIST"
