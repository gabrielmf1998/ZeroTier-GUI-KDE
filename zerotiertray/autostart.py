"""Starting the tray icon itself when the user logs in (XDG autostart).

This is only about the icon. Starting the zerotier-one service at boot is a
different thing entirely and goes through systemd, in the helper.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .config import APP_ID, APP_NAME

AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / f"{APP_ID}.desktop"


def _exec_command() -> str:
    launcher = shutil.which(APP_ID)
    if launcher:
        return launcher
    return f"{sys.executable} -m zerotiertray"


DESKTOP = """\
[Desktop Entry]
Type=Application
Name={name}
Comment=Tray icon for ZeroTier One
Exec={exec}
Icon={icon}
Terminal=false
Categories=Network;Utility;
X-GNOME-Autostart-enabled=true
"""


def is_enabled() -> bool:
    return AUTOSTART_FILE.is_file()


def set_enabled(enabled: bool) -> bool:
    try:
        if enabled:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                DESKTOP.format(name=APP_NAME, exec=_exec_command(), icon=APP_ID),
                encoding="utf-8")
        elif AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
        return True
    except OSError:
        return False
