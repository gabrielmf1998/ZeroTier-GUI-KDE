"""Persistent configuration for ZeroTier Tray."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_NAME = "ZeroTier Tray"
APP_ID = "zerotier-tray"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"

# Where the helper drops a readable copy of the service auth token, so the
# tray can talk to the local API without asking for a password every poll.
TOKEN_FILE = CONFIG_DIR / "authtoken.secret"

# ZeroTier's own brand orange, straight from artwork/logo.html upstream.
ZT_ORANGE = "#ffb354"

# The states an icon can be painted in.  Everything that is configurable
# per-state (colour, animation) uses exactly these keys.
STATES = [
    ("stopped", "Stopped"),                 # zerotier-one is not running
    ("starting", "Starting"),               # running, no root replied yet
    ("online", "Online, no networks"),      # node is online, nothing joined
    ("joining", "Joining a network"),       # waiting for the controller
    ("connected", "Connected"),             # at least one network is OK
    ("denied", "Access denied"),            # controller has not authorised us
    ("notfound", "Network not found"),      # bad network ID
    ("noauth", "No access to the service"), # no usable auth token
    ("error", "Service failed"),
]

STATE_KEYS = [k for k, _ in STATES]

# Colour presets, offered in the settings dialog.
COLOR_PRESETS: dict[str, dict[str, str]] = {
    "ZeroTier (default)": {
        "stopped": "#8b949e", "starting": "#e3b341", "online": "#58a6ff",
        "joining": "#e3b341", "connected": ZT_ORANGE, "denied": "#f0883e",
        "notfound": "#f85149", "noauth": "#a371f7", "error": "#f85149",
    },
    "Traffic light": {
        "stopped": "#8b949e", "starting": "#e3b341", "online": "#58a6ff",
        "joining": "#e3b341", "connected": "#3fb950", "denied": "#f0883e",
        "notfound": "#f85149", "noauth": "#a371f7", "error": "#f85149",
    },
    "Amber mono": {
        "stopped": "#6b5d52", "starting": "#c98f2f", "online": "#e0a94a",
        "joining": "#d9a03d", "connected": ZT_ORANGE, "denied": "#e07b39",
        "notfound": "#c94f4f", "noauth": "#b08968", "error": "#c94f4f",
    },
    "Cool grey": {
        "stopped": "#4d5566", "starting": "#9fb3c8", "online": "#bcd0e5",
        "joining": "#9fb3c8", "connected": "#e4f0fa", "denied": "#c8b88a",
        "notfound": "#d98a8a", "noauth": "#a8a0c0", "error": "#d98a8a",
    },
    "Neon": {
        "stopped": "#3a3a4a", "starting": "#ffcc00", "online": "#00e5ff",
        "joining": "#ffcc00", "connected": "#39ff14", "denied": "#ff9500",
        "notfound": "#ff1744", "noauth": "#c400ff", "error": "#ff1744",
    },
    "High contrast": {
        "stopped": "#808080", "starting": "#ffff00", "online": "#00ffff",
        "joining": "#ffff00", "connected": "#00ff00", "denied": "#ff8800",
        "notfound": "#ff0000", "noauth": "#ff00ff", "error": "#ff0000",
    },
}

DEFAULT_COLORS = dict(COLOR_PRESETS["ZeroTier (default)"])

DEFAULT_ANIMATIONS = {
    "stopped": "none",
    "starting": "spin",
    "online": "breathe",
    "joining": "pulse",
    "connected": "none",
    "denied": "blink",
    "notfound": "blink",
    "noauth": "breathe",
    "error": "flash",
}

DEFAULTS: dict = {
    # ---------------- appearance ----------------
    "icon_style": "zerotier",
    "colors": DEFAULT_COLORS,
    "animations": DEFAULT_ANIMATIONS,
    "animation_speed": 1.0,          # 0.25 .. 3.0
    "animation_fps": 20,             # 5 .. 60
    "animate_when_idle": True,       # keep animating in the resting states
    "icon_thickness": 1.0,           # 0.4 .. 2.5, stroke weight multiplier
    "icon_padding": 0.04,            # 0.0 .. 0.30, margin inside the icon
    "icon_scale": 1.12,              # 0.6 .. 1.6, how much of the cell to fill
    "monochrome": False,             # ignore per-state colour, paint one colour
    "monochrome_color": "#c9d1d9",
    "state_dot": True,               # corner dot when the style is brand-fixed

    # ---------------- count badge ----------------
    "show_badge": False,
    "badge_source": "members",       # members | networks
    "badge_style": "circle",         # circle | pill | plain | dot
    "badge_position": "br",          # tl | tr | bl | br
    "badge_when_zero": False,
    "badge_color": "#0d1117",
    "badge_text_color": "#ffffff",

    # ---------------- members ----------------
    "show_roots": False,             # list ZeroTier's own roots as members too
    "member_limit": 24,              # how many to put in the menu
    "resolve_member_ips": True,      # map peers to managed IPs via the ARP table

    # ---------------- behaviour ----------------
    "click_action": "menu",          # menu | settings | toggle | nothing
    "confirm_leave": True,
    "confirm_stop": True,
    "tooltip_details": True,
    "hide_when_stopped": False,
    "start_service_with_tray": False,

    # ---------------- notifications ----------------
    "notifications_enabled": True,   # master switch; off silences every one below
    "notify_on_service": True,       # started / stopped / failed
    "notify_on_network": True,       # a network came up or went down
    "notify_on_member": False,       # someone joined or left a network
    "notify_on_denied": True,        # controller refused us

    # ---------------- polling ----------------
    "status_poll_ms": 2000,
    "peer_poll_ms": 5000,
    "service_poll_ms": 4000,

    # ---------------- service plumbing ----------------
    "service_name": "zerotier-one.service",
    "home_dir": "/var/lib/zerotier-one",
    "api_host": "127.0.0.1",
    "api_port": 0,                   # 0 = read zerotier-one.port, else 9993

    # ---------------- firewall ----------------
    "firewall_zone": "",             # "" = the zone of the default route
}


def _merge(base: dict, incoming: dict) -> dict:
    """Shallow merge, but recurse one level into the nested dicts."""
    out = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


class Config:
    def __init__(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    # -- dict-ish access -------------------------------------------------
    def __getitem__(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def update(self, values: dict) -> None:
        self._data = _merge(self._data, values)

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def reset(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)

    # -- per-state helpers -----------------------------------------------
    def color_for(self, state: str) -> str:
        if self._data.get("monochrome"):
            return self._data.get("monochrome_color", "#c9d1d9")
        colors = self._data.get("colors") or {}
        return colors.get(state) or DEFAULT_COLORS.get(state, ZT_ORANGE)

    def animation_for(self, state: str) -> str:
        anims = self._data.get("animations") or {}
        return anims.get(state) or DEFAULT_ANIMATIONS.get(state, "none")

    # -- paths ------------------------------------------------------------
    def home_dir(self) -> Path:
        return Path(str(self.get("home_dir", "/var/lib/zerotier-one")))

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._data = _merge(DEFAULTS, raw)

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass
