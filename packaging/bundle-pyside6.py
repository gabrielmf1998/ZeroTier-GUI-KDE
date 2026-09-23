#!/usr/bin/env python3
"""Stage a trimmed PySide6 for distributions that do not package one.

Ubuntu 24.04 and everything built on it - Kubuntu, KDE neon, Linux Mint 22,
Pop!_OS 24.04 - and Debian 12 have no PySide6 at all, so the tray had nothing
to run on there. This takes the official wheels from PyPI, a pinned version
checked against pinned hashes, and keeps only what the tray uses: QtCore,
QtGui, QtWidgets and QtNetwork, QtDBus for the tray icon, the Qt libraries
those need - worked out from the ELF NEEDED entries, not guessed - and the
plugins a desktop session loads.

    bundle-pyside6.py DEST

leaves DEST/PySide6, DEST/shiboken6 and DEST/copyright. The wheels' layout and
their $ORIGIN rpaths are kept as they are, so the tree is relocatable: it
becomes /usr/lib/zerotier-tray-kde/pyside6, or the same path in the AppImage.
Nothing in it is rebuilt or modified; files are only left out.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "6.10.3"
MINOR = VERSION.rsplit(".", 1)[0]

# manylinux_2_34: glibc 2.34 or newer - Ubuntu 22.04, Debian 12 and later.
_PYPI = "https://files.pythonhosted.org/packages"
WHEELS = {
    "pyside6_essentials-6.10.3-cp39-abi3-manylinux_2_34_x86_64.whl": (
        f"{_PYPI}/df/78/1b2bc96e720c17b35f8d6f54a13d32fbcd4cb8a8fc4a3dba8741711a3ab0/"
        "pyside6_essentials-6.10.3-cp39-abi3-manylinux_2_34_x86_64.whl",
        "5fb32b48010f513335a2166ac3df279c5996fb96402fd91a4875fc717fe55de0"),
    "shiboken6-6.10.3-cp39-abi3-manylinux_2_34_x86_64.whl": (
        f"{_PYPI}/1b/c5/cbc20e31c0d7c47adeb5a6557991ce0eaab7a0288ee3b6aa25edaf3215d7/"
        "shiboken6-6.10.3-cp39-abi3-manylinux_2_34_x86_64.whl",
        "99e8ac3319a2c93a4a8f8bb4512d71a9d43bc9f226f094a80a728d6dcc3e4826"),
}

MODULES = ("QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtDBus")

# What a desktop session loads. The rest of the wheel - QML, Quick, Designer,
# eglfs, printing, SQL, image formats nobody here reads, OpenGL integrations a
# widgets-only program never asks for - stays behind.
PLUGINS = {
    "platforms": ("libqxcb.so", "libqwayland.so", "libqoffscreen.so"),
    "platformthemes": ("libqxdgdesktopportal.so",),
    "platforminputcontexts": ("libcomposeplatforminputcontextplugin.so",
                              "libibusplatforminputcontextplugin.so"),
    "wayland-shell-integration": ("libxdg-shell.so",),
    "wayland-decoration-client": ("libbradient.so",),
    "tls": ("libqopensslbackend.so", "libqcertonlybackend.so"),
}

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "zerotier-tray-build"

COPYRIGHT = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: PySide6 (Qt for Python) {version}, with the Qt {version} libraries it ships
Source: https://download.qt.io/official_releases/QtForPython/pyside6/
Comment:
 Unmodified binaries from the official PyPI wheels, with the modules and
 plugins ZeroTier Tray does not use left out:
{wheels}
 Qt and Qt for Python sources: https://code.qt.io/cgit/qt/ and
 https://code.qt.io/cgit/pyside/pyside-setup.git (tag v{version}).

Files: *
Copyright: The Qt Company Ltd. and other contributors
License: LGPL-3.0-only or GPL-2.0-only or GPL-3.0-only
 On Debian systems the full texts are in /usr/share/common-licenses/LGPL-3,
 /usr/share/common-licenses/GPL-2 and /usr/share/common-licenses/GPL-3.

Files: PySide6/Qt/lib/libicu*
Copyright: Unicode, Inc. and others
License: Unicode-3.0
 https://www.unicode.org/license.txt
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str) -> Path:
    url, want = WHEELS[name]
    path = CACHE / name
    if not path.is_file() or sha256(path) != want:
        CACHE.mkdir(parents=True, exist_ok=True)
        part = path.with_name(name + ".part")
        with urllib.request.urlopen(url, timeout=300) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out)
        part.replace(path)
    if sha256(path) != want:
        sys.exit(f"{name}: checksum mismatch, refusing to use it")
    return path


def needed(lib: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", str(lib)], capture_output=True,
                         text=True, check=True).stdout
    return [line.split("[", 1)[1].rstrip("]")
            for line in out.splitlines() if "(NEEDED)" in line]


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dest = Path(sys.argv[1]).resolve()
    work = dest.with_name(dest.name + ".wheels")
    for d in (dest, work):
        shutil.rmtree(d, ignore_errors=True)
    for name in WHEELS:
        with zipfile.ZipFile(fetch(name)) as wheel:
            wheel.extractall(work)

    src = work / "PySide6"
    qtlib = src / "Qt" / "lib"
    keep: list[Path] = [src / "__init__.py", src / "_config.py",
                        src / "_git_pyside_version.py", src / "support",
                        src / f"libpyside6.abi3.so.{MINOR}"]
    keep += [src / f"{m}.abi3.so" for m in MODULES]
    for folder, names in PLUGINS.items():
        keep += [src / "Qt" / "plugins" / folder / n for n in names]
    missing = [p for p in keep if not p.exists()]
    if missing:
        sys.exit("not in the wheel: " + ", ".join(str(p.relative_to(work)) for p in missing))

    # The Qt libraries: whatever the kept binaries need, transitively.
    libs: set[str] = set()
    queue = [p for p in keep if p.suffix == ".so" or ".so." in p.name]
    while queue:
        for soname in needed(queue.pop()):
            if soname not in libs and (qtlib / soname).exists():
                libs.add(soname)
                queue.append(qtlib / soname)
    keep += [qtlib / s for s in sorted(libs)]

    for path in keep:
        target = dest / path.relative_to(work)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(path, target)

    # shiboken6: the runtime, without headers, CMake files or stubs.
    shiboken = dest / "shiboken6"
    shiboken.mkdir(parents=True, exist_ok=True)
    for path in (work / "shiboken6").iterdir():
        if path.name in ("include", "lib", "py.typed") or path.suffix == ".pyi":
            continue
        shutil.copy2(path, shiboken / path.name)

    # zipfile drops the mode bits; shared objects are mapped, not executed,
    # but make everything plainly readable rather than trusting the umask.
    for path in dest.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)

    wheels = "\n".join(f"   {n}\n     sha256 {WHEELS[n][1]}" for n in WHEELS)
    (dest / "copyright").write_text(COPYRIGHT.format(version=VERSION, wheels=wheels))
    shutil.rmtree(work)

    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"PySide6 {VERSION}: {len(libs)} Qt libraries, "
          f"{sum(len(v) for v in PLUGINS.values())} plugins, {size / 2**20:.0f} MiB -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
