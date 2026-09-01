"""Talking to the rest of the system: systemd, iproute2, firewalld, pkexec.

Everything read-only runs as the user. The four things that genuinely need
root - driving the unit, enabling it at boot, opening a firewall port and
handing this user a copy of the service auth token - go through one small
helper launched with pkexec.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

HELPER_NAME = "zerotier-tray-helper"


def helper_path() -> str:
    """The privileged helper, from the packaged location or this checkout."""
    here = Path(__file__).resolve().parent
    for cand in (Path("/usr/libexec") / HELPER_NAME,
                 Path("/usr/local/libexec") / HELPER_NAME,
                 here.parent / "helper" / HELPER_NAME):
        if cand.is_file():
            return str(cand)
    found = shutil.which(HELPER_NAME)
    return found or HELPER_NAME


def run(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Blocking run for one-shot reads. Never raises."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, "", "could not run " + " ".join(args[:1])


class AsyncRun(QObject):
    """One QProcess wrapped so callers get a single (code, out, err) callback.

    The instance keeps itself alive by parenting to whoever asked, and drops
    the reference once the callback has fired.
    """

    done = Signal(int, str, str)

    _live: set = set()

    def __init__(self, program: str, args: list[str], parent=None) -> None:
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.SeparateChannels)
        self._proc.finished.connect(self._finished)
        self._proc.errorOccurred.connect(self._error)
        self._program = program
        self._args = args
        self._fired = False

    def start(self, timeout_ms: int = 20000) -> None:
        AsyncRun._live.add(self)
        self._proc.start(self._program, self._args)
        if timeout_ms > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(timeout_ms, self._timeout)

    def _timeout(self) -> None:
        if not self._fired and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _error(self, _err) -> None:
        if self._proc.state() == QProcess.NotRunning:
            self._emit(127, "", self._proc.errorString())

    def _finished(self, code: int, _status) -> None:
        out = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        err = bytes(self._proc.readAllStandardError()).decode(errors="replace")
        self._emit(code, out.strip(), err.strip())

    def _emit(self, code: int, out: str, err: str) -> None:
        if self._fired:
            return
        self._fired = True
        self.done.emit(code, out, err)
        AsyncRun._live.discard(self)


def run_async(program: str, args: list[str], callback, parent=None,
              timeout_ms: int = 20000) -> AsyncRun:
    job = AsyncRun(program, args, parent)
    job.done.connect(callback)
    job.start(timeout_ms)
    return job


# --------------------------------------------------------------------------
# systemd
# --------------------------------------------------------------------------
def unit_exists(unit: str) -> bool:
    code, out, _ = run(["systemctl", "list-unit-files", unit], timeout=4)
    return code == 0 and unit.split(".")[0] in out


def unit_state(unit: str) -> tuple[str, str]:
    """(ActiveState, UnitFileState) - both "" when systemctl is unavailable."""
    code, out, _ = run(
        ["systemctl", "show", unit, "-p", "ActiveState", "-p", "UnitFileState",
         "--value"], timeout=4)
    if code != 0:
        return "", ""
    lines = out.splitlines()
    active = lines[0].strip() if len(lines) > 0 else ""
    enabled = lines[1].strip() if len(lines) > 1 else ""
    return active, enabled


# --------------------------------------------------------------------------
# iproute2 - the ARP/neighbour table, used to put an IP on a peer
# --------------------------------------------------------------------------
def neighbours(iface: str) -> dict[str, str]:
    """{lowercase MAC: IP} for one interface, from the kernel neighbour table."""
    out: dict[str, str] = {}
    if not iface:
        return out
    code, raw, _ = run(["ip", "-json", "neigh", "show", "dev", iface], timeout=4)
    if code != 0 or not raw:
        return out
    try:
        entries = json.loads(raw)
    except ValueError:
        return out
    for e in entries:
        mac, dst = e.get("lladdr"), e.get("dst")
        if not mac or not dst or e.get("state") in (["FAILED"], ["INCOMPLETE"]):
            continue
        out.setdefault(mac.lower(), dst)
    return out


# --------------------------------------------------------------------------
# firewalld
# --------------------------------------------------------------------------
def firewalld_running() -> bool:
    code, out, _ = run(["firewall-cmd", "--state"], timeout=4)
    return code == 0 and out.startswith("running")


def firewall_zones() -> list[str]:
    code, out, _ = run(["firewall-cmd", "--get-zones"], timeout=4)
    return sorted(out.split()) if code == 0 else []


def firewall_default_zone() -> str:
    _, out, _ = run(["firewall-cmd", "--get-default-zone"], timeout=4)
    return out


def firewall_zone_of(iface: str) -> str:
    if not iface:
        return ""
    _, out, _ = run(["firewall-cmd", "--get-zone-of-interface", iface], timeout=4)
    return out if out and "no zone" not in out else ""


def firewall_port_open(zone: str, port: int, proto: str = "udp") -> bool:
    code, out, _ = run(
        ["firewall-cmd", f"--zone={zone}", f"--query-port={port}/{proto}"], timeout=4)
    return code == 0 and out == "yes"


# --------------------------------------------------------------------------
# the privileged helper
# --------------------------------------------------------------------------
class Privileged(QObject):
    """Runs the helper under pkexec and reports (ok, message)."""

    result = Signal(bool, str)      # ok, message
    started = Signal(str)           # the command that went out

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, args: list[str]) -> bool:
        """Ask for one privileged action. False if one is already in flight."""
        if self._busy:
            return False
        self._busy = True
        self.started.emit(" ".join(args))
        env = os.environ.copy()
        job = AsyncRun("pkexec", [helper_path(), *args], self)
        # pkexec inherits the environment it is given; the polkit agent needs
        # the session bits that are already in ours.
        job._proc.setProcessEnvironment(_qt_env(env))
        job.done.connect(self._finished)
        job.start(180000)
        return True

    def _finished(self, code: int, out: str, err: str) -> None:
        self._busy = False
        if code == 0:
            self.result.emit(True, out)
        elif code == 126:
            self.result.emit(False, "")            # prompt dismissed
        elif code == 127:
            self.result.emit(False, err or "Authentication failed.")
        else:
            self.result.emit(False, err or out or f"The helper exited with {code}.")


def _qt_env(env: dict):
    from PySide6.QtCore import QProcessEnvironment
    qenv = QProcessEnvironment()
    for k, v in env.items():
        qenv.insert(k, v)
    return qenv
