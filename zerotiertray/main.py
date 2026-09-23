"""Application entry point."""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import icons
from .config import APP_ID, APP_NAME, Config
from .tray import ZeroTierTray
from .zt import ZeroTierMonitor


# Passed by the autostart entry and the user unit: a login start that finds
# the tray already up just leaves. Anything else is a person asking for it.
AUTOSTART_FLAG = "--autostart"


def _already_running(server_name: str, message: bytes) -> bool:
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(250):
        if message:
            probe.write(message)
            probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return True
    QLocalServer.removeServer(server_name)
    return False


def _listen_for_peers(guard: QLocalServer, tray: ZeroTierTray) -> None:
    """A second launch hands over to this one and opens its window.

    Without it, starting the app from the menu while it ran did nothing at
    all - and with the icon hidden while stopped, or no menu on a Wayland left
    click, that left no way back in.
    """
    def read(sock: QLocalSocket) -> None:
        if b"show" in bytes(sock.readAll()):
            tray.open_settings()

    def accept() -> None:
        while guard.hasPendingConnections():
            sock = guard.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: read(s))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                read(sock)

    guard.newConnection.connect(accept)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)

    autostarted = AUTOSTART_FLAG in argv
    if _already_running(APP_ID, b"" if autostarted else b"show\n"):
        print(f"{APP_NAME} is already running"
              + ("." if autostarted else "; opened its window."), file=sys.stderr)
        return 0
    guard = QLocalServer()
    guard.listen(APP_ID)

    cfg = Config()
    monitor = ZeroTierMonitor(cfg)
    tray = ZeroTierTray(cfg, monitor)
    _listen_for_peers(guard, tray)
    tray.show()
    monitor.start()

    # Not having a tray *yet* is normal: a panel registers its
    # StatusNotifierWatcher on the session bus some time after login, and an
    # app started from autostart routinely wins that race. Refusing to run is
    # the wrong answer - wait for it instead, and stay useful meanwhile.
    if not QSystemTrayIcon.isSystemTrayAvailable():
        tray.wait_for_tray()

    if cfg.get("start_service_with_tray", False) and not monitor.running:
        QTimer.singleShot(800, tray.start_service)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.start(400)
    wake.timeout.connect(lambda: None)

    app.aboutToQuit.connect(monitor.shutdown)
    return app.exec()
