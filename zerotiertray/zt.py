"""The ZeroTier model: the local JSON API, the unit, and who else is on a network.

zerotier-one exposes everything this tray shows on http://127.0.0.1:9993, gated
by a token in /var/lib/zerotier-one/authtoken.secret. That file is root-only, so
the first run asks - once - for a readable copy in the user's config directory.
After that, joining, leaving, per-network flags and the whole live view need no
password at all; only the unit itself and the firewall still do.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import system
from .config import TOKEN_FILE

DEFAULT_PORT = 9993

# What zerotier-one reports in a network's "status" field.
NETWORK_STATES = {
    "OK": "connected",
    "REQUESTING_CONFIGURATION": "joining",
    "ACCESS_DENIED": "denied",
    "NOT_FOUND": "notfound",
    "AUTHENTICATION_REQUIRED": "denied",
    "PORT_ERROR": "error",
    "CLIENT_TOO_OLD": "error",
}

# The per-network switches zerotier-cli calls "set <nwid> allowX=".
NETWORK_FLAGS = [
    ("allowManaged", "Allow managed IPs",
     "Let the controller assign this machine an IP on the network."),
    ("allowGlobal", "Allow assigning public IPs",
     "Let the controller hand out addresses outside the private ranges."),
    ("allowDefault", "Allow default route override",
     "Let this network become the default route - all traffic goes through it."),
    ("allowDNS", "Allow DNS configuration",
     "Let the controller set DNS servers for its own search domain."),
]

NWID_LEN = 16
NODE_LEN = 10


def valid_nwid(nwid: str) -> bool:
    nwid = nwid.strip().lower()
    return len(nwid) == NWID_LEN and all(c in "0123456789abcdef" for c in nwid)


def mac_for(nwid: str, node: str) -> str:
    """The MAC zerotier-one gives a node on a network.

    Deterministic, from ZeroTier's MAC::fromAddress: the low byte of the
    network ID seeds the first octet, the node's 40-bit address fills the rest,
    and the remaining network ID bytes are XORed over the top.
    """
    try:
        n = int(nwid, 16)
        a = int(node, 16)
    except ValueError:
        return ""
    first = (n & 0xFE) | 0x02          # locally administered, not multicast
    if first == 0x52:                  # 52: is KVM/libvirt territory upstream
        first = 0x32
    m = (first << 40) | (a & 0xFFFFFFFFFF)
    m ^= ((n >> 8) & 0xFF) << 32
    m ^= ((n >> 16) & 0xFF) << 24
    m ^= ((n >> 24) & 0xFF) << 16
    m ^= ((n >> 32) & 0xFF) << 8
    m ^= (n >> 40) & 0xFF
    return ":".join(f"{(m >> s) & 0xFF:02x}" for s in (40, 32, 24, 16, 8, 0))


def find_token(home_dir: Path) -> tuple[str, str]:
    """(token, where it came from). Empty token means we have no access."""
    candidates = [
        (TOKEN_FILE, "your config directory"),
        (home_dir / "authtoken.secret", "the service directory"),
        (Path.home() / ".zeroTierOneAuthToken", "~/.zeroTierOneAuthToken"),
    ]
    for path, label in candidates:
        try:
            tok = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if tok:
            return tok, label
    return "", ""


def find_port(home_dir: Path, override: int = 0) -> int:
    if override:
        return int(override)
    try:
        return int((home_dir / "zerotier-one.port").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return DEFAULT_PORT


def local_settings(home_dir: Path) -> dict:
    """The "settings" block of local.conf, or {} when there is none to read."""
    try:
        data = json.loads((home_dir / "local.conf").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    settings = data.get("settings") if isinstance(data, dict) else None
    return settings if isinstance(settings, dict) else {}


def _valid_port(value) -> bool:
    return isinstance(value, int) and 0 < value < 65536


def stable_ports(primary: int, local: dict) -> list[int]:
    """The UDP ports worth opening in a firewall: the ones that survive a restart.

    zerotier-one draws its secondary and tertiary ports at random every time it
    starts (OneService.cpp: "Secondary ports are now randomized on startup"),
    unless local.conf pins them. Opening one of those permanently only leaves a
    dead hole behind after the next restart, and a new one gets opened beside
    it. The primary port is the one that matters for inbound, and the one
    ZeroTier's own docs say to open. The helper applies the same rule.
    """
    ports = [primary if _valid_port(primary) else DEFAULT_PORT]
    for key in ("secondaryPort", "tertiaryPort"):
        value = local.get(key)
        if _valid_port(value) and value not in ports:
            ports.append(value)
    return ports


def parse_port_specs(text: str) -> list[tuple[int, int, str]]:
    """firewall-cmd --list-ports output -> [(low, high, protocol)]."""
    specs = []
    for item in (text or "").split():
        port, _, proto = item.partition("/")
        low, _, high = port.partition("-")
        try:
            a = int(low)
            b = int(high) if high else a
        except ValueError:
            continue
        specs.append((a, b, proto))
    return specs


def _covers(specs: list[tuple[int, int, str]], port: int, proto: str = "udp") -> bool:
    return any(a <= port <= b and p == proto for a, b, p in specs)


# --------------------------------------------------------------------------
# the local JSON API
# --------------------------------------------------------------------------
class LocalApi(QObject):
    """Async client for zerotier-one's control plane on localhost."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self.host = "127.0.0.1"
        self.port = DEFAULT_PORT
        self.token = ""
        self.rejected = False            # the daemon answered 401 to this token
        self._live: set = set()
        self._generation = 0

    def configure(self, host: str, port: int, token: str) -> None:
        self.host, self.port, self.token = host, int(port), token
        self.rejected = False

    @property
    def ready(self) -> bool:
        return bool(self.token)

    def _request(self, path: str) -> QNetworkRequest:
        req = QNetworkRequest(QUrl(f"http://{self.host}:{self.port}{path}"))
        req.setRawHeader(b"X-ZT1-Auth", self.token.encode())
        req.setRawHeader(b"Content-Type", b"application/json")
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.ManualRedirectPolicy)
        req.setTransferTimeout(6000)
        return req

    def _dispatch(self, reply: QNetworkReply, callback) -> None:
        generation = self._generation
        self._live.add(reply)

        def finished() -> None:
            self._live.discard(reply)
            err = reply.error()
            raw = bytes(reply.readAll())
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            reply.deleteLater()
            if generation != self._generation:
                return                       # abandoned by quiesce(); say nothing
            # Before the generic error: a 401 comes back with an empty body, so
            # checking for an error first swallowed it as "Host requires
            # authentication" and the tray spun in "Starting" for ever.
            if status and int(status) == 401:
                self.rejected = True
                callback(False, None, "The service rejected the auth token.")
                return
            if err != QNetworkReply.NoError and not raw:
                callback(False, None, reply.errorString())
                return
            if status and int(status) >= 400:
                callback(False, None, f"HTTP {status}: {raw.decode(errors='replace')[:200]}")
                return
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                callback(False, None, "The service returned something that is not JSON.")
                return
            self.rejected = False
            callback(True, data, "")

        reply.finished.connect(finished)

    def get(self, path: str, callback) -> None:
        if not self.ready:
            callback(False, None, "No auth token.")
            return
        self._dispatch(self._nam.get(self._request(path)), callback)

    def post(self, path: str, body: dict, callback) -> None:
        if not self.ready:
            callback(False, None, "No auth token.")
            return
        payload = QByteArray(json.dumps(body or {}).encode())
        self._dispatch(self._nam.post(self._request(path), payload), callback)

    def delete(self, path: str, callback) -> None:
        if not self.ready:
            callback(False, None, "No auth token.")
            return
        self._dispatch(self._nam.deleteResource(self._request(path)), callback)

    def quiesce(self) -> None:
        """Let go of the daemon completely: in-flight requests and sockets.

        zerotier-one sometimes dies with SIGSEGV while being stopped, in its
        control-plane HTTP server's thread pool. It is intermittent and its
        trigger was never pinned down, so this is precaution rather than a
        proven cure: do not hold a connection open to something that is about
        to get SIGTERM. Keep-alive means dropping the timers is not enough on
        its own, hence the connection cache. Replies that land afterwards are
        discarded instead of being reported as errors.
        """
        self._generation += 1
        for reply in list(self._live):
            reply.abort()
        self._live.clear()
        self._nam.clearConnectionCache()


