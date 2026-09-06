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


def _already_running(server_name: str) -> bool:
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(250):
        probe.close()
        return True
    QLocalServer.removeServer(server_name)
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)

    if _already_running(APP_ID):
        print(f"{APP_NAME} is already running.", file=sys.stderr)
        return 0
    guard = QLocalServer()
    guard.listen(APP_ID)

    cfg = Config()
    monitor = ZeroTierMonitor(cfg)
    tray = ZeroTierTray(cfg, monitor)
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
