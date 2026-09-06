"""Asking GitHub whether there is a newer release of this tray.

Only ever when asked. There is no timer, no check at startup and no periodic
poll: the request happens when someone picks "Check for updates", and at no
other time. So in normal use nothing leaves the machine at all.

It is one unauthenticated GET against a public endpoint, and nothing about the
node, its networks or its peers goes with it. Nothing here installs anything
either - the packages come from dnf, pacman or apt, so the most this can do is
say a version is out and hand over the command that fetches it.
"""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .config import APP_NAME, PROJECT_URL

RELEASES_API = "https://api.github.com/repos/gabrielmf1998/ZeroTier-GUI-KDE/releases/latest"
RELEASES_PAGE = f"{PROJECT_URL}/releases/latest"
INSTALL_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/"
    "ZeroTier-GUI-KDE/main/install-online.sh | sh"
)


def parse_version(text: str) -> tuple[int, ...]:
    """"v1.2.3" or "1.2.3-1" -> (1, 2, 3). Anything unparsable sorts lowest."""
    numbers = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


class UpdateChecker(QObject):
    """One on-demand request to the releases endpoint of this project."""

    checked = Signal(bool, str)     # ok, message to show

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self.latest = ""
        self.page = RELEASES_PAGE
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def available(self) -> bool:
        return bool(self.latest) and is_newer(self.latest)

    def check(self) -> bool:
        """Ask now. False if a check is already in flight."""
        if self._busy:
            return False
        self._busy = True
        request = QNetworkRequest(QUrl(RELEASES_API))
        # GitHub rejects requests without one, and says so in plain text.
        request.setRawHeader(b"User-Agent", f"{APP_NAME}/{__version__}".encode())
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setTransferTimeout(15000)
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._finished(reply))
        return True

    def _finished(self, reply: QNetworkReply) -> None:
        self._busy = False
        raw = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        error = reply.errorString() if reply.error() != QNetworkReply.NoError else ""
        reply.deleteLater()

        if error and not raw:
            self.checked.emit(False, f"Could not reach GitHub: {error}")
            return
        if status and int(status) >= 400:
            self.checked.emit(False, f"GitHub answered HTTP {status}.")
            return
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.checked.emit(False, "GitHub returned something unreadable.")
            return

        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            self.checked.emit(False, "That release has no tag.")
            return

        self.latest = tag.lstrip("vV")
        self.page = str(data.get("html_url") or RELEASES_PAGE)
        if self.available:
            self.checked.emit(True, f"Version {self.latest} is out.")
        else:
            self.checked.emit(True, f"{__version__} is the latest release.")