# --------------------------------------------------------------------------
# firewalld, asked in the background
# --------------------------------------------------------------------------
class FirewallWatch(QObject):
    """What firewalld says, fetched off the GUI thread and kept for a while.

    firewall-cmd is a Python program talking D-Bus, a fifth of a second a call.
    Asking it synchronously - every time the menu opened, on every poll while
    the settings window was up - is what made both of them stall. Nothing here
    blocks: a caller gets the last answer, or None while there is none yet, and
    a refresh is queued behind it. `changed` fires when an answer lands.
    """

    changed = Signal()
    MAX_AGE = 20.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.running: bool | None = None       # None: not asked yet
        self.default_zone = ""
        self.zones: list[str] = []
        self._ports: dict[str, list[tuple[int, int, str]]] = {}
        self._asked: dict[str, float] = {}     # zone -> when it was last queued
        self._checked = 0.0                    # when running/default were read
        self._queue: list[str] = []
        self._busy = False

    # -------------------------------------------------------------- answers
    def open_ports(self, zone: str, ports: list[int]) -> list[int] | None:
        """Which of `ports` are open in `zone` (default zone if ""), or None."""
        zone = zone or self.default_zone
        if not zone:
            self._kick("")
            return None
        if time.monotonic() - self._asked.get(zone, -1e9) > self.MAX_AGE:
            self._kick(zone)
        specs = self._ports.get(zone)
        if self.running is not True or specs is None or not ports:
            return None
        return [p for p in ports if _covers(specs, p)]

    def all_open(self, zone: str, ports: list[int]) -> bool | None:
        found = self.open_ports(zone, ports)
        return None if found is None else len(found) == len(ports)

    def forget(self) -> None:
        """Drop every answer, after we changed the firewall ourselves."""
        self._ports.clear()
        self._asked.clear()
        self._checked = 0.0
        self._kick("")

    # -------------------------------------------------------------- asking
    def _kick(self, zone: str) -> None:
        now = time.monotonic()
        if zone:
            self._asked[zone] = now
            if zone not in self._queue:
                self._queue.append(zone)
        if self._busy:
            return
        if self.running is None or now - self._checked > self.MAX_AGE:
            self._busy = True
            self._checked = now
            system.run_async("firewall-cmd", ["--get-default-zone"],
                             self._got_default, self, 8000)
        elif self.running is False:
            self._queue.clear()                # nothing to ask until it is back
        elif self._queue:
            self._busy = True
            self._next()

    def _got_default(self, code: int, out: str, _err: str) -> None:
        if code != 0 or not out.strip():
            self.running = False
            self.default_zone = ""
            self._queue.clear()
            self._finish()
            return
        self.running = True
        self.default_zone = out.strip()
        if self.default_zone not in self._ports and self.default_zone not in self._queue:
            self._asked[self.default_zone] = time.monotonic()
            self._queue.append(self.default_zone)
        if self.zones:
            self._next()
        else:
            system.run_async("firewall-cmd", ["--get-zones"], self._got_zones, self, 8000)

    def _got_zones(self, code: int, out: str, _err: str) -> None:
        if code == 0:
            self.zones = sorted(out.split())
        self._next()

    def _next(self) -> None:
        if not self._queue:
            self._finish()
            return
        zone = self._queue.pop(0)
        system.run_async("firewall-cmd", [f"--zone={zone}", "--list-ports"],
                         lambda code, out, _e, z=zone: self._got_ports(z, code, out),
                         self, 8000)

    def _got_ports(self, zone: str, code: int, out: str) -> None:
        if code == 0:
            self._ports[zone] = parse_port_specs(out)
        else:
            self._ports.pop(zone, None)
        self._next()

    def _finish(self) -> None:
        self._busy = False
        self.changed.emit()
        if self._queue:                        # asked for more while we were out
            self._kick("")


