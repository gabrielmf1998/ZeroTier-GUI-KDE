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
        self._live: set = set()
        self._generation = 0

    def configure(self, host: str, port: int, token: str) -> None:
        self.host, self.port, self.token = host, int(port), token

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
            if err != QNetworkReply.NoError and not raw:
                callback(False, None, reply.errorString())
                return
            if status and int(status) == 401:
                callback(False, None, "The service rejected the auth token.")
                return
            if status and int(status) >= 400:
                callback(False, None, f"HTTP {status}: {raw.decode(errors='replace')[:200]}")
                return
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                callback(False, None, "The service returned something that is not JSON.")
                return
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
        self._neigh: dict[str, str] = {}
        self._lan: list[tuple[str, str]] = []
        self._fw_cache: tuple | None = None
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

    def _compute_state(self) -> str:
        if self.installed and self.unit_state == "failed":
            return "error"
        if not self.running:
            return "stopped"
        if not self.has_token:
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
        keys = {m["key"] for n in self.networks for m in self.members_of(n)}
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
        zone.
        """
        chosen = str(self.cfg.get("firewall_zone", "")).strip()
        if chosen:
            return chosen
        return system.firewall_default_zone()

    def firewall_open(self, max_age: float = 20.0) -> bool | None:
        """True/False if every ZeroTier port is open, None if unknown.

        Each answer costs a firewall-cmd per port, so it is cached: the menu is
        rebuilt whenever anything moves, and none of that should shell out.
        """
        zone = self.firewall_zone()
        ports = tuple(self.snapshot()["ports"])
        key = (zone, ports)
        now = time.monotonic()
        cached = self._fw_cache
        if cached and cached[0] == key and now - cached[1] < max_age:
            return cached[2]

        if not system.firewalld_running() or not zone or not ports:
            answer = None
        else:
            answer = all(system.firewall_port_open(zone, p) for p in ports)
        self._fw_cache = (key, now, answer)
        return answer

    def forget_firewall(self) -> None:
        """Drop the cache after we changed the firewall ourselves."""
        self._fw_cache = None


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