# --------------------------------------------------------------------------
# pinging a subnet, off the GUI thread
# --------------------------------------------------------------------------
class SubnetScan(QObject):
    """scan_subnet() in a worker thread. It pings up to 254 hosts and waits a
    second for each batch, which froze the whole tray - menu included - for
    several seconds when it ran on the GUI thread."""

    finished = Signal(int, str)       # addresses probed, what was scanned
    _done = Signal(int, str)          # from the worker; crosses to the GUI thread

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        # Bound to a method of this QObject, so Qt queues it onto the thread
        # the scanner lives in; `finished` is then only ever emitted there.
        self._done.connect(self._finish)

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, networks: list[dict]) -> bool:
        """False if a scan is already running."""
        if self._busy:
            return False
        self._busy = True
        nets = [dict(n) for n in networks]
        label = ", ".join(n.get("name") or n.get("nwid", "") for n in nets)
        threading.Thread(target=self._run, args=(nets, label), daemon=True).start()
        return True

    def _run(self, nets: list[dict], label: str) -> None:
        total = 0
        for net in nets:
            try:
                total += scan_subnet(net)
            except Exception:                            # noqa: BLE001
                pass
        self._done.emit(total, label)

    def _finish(self, total: int, label: str) -> None:
        self._busy = False
        self.finished.emit(total, label)


# --------------------------------------------------------------------------
# the monitor
# --------------------------------------------------------------------------
class ZeroTierMonitor(QObject):
    """Polls the unit and the local API, and keeps one snapshot of the world."""

    changed = Signal()                  # anything at all moved
    stateChanged = Signal(str, str)     # old, new
    networksChanged = Signal()
    membersChanged = Signal()
    actionFailed = Signal(str, str)     # title, detail

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.api = LocalApi(self)
        self.fw = FirewallWatch(self)
        self.fw.changed.connect(self.changed)
        self.scanner = SubnetScan(self)
        self.scanner.finished.connect(lambda *_: self.poll_peers())

        self.unit = str(cfg.get("service_name", "zerotier-one.service"))
        self.installed = False
        self.running = False
        self.boot_enabled = False
        self.unit_state = ""

        self.token_source = ""
        self.api_ok = False
        self.api_error = ""

        self.status: dict = {}
        self.networks: list[dict] = []
        self.peers: list[dict] = []

        self._state = "stopped"
        self._member_keys: set[str] = set()
        self._members: dict[str, list[dict]] = {}
        self._neigh: dict[str, str] = {}
        self._lan: list[tuple[str, str]] = []
        self._local: tuple[float, dict] = (0.0, {})
        self._api_paused = False
        self._svc_seen = False
        self._first_pass = True

        self._svc_timer = QTimer(self)
        self._svc_timer.timeout.connect(self.poll_service)
        self._api_timer = QTimer(self)
        self._api_timer.timeout.connect(self.poll_status)
        self._peer_timer = QTimer(self)
        self._peer_timer.timeout.connect(self.poll_peers)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.reload_token()
        self.reload_intervals()
        self.poll_service()
        self.poll_status()
        self.poll_peers()

    def shutdown(self) -> None:
        for t in (self._svc_timer, self._api_timer, self._peer_timer):
            t.stop()
        self.api.quiesce()

    def reload_intervals(self) -> None:
        self.unit = str(self.cfg.get("service_name", "zerotier-one.service"))
        self._svc_timer.start(max(1000, int(self.cfg.get("service_poll_ms", 4000))))
        if self._api_paused:
            return
        self._api_timer.start(max(500, int(self.cfg.get("status_poll_ms", 2000))))
        self._peer_timer.start(max(1000, int(self.cfg.get("peer_poll_ms", 5000))))

    # ------------------------------------------------ getting out of the way
    def watch_privileged(self, priv) -> None:
        """Let go of the daemon around any action that takes the unit down."""
        priv.started.connect(self._priv_started)
        priv.result.connect(self._priv_finished)

    def _priv_started(self, command: str) -> None:
        if command.split(" ")[0] in ("stop", "restart"):
            self.pause_api()

    def _priv_finished(self, _ok: bool, _message: str) -> None:
        self.poll_service()
        if self._api_paused:
            QTimer.singleShot(2000, self.resume_api)

    def pause_api(self) -> None:
        """Stop touching the local API, and drop the connections we hold."""
        self._api_paused = True
        self._api_timer.stop()
        self._peer_timer.stop()
        self.api.quiesce()

    def resume_api(self) -> None:
        if not self._api_paused:
            return
        self._api_paused = False
        self._api_timer.start(max(500, int(self.cfg.get("status_poll_ms", 2000))))
        self._peer_timer.start(max(1000, int(self.cfg.get("peer_poll_ms", 5000))))
        self.poll_status()
        self.poll_peers()

    def reload_token(self) -> None:
        home = self.cfg.home_dir()
        token, source = find_token(home)
        self.token_source = source
        self.api.configure(str(self.cfg.get("api_host", "127.0.0.1")),
                           find_port(home, int(self.cfg.get("api_port", 0))),
                           token)

    @property
    def has_token(self) -> bool:
        return self.api.ready

    # ---------------------------------------------------------------- polling
    def poll_service(self) -> None:
        def got(code: int, out: str, _err: str) -> None:
            lines = out.splitlines()
            active = lines[0].strip() if lines else ""
            enabled = lines[1].strip() if len(lines) > 1 else ""
            self._svc_seen = True
            self.installed = bool(enabled) and enabled != "not-found"
            self.unit_state = active
            was_running = self.running
            self.running = active == "active"
            self.boot_enabled = enabled.startswith("enabled")
            if self.running != was_running:
                self.reload_token()
                self.poll_status()
            self._settle()

        system.run_async(
            "systemctl",
            ["show", self.unit, "-p", "ActiveState", "-p", "UnitFileState", "--value"],
            got, self, 8000)
        self.poll_lan()

    def poll_lan(self) -> None:
        """The address this machine uses to reach the internet, from the kernel.

        Not a lookup and not a guess: `ip route get` reports the source address
        the routing table would pick, which is the one ZeroTier binds on.
        """
        def got(_code: int, out: str, _err: str) -> None:
            found: list[tuple[str, str]] = []
            try:
                entries = json.loads(out) if out else []
            except ValueError:
                entries = []
            for entry in entries:
                src = entry.get("prefsrc")
                if src:
                    found.append((src, entry.get("dev", "")))
            if found != self._lan:
                self._lan = found
                self.changed.emit()

        system.run_async("ip", ["-json", "route", "get", "1.1.1.1"], got, self, 6000)

    def poll_status(self) -> None:
        if self._api_paused:
            return
        if self._svc_seen and not self.running:
            # No point knocking, and it narrows the window if somebody stops
            # the unit from a terminal while we are mid-poll.
            self.api_ok = False
            self.status = {}
            self.networks = []
            self._settle()
            return
        if not self.api.ready:
            self.api_ok = False
            self.api_error = "No readable auth token."
            self._settle()
            return

        def got_status(ok: bool, data, err: str) -> None:
            self.api_ok = bool(ok and isinstance(data, dict))
            self.api_error = "" if self.api_ok else err
            if self.api_ok:
                self.status = data
                self.api.get("/network", got_networks)
            else:
                self.status = {}
                self.networks = []
                self._settle()

        def got_networks(ok: bool, data, err: str) -> None:
            if ok and isinstance(data, list):
                before = {n.get("nwid", ""): n.get("status", "") for n in self.networks}
                self.networks = data
                after = {n.get("nwid", ""): n.get("status", "") for n in data}
                if before != after:
                    self.networksChanged.emit()
            else:
                self.api_error = err
            self._settle()

        self.api.get("/status", got_status)

    def poll_peers(self) -> None:
        if self._api_paused or not self.api.ready:
            return
        if self._svc_seen and not self.running:
            self.peers = []
            return

        def got(ok: bool, data, _err: str) -> None:
            if ok and isinstance(data, list):
                self.peers = data
            self._refresh_neighbours()

        self.api.get("/peer", got)

    def _refresh_neighbours(self) -> None:
        """Fill in each member's managed IP from the kernel neighbour table."""
        if not self.cfg.get("resolve_member_ips", True):
            self._neigh = {}
            self._settle()
            return
        ifaces = [n.get("portDeviceName", "") for n in self.networks
                  if n.get("portDeviceName")]
        table: dict[str, str] = {}
        for iface in ifaces:
            table.update(system.neighbours(iface))
        self._neigh = table
        self._settle()

    # ------------------------------------------------------------------ state
    def state(self) -> str:
        return self._state

    @property
    def token_rejected(self) -> bool:
        return self.api.rejected

    def _compute_state(self) -> str:
        if self.installed and self.unit_state == "failed":
            return "error"
        if not self.running:
            return "stopped"
        if not self.has_token or self.api.rejected:
            return "noauth"
        if not self.api_ok:
            return "starting"
        if not self.status.get("online", False):
            return "starting"
        if not self.networks:
            return "online"
        mapped = [NETWORK_STATES.get(n.get("status", ""), "error")
                  for n in self.networks]
        for want in ("connected", "joining", "denied", "notfound", "error"):
            if want in mapped:
                return want
        return "online"

    def _settle(self) -> None:
        new = self._compute_state()
        old = self._state
        if new != old:
            self._state = new
            if not self._first_pass:
                self.stateChanged.emit(old, new)
        # Worked out once per change, not once per caller: the menu, the
        # tooltip, the badge and the snapshot all ask, and they ask often.
        self._members = {n.get("nwid", ""): self._compute_members(n)
                         for n in self.networks if n.get("nwid")}
        keys = {m["key"] for ms in self._members.values() for m in ms}
        if keys != self._member_keys:
            self._member_keys = keys
            if not self._first_pass:
                self.membersChanged.emit()
        self._first_pass = False
        self.changed.emit()

    # ---------------------------------------------------------------- members
    def controller_of(self, nwid: str) -> str:
        return (nwid or "")[:NODE_LEN]

    def members_of(self, network: dict) -> list[dict]:
        cached = self._members.get(network.get("nwid", ""))
        if cached is not None:
            return cached
        return self._compute_members(network)

    def _compute_members(self, network: dict) -> list[dict]:
        """Everyone else zerotier-one can currently see on this network.

        The local API lists VL1 peers globally, not per network, so two things
        decide who belongs here: a MAC in the neighbour table that maps back to
        a peer proves it, and when only one network is joined every leaf peer
        must be on it. Roots and the network's own controller are dropped
        unless the user asked to see them.
        """
        nwid = network.get("nwid", "")
        if not nwid:
            return []
        controller = self.controller_of(nwid)
        me = self.status.get("address", "")
        show_roots = bool(self.cfg.get("show_roots", False))
        only_network = len(self.networks) == 1

        out: list[dict] = []
        for peer in self.peers:
            addr = peer.get("address", "")
            if not addr or addr == me:
                continue
            role = peer.get("role", "LEAF")
            is_root = role in ("PLANET", "MOON")
            if is_root and not show_roots:
                continue
            if addr == controller and not show_roots:
                continue

            mac = mac_for(nwid, addr)
            ip = self._neigh.get(mac, "")
            if not (ip or only_network or is_root or addr == controller):
                continue

            paths = [p for p in peer.get("paths", []) if p.get("active")]
            preferred = next((p for p in paths if p.get("preferred")), None)
            endpoint = (preferred or (paths[0] if paths else {})).get("address", "")
            latency = peer.get("latency", -1)
            out.append({
                "key": f"{nwid}:{addr}",
                "address": addr,
                "ip": ip,
                "mac": mac,
                "role": role,
                "controller": addr == controller,
                "latency": latency if isinstance(latency, int) else -1,
                "version": _peer_version(peer),
                "endpoint": endpoint,
                "direct": bool(paths) and not peer.get("tunneled", False),
                "reachable": bool(paths) and latency is not None and latency >= 0,
            })

        out.sort(key=lambda m: (not m["reachable"], m["ip"] == "",
                                _ip_key(m["ip"]), m["address"]))
        return out

    def all_members(self) -> list[dict]:
        seen, out = set(), []
        for net in self.networks:
            for m in self.members_of(net):
                if m["key"] in seen:
                    continue
                seen.add(m["key"])
                out.append(m)
        return out

    def member_count(self) -> int:
        return sum(1 for m in self.all_members() if m["reachable"])

    # ------------------------------------------------------------ addresses
    def my_addresses(self) -> dict:
        """Every address this machine answers on, grouped and deduplicated.

        The ZeroTier ones come from the networks, the public ones are the
        surface addresses the roots see us at (so no third party is asked),
        and the local one is whatever the routing table picks.
        """
        zerotier = []
        for net in self.networks:
            label = net.get("name") or net.get("nwid", "")
            for addr in net.get("assignedAddresses") or []:
                zerotier.append((addr, label))

        settings = (self.status.get("config") or {}).get("settings") or {}
        v4, v6 = [], []
        for surface in settings.get("surfaceAddresses") or []:
            host = surface.rsplit("/", 1)[0]
            (v6 if ":" in host else v4).append(host)

        return {
            "zerotier": zerotier,
            "public_v4": _dedupe(v4),
            "public_v6": _dedupe(v6),
            "lan": list(self._lan),
        }

    def my_ip(self) -> str:
        """The ZeroTier address, bare - what "my IP" means in this tray."""
        for net in self.networks:
            for addr in net.get("assignedAddresses") or []:
                return addr.split("/")[0]
        return ""

    def public_ip(self) -> str:
        v4 = self.my_addresses()["public_v4"]
        return v4[0] if v4 else ""

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        cfg = (self.status.get("config") or {}).get("settings") or {}
        return {
            "state": self._state,
            "installed": self.installed,
            "running": self.running,
            "unit_state": self.unit_state,
            "boot_enabled": self.boot_enabled,
            "has_token": self.has_token,
            "token_rejected": self.api.rejected,
            "token_source": self.token_source,
            "api_ok": self.api_ok,
            "api_error": self.api_error,
            "address": self.status.get("address", ""),
            "online": bool(self.status.get("online", False)),
            "version": self.status.get("version", ""),
            "tcp_relay": bool(self.status.get("tcpFallbackActive", False)),
            "ports": ports_from(cfg),
            "port_mapping": bool(cfg.get("portMappingEnabled", False)),
            "surface": list(cfg.get("surfaceAddresses") or []),
            "networks": list(self.networks),
            "member_count": self.member_count(),
            "network_count": len(self.networks),
        }

    # -------------------------------------------------------------- API acts
    def _after(self, callback):
        def done(ok: bool, _data, err: str) -> None:
            self.poll_status()
            self.poll_peers()
            callback(ok, err)
        return done

    def join(self, nwid: str, callback) -> None:
        nwid = nwid.strip().lower()
        if not valid_nwid(nwid):
            callback(False, "A network ID is 16 hexadecimal characters.")
            return
        self.api.post(f"/network/{nwid}", {}, self._after(callback))

    def leave(self, nwid: str, callback) -> None:
        nwid = nwid.strip().lower()
        if not valid_nwid(nwid):
            callback(False, "A network ID is 16 hexadecimal characters.")
            return
        self.api.delete(f"/network/{nwid}", self._after(callback))

    def set_flag(self, nwid: str, key: str, value: bool, callback) -> None:
        if not valid_nwid(nwid) or key not in [k for k, _, _ in NETWORK_FLAGS]:
            callback(False, "Unknown network setting.")
            return
        self.api.post(f"/network/{nwid}", {key: bool(value)}, self._after(callback))

    # ------------------------------------------------------------ firewall
    def firewall_zone(self) -> str:
        """The zone the ZeroTier ports should be opened in.

        The traffic arrives on the physical link, not on a zt interface, so the
        right zone is the one holding the real NIC - by default, the default
        zone. "" until firewalld has answered.
        """
        chosen = str(self.cfg.get("firewall_zone", "")).strip()
        return chosen or self.fw.default_zone

    def firewall_ports(self) -> list[int]:
        """The ports "Allow the ZeroTier ports" means: see stable_ports()."""
        settings = (self.status.get("config") or {}).get("settings") or {}
        primary = settings.get("primaryPort")
        if not _valid_port(primary):
            primary = find_port(self.cfg.home_dir())
        return stable_ports(primary, self._local_settings())

    def _local_settings(self) -> dict:
        stamp, data = self._local
        if time.monotonic() - stamp > 30.0:
            data = local_settings(self.cfg.home_dir())
            self._local = (time.monotonic(), data)
        return data

    def firewall_open(self) -> bool | None:
        """True/False if the ZeroTier ports are open, None if not known (yet).

        Never blocks: the answer comes from FirewallWatch's cache.
        """
        return self.fw.all_open(self.firewall_zone(), self.firewall_ports())

    def forget_firewall(self) -> None:
        """Ask again, after we changed the firewall ourselves."""
        self.fw.forget()


def ports_from(settings: dict) -> list[int]:
    ports = []
    for key in ("primaryPort", "secondaryPort", "tertiaryPort"):
        val = settings.get(key)
        if isinstance(val, int) and 0 < val < 65536:
            ports.append(val)
    return ports or [DEFAULT_PORT]


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _peer_version(peer: dict) -> str:
    v = peer.get("version", "")
    return "" if not v or v.startswith("-1") else v


def _ip_key(ip: str):
    try:
        return tuple(int(p) for p in ip.split("."))
    except (ValueError, AttributeError):
        return (999,)


def scan_subnet(network: dict, timeout: float = 2.0) -> int:
    """Ping every address in our own managed /24 so the ARP table fills up.

    ZeroTier will not tell a member who its neighbours are - that lives on the
    controller. A sweep of the subnet we were assigned is the ordinary way to
    find out, and it only ever touches the user's own virtual network.
    """
    iface = network.get("portDeviceName", "")
    targets: list[str] = []
    for addr in network.get("assignedAddresses") or []:
        try:
            net = ipaddress.ip_interface(addr).network
        except ValueError:
            continue
        if net.version != 4 or net.num_addresses > 512:
            continue
        mine = str(ipaddress.ip_interface(addr).ip)
        targets += [str(h) for h in net.hosts() if str(h) != mine]
    if not targets:
        return 0

    base = ["ping", "-c", "1", "-W", "1", "-n", "-q"]
    if iface:
        base += ["-I", iface]

    def ping(host: str) -> None:
        system.run(base + [host], timeout=timeout + 1.0)

    with ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(ping, targets))
    return len(targets)
