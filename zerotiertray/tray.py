"""The tray icon itself: menu, tooltip, animation loop and notifications."""

from __future__ import annotations

import getpass
from dataclasses import astuple

from PySide6.QtCore import QObject, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import icons, system, updates, zt
from . import __version__
from .config import APP_NAME, CONFIG_FILE, PROJECT_URL, STATES

STATE_LABELS = dict(STATES)

NETWORK_STATUS_TEXT = {
    "OK": "connected",
    "REQUESTING_CONFIGURATION": "waiting for the controller",
    "ACCESS_DENIED": "access denied - not authorised yet",
    "NOT_FOUND": "no such network",
    "AUTHENTICATION_REQUIRED": "sign-in required",
    "PORT_ERROR": "the interface could not be created",
    "CLIENT_TOO_OLD": "this ZeroTier is too old for the network",
}


def fmt_latency(ms: int) -> str:
    return "no route yet" if ms is None or ms < 0 else f"{ms} ms"


def member_line(m: dict) -> str:
    bits = [m["ip"] or m["address"]]
    if m["ip"]:
        bits.append(m["address"])
    bits.append(fmt_latency(m["latency"]))
    if m["controller"]:
        bits.append("controller")
    elif m["role"] in ("PLANET", "MOON"):
        bits.append("root")
    elif not m["direct"]:
        bits.append("relayed")
    return "  ·  ".join(bits)


class ZeroTierTray(QObject):
    def __init__(self, cfg, monitor, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.monitor = monitor
        self.priv = system.Privileged(self)
        self.updates = updates.UpdateChecker(self)
        self.settings_dialog = None

        self.phase = 0.0
        self._pending = ""
        self._no_tray = False
        self._scan_mine = False

        # The menu is exported over D-Bus and drawn by the panel, which holds
        # on to item IDs. So it is only rebuilt when its *shape* changes, never
        # while it is open, and everything else - labels, ticks - is updated in
        # place through these callbacks.
        self._menu_structure_cache: tuple | None = None
        self._menu_open = False
        self._menu_dirty = False
        self._live_items: list = []
        # When is the menu open? The panel says when it opens it (AboutToShow,
        # for the menu and for each submenu), but under Plasma nothing ever
        # says it closed: KDE's exporter drops the "closed" event, so
        # aboutToHide never comes. So: open from any sign of use until a click,
        # an aboutToHide where there is one, or this long with no activity.
        self._menu_idle = QTimer(self)
        self._menu_idle.setSingleShot(True)
        self._menu_idle.setInterval(15000)
        self._menu_idle.timeout.connect(self._menu_closed)
        # A host that does send "closed" sends it *before* "clicked", and the
        # click has to find the item it was aimed at: a short grace period.
        self._menu_settle = QTimer(self)
        self._menu_settle.setSingleShot(True)
        self._menu_settle.setInterval(600)
        self._menu_settle.timeout.connect(self._rebuild_if_dirty)
        self._members_now: dict[str, dict] = {}

        # What was last handed to the panel, so an unchanged icon or tooltip is
        # not re-sent - each one is a D-Bus signal and a pixmap fetch.
        self._icon_key: tuple | None = None
        self._tooltip_text: str | None = None
        self._ctx_cache: icons.RenderCtx | None = None

        self.tray = QSystemTrayIcon(parent)
        self.tray.setIcon(icons.app_icon())
        self.tray.activated.connect(self._on_activated)

        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self._menu_about_to_show)
        self.menu.aboutToHide.connect(self._menu_about_to_hide)
        # Fires for a click anywhere in the menu, submenus included, however
        # it arrived - and a click is the one close every host reports.
        self.menu.triggered.connect(lambda _a: self._menu_about_to_hide())

        self.anim = QTimer(self)
        self.anim.timeout.connect(self._tick)

        monitor.changed.connect(self.refresh)
        monitor.stateChanged.connect(self._on_state)
        monitor.networksChanged.connect(self._on_networks)
        monitor.membersChanged.connect(self._on_members)
        self.priv.result.connect(self._on_priv)
        monitor.watch_privileged(self.priv)
        monitor.scanner.finished.connect(self._scan_done)

        self.apply_settings()
        self.rebuild_menu()
        self.refresh()

    # ------------------------------------------------------------------ setup
    def show(self) -> None:
        self.tray.show()

    # --------------------------------------------------- waiting for a panel
    def wait_for_tray(self) -> None:
        """Keep trying until somewhere to put the icon turns up.

        Qt answers isSystemTrayAvailable() from whatever is on the session bus
        at that instant, so an app that starts before the panel gets told there
        is no tray - on a desktop that certainly has one. Poll, say nothing for
        the first few seconds, and only explain if it really is not coming.
        """
        self._tray_wait_elapsed = 0
        self._tray_notice = None
        # With no tray, closing the settings window ends the program, or it
        # would sit there invisible. Not quitOnLastWindowClosed: that also
        # fired when the notice below was dismissed, so "Wait in the
        # background" quit instead of waiting.
        self._no_tray = True
        self._tray_timer = QTimer(self)
        self._tray_timer.timeout.connect(self._poll_for_tray)
        self._tray_timer.start(1000)

    def _poll_for_tray(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_timer.stop()
            self._no_tray = False
            self.tray.show()
            self.refresh()
            if self._tray_notice is not None:
                self._tray_notice.done(0)
                self._tray_notice = None
            return

        self._tray_wait_elapsed += 1
        if self._tray_wait_elapsed == 15:
            self._explain_no_tray()
        elif self._tray_wait_elapsed == 60:
            self._tray_timer.setInterval(5000)   # settle into a slow watch

    def _explain_no_tray(self) -> None:
        box = QMessageBox(QMessageBox.Information, APP_NAME, "")
        box.setWindowIcon(icons.app_icon())
        box.setTextFormat(Qt.RichText)
        box.setText(
            "<b>Nothing is offering a system tray yet.</b><br><br>"
            "A panel publishes one on the session bus when it starts, and this "
            "can simply be running ahead of it - at login that is routine. "
            "The icon will appear on its own the moment a tray shows up; "
            "nothing needs restarting.<br><br>"
            "If it never does, something has to provide one:<br>"
            "• KDE Plasma, Xfce, LXQt, Cinnamon and MATE all do<br>"
            "• GNOME needs the <i>AppIndicator and KStatusNotifierItem "
            "Support</i> extension<br>"
            "• Sway, Hyprland and i3 need a bar with a tray module, such as "
            "waybar; on X11, <tt>stalonetray</tt> or <tt>trayer</tt> also work"
        )
        open_window = box.addButton("Open the window instead",
                                    QMessageBox.AcceptRole)
        box.addButton("Wait in the background", QMessageBox.RejectRole)
        self._tray_notice = box
        box.exec()
        self._tray_notice = None
        if box.clickedButton() is open_window:
            self.open_settings()

    def apply_settings(self) -> None:
        fps = max(1, min(60, int(self.cfg.get("animation_fps", 20))))
        self.anim.setInterval(int(1000 / fps))
        self.monitor.reload_intervals()
        self._restart_anim_if_needed()
        self.refresh()

    def _current_animation(self) -> str:
        state = self.monitor.state()
        anim = self.cfg.animation_for(state)
        if anim == "none":
            return "none"
        if state in ("connected", "online", "stopped") and \
                not self.cfg.get("animate_when_idle", True):
            return "none"
        return anim

    def _restart_anim_if_needed(self) -> None:
        if self._current_animation() == "none":
            self.anim.stop()
            self.phase = 0.0
        elif not self.anim.isActive():
            self.anim.start()

    def _tick(self) -> None:
        fps = max(1, min(60, int(self.cfg.get("animation_fps", 20))))
        speed = max(0.1, min(4.0, float(self.cfg.get("animation_speed", 1.0))))
        self.phase = (self.phase + speed / (fps * 2.0)) % 1.0
        self._paint()

    # ------------------------------------------------------------------ paint
    def _ctx(self) -> icons.RenderCtx:
        """The render context, worked out once per change rather than per frame."""
        if self._ctx_cache is None:
            self._ctx_cache = self._build_ctx(self.monitor.snapshot())
        return self._ctx_cache

    def _build_ctx(self, snap: dict) -> icons.RenderCtx:
        style = str(self.cfg.get("icon_style", "zerotier"))
        source = str(self.cfg.get("badge_source", "members"))
        count = snap["member_count"] if source == "members" else snap["network_count"]

        badge = bool(self.cfg.get("show_badge", False))
        if count == 0 and not self.cfg.get("badge_when_zero", False):
            badge = False
        if not snap["running"]:
            badge = False

        dot = ""
        if style in icons.FIXED_BRAND_STYLES and self.cfg.get("state_dot", True):
            dot = self.cfg.color_for(snap["state"])

        return icons.RenderCtx(
            members=snap["member_count"],
            networks=snap["network_count"],
            thickness=float(self.cfg.get("icon_thickness", 1.0)),
            padding=float(self.cfg.get("icon_padding", 0.04)),
            scale=float(self.cfg.get("icon_scale", 1.12)),
            badge=badge,
            badge_text=str(count),
            badge_style=str(self.cfg.get("badge_style", "circle")),
            badge_position=str(self.cfg.get("badge_position", "br")),
            badge_color=str(self.cfg.get("badge_color", "#0d1117")),
            badge_text_color=str(self.cfg.get("badge_text_color", "#ffffff")),
            state_dot=dot,
        )

    def _paint(self) -> None:
        state = self.monitor.state()
        style = str(self.cfg.get("icon_style", "zerotier"))
        color = self.cfg.color_for(state)
        animation = self._current_animation()
        phase = self.phase if animation != "none" else 0.0
        ctx = self._ctx()
        # Every setIcon is a NewIcon signal and a full pixmap fetch by the
        # panel. Resting, the icon does not change, so do not say it did.
        key = (style, color, animation, phase, astuple(ctx))
        if key == self._icon_key:
            return
        self._icon_key = key
        self.tray.setIcon(icons.render_icon(style, color, animation, phase, ctx))

    def refresh(self) -> None:
        snap = self.monitor.snapshot()
        if self.cfg.get("hide_when_stopped", False) and snap["state"] == "stopped":
            self.tray.hide()
        elif not self.tray.isVisible():
            self.tray.show()
        self._ctx_cache = self._build_ctx(snap)
        self._restart_anim_if_needed()
        self._paint()
        tooltip = self._tooltip(snap)
        if tooltip != self._tooltip_text:
            self._tooltip_text = tooltip
            self.tray.setToolTip(tooltip)

        # Keep the exported menu in step with reality, not only with the last
        # time somebody opened it: a D-Bus tray hands the panel whatever layout
        # it was given, and a stale one says "not installed" for ever. But a
        # rebuild replaces every item ID, so while the panel is showing the
        # menu it waits - rebuilding under it is what froze it and ate clicks.
        if self._menu_structure(snap) != self._menu_structure_cache:
            if self._menu_open or self._menu_settle.isActive():
                self._menu_dirty = True
                self._update_menu(snap)
            else:
                self.rebuild_menu()
        else:
            self._update_menu(snap)

    def _member_limit(self) -> int:
        return max(1, int(self.cfg.get("member_limit", 24)))

    def _menu_structure(self, snap: dict) -> tuple:
        """What decides which entries the menu has - not what they say.

        Latency, reachability, a member's IP, the state line and every tick
        change all the time; they are updated in place by _update_menu(), which
        keeps the item IDs. Only a change in here means a rebuild.
        """
        addrs = self.monitor.my_addresses()
        nets = []
        for n in snap["networks"]:
            nets.append((
                n.get("nwid"), n.get("name"), n.get("status"),
                tuple(n.get("assignedAddresses") or []),
                n.get("portDeviceName"), n.get("mtu"), n.get("type"),
                tuple(sorted(m["key"] for m in self.monitor.members_of(n))),
            ))
        firewall_known = snap["installed"] and self.monitor.firewall_open() is not None
        return (
            self.updates.available,
            snap["installed"], snap["running"], snap["has_token"],
            snap["state"] == "noauth", snap["token_rejected"],
            bool(snap["address"]),
            tuple(nets),
            tuple(addrs["zerotier"]), tuple(addrs["public_v4"]),
            tuple(addrs["public_v6"]), tuple(addrs["lan"]),
            firewall_known,
            bool(self.cfg.get("show_addresses_in_menu", True)),
            self._member_limit(),
        )

    def _menu_about_to_show(self) -> None:
        """Never a rebuild here.

        By the time this runs the panel has already decided to show the layout
        it holds - KDE's exporter answers AboutToShow with "nothing changed" -
        so rebuilding now swaps every item ID out from under the open menu:
        submenus that will not load, clicks that land nowhere. 1.0.3 rebuilt
        here on every open. The exported menu is kept current while it is
        closed instead, so there is nothing to catch up on.
        """
        self._menu_activity()
        self._update_menu(self.monitor.snapshot())

    def _menu_activity(self) -> None:
        self._menu_open = True
        self._menu_settle.stop()
        self._menu_idle.start()

    def _menu_about_to_hide(self) -> None:
        self._menu_idle.stop()
        self._menu_open = False
        self._menu_settle.start()

    def _menu_closed(self) -> None:
        self._menu_open = False
        self._rebuild_if_dirty()

    def _rebuild_if_dirty(self) -> None:
        if not self._menu_dirty or self._menu_open or self._menu_settle.isActive():
            return
        if QApplication.activeModalWidget() is not None:
            # A confirmation opened from a menu click is still up, and that
            # click's action is on the stack underneath it: wait it out.
            self._menu_settle.start()
            return
        self.rebuild_menu()

    def _submenu(self, parent: QMenu, title: str) -> QMenu:
        """addMenu(), plus: opening it counts as the menu being in use."""
        sub = parent.addMenu(title)
        sub.aboutToShow.connect(self._menu_activity)
        return sub

    def _update_menu(self, snap: dict) -> None:
        self._members_now = {m["key"]: m for m in self.monitor.all_members()}
        for update in self._live_items:
            update(snap)

    def _live(self, menu: QMenu, update, checkable: bool = False) -> QAction:
        """An entry whose text or tick follows the live state.

        update(action, snap) sets it; it runs now and on every refresh.
        """
        act = QAction(menu)
        act.setCheckable(checkable)
        menu.addAction(act)
        self._live_items.append(lambda snap, a=act: update(a, snap))
        return act

    def _clear_menu(self) -> None:
        # QMenu.clear() deletes the actions but not the submenus addMenu()
        # parented to this menu, so every rebuild used to leak all of them.
        # deleteLater, because this can run from inside one of their signals.
        m = self.menu
        for sub in m.findChildren(QMenu, "", Qt.FindDirectChildrenOnly):
            sub.deleteLater()
        for action in m.actions():
            m.removeAction(action)
            action.deleteLater()

    def _tooltip(self, snap: dict) -> str:
        lines = [f"{APP_NAME} - {STATE_LABELS.get(snap['state'], snap['state'])}"]
        if not self.cfg.get("tooltip_details", True):
            return lines[0]

        if snap["state"] == "noauth":
            lines.append("ZeroTier rejected the saved token - grant access again "
                         "from the menu." if snap["token_rejected"] else
                         "Grant access to the service from the menu to see anything.")
            return "\n".join(lines)
        if not snap["running"]:
            lines.append("zerotier-one is not running." if snap["installed"]
                         else "zerotier-one is not installed.")
            return "\n".join(lines)

        if snap["address"]:
            lines.append(f"Node {snap['address']}  ·  v{snap['version']}")
        if not snap["online"]:
            lines.append("No root server has answered yet.")
        for net in snap["networks"]:
            ips = ", ".join(net.get("assignedAddresses") or []) or "no address"
            status = NETWORK_STATUS_TEXT.get(net.get("status", ""), net.get("status", ""))
            lines.append(f"{net.get('name') or net.get('nwid')}: {ips} ({status})")
        if not snap["networks"]:
            lines.append("Not joined to any network.")
        elif snap["member_count"]:
            lines.append(f"{snap['member_count']} other "
                         f"{'member' if snap['member_count'] == 1 else 'members'} reachable")
        public = self.monitor.public_ip()
        if public:
            lines.append(f"Public IP: {public}")
        if snap["tcp_relay"]:
            lines.append("Falling back to TCP relay - UDP is being blocked.")
        return "\n".join(lines)

    # ------------------------------------------------------------------- menu
    def rebuild_menu(self) -> None:
        snap = self.monitor.snapshot()
        self._menu_structure_cache = self._menu_structure(snap)
        self._menu_dirty = False
        self._live_items = []
        self._clear_menu()
        m = self.menu

        if self.updates.available:
            act = m.addAction(f"Update available: {self.updates.latest}")
            act.setToolTip("A newer release of this tray is out.")
            act.triggered.connect(self._offer_update)
            m.addSeparator()

        header = self._live(m, lambda a, s: a.setText(
            f"{APP_NAME} - {STATE_LABELS.get(s['state'], s['state'])}"))
        header.setEnabled(False)

        if snap["address"]:
            node = self._live(m, lambda a, s: a.setText(
                f"Node {s['address']}   (click to copy)"))
            node.triggered.connect(lambda _c=False: self._copy(
                self.monitor.status.get("address", ""), "Node ID copied."))

        if self.cfg.get("show_addresses_in_menu", True):
            self._add_addresses(m)

        if snap["state"] == "noauth":
            m.addSeparator()
            if snap["token_rejected"]:
                note = m.addAction("ZeroTier rejected this tray's token")
                note.setEnabled(False)
                m.addAction("Grant access again...", self._grant)
            else:
                note = m.addAction("This tray cannot read the service token yet")
                note.setEnabled(False)
                m.addAction("Grant access to ZeroTier...", self._grant)

        # ---- networks -------------------------------------------------
        m.addSeparator()
        if snap["running"] and snap["has_token"]:
            for net in snap["networks"]:
                self._add_network_menu(m, net)
            if not snap["networks"]:
                empty = m.addAction("No networks joined")
                empty.setEnabled(False)
            m.addAction("Join a network...", self._join)

        # ---- service --------------------------------------------------
        m.addSeparator()
        if not snap["installed"]:
            miss = m.addAction("zerotier-one is not installed")
            miss.setEnabled(False)
        elif snap["running"]:
            m.addAction("Stop ZeroTier", self._stop)
            m.addAction("Restart ZeroTier", lambda: self._privileged(["restart"]))
        else:
            m.addAction("Start ZeroTier", lambda: self._privileged(["start"]))

        # Checkable entries act on `triggered`, which only a click emits, so
        # the in-place updates below can set their ticks without firing them.
        if snap["installed"]:
            boot = self._live(m, lambda a, s: a.setChecked(s["boot_enabled"]),
                              checkable=True)
            boot.setText("Start with the system")
            boot.triggered.connect(self._set_boot)

            if self.monitor.firewall_open() is not None:
                fw = self._live(m, self._sync_firewall_action, checkable=True)
                fw.setToolTip("Inbound UDP lets peers reach you directly instead "
                              "of through a relay.")
                fw.triggered.connect(self._set_firewall)

        # ---- appearance and the rest ----------------------------------
        m.addSeparator()
        style_menu = self._submenu(m, "Icon")
        group = QActionGroup(style_menu)
        group.setExclusive(True)
        for key, label in icons.ICON_STYLES:
            act = QAction(label, style_menu)
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, k=key: self._set_style(k))
            group.addAction(act)
            style_menu.addAction(act)
            self._live_items.append(lambda _s, a=act, k=key: a.setChecked(
                k == str(self.cfg.get("icon_style", "zerotier"))))

        quiet = self._live(m, lambda a, s: a.setChecked(
            not self.cfg.get("notifications_enabled", True)), checkable=True)
        quiet.setText("Silence notifications")
        quiet.triggered.connect(self._set_quiet)

        m.addAction("Settings...", self.open_settings)
        m.addSeparator()
        m.addAction("Copy status", self._copy_status)
        m.addAction("Open config folder", self._open_config)
        m.addAction("Check for updates", self._check_updates)
        m.addAction("About", self._about)
        m.addAction("Quit", self._quit)

        self._update_menu(snap)

    def _sync_firewall_action(self, act: QAction, _snap: dict) -> None:
        fw = self.monitor.firewall_open()
        ports = ", ".join(str(p) for p in self.monitor.firewall_ports())
        act.setText(f"Allow UDP {ports} in zone \"{self.monitor.firewall_zone()}\"")
        act.setChecked(bool(fw))

    def _add_addresses(self, m: QMenu) -> None:
        """My IP, right at the top, plus everything else one hop away."""
        addrs = self.monitor.my_addresses()
        several = len(addrs["zerotier"]) > 1

        for addr, label in addrs["zerotier"]:
            bare = addr.split("/")[0]
            text = f"IP {bare}" + (f"  ·  {label}" if several else "")
            act = m.addAction(f"{text}   (click to copy)")
            act.setToolTip("The address this network's controller assigned to "
                           "this machine.")
            act.triggered.connect(lambda _c=False, v=bare:
                                  self._copy(v, f"{v} copied."))
        if not addrs["zerotier"] and self.monitor.running:
            none = m.addAction("No ZeroTier address yet")
            none.setEnabled(False)

        public = addrs["public_v4"][0] if addrs["public_v4"] else ""
        if public:
            act = m.addAction(f"Public IP {public}   (click to copy)")
            act.setToolTip("What ZeroTier's root servers see this machine as. "
                           "No third party was asked.")
            act.triggered.connect(lambda _c=False, v=public:
                                  self._copy(v, f"{v} copied."))

        if any(addrs[k] for k in ("zerotier", "public_v4", "public_v6", "lan")):
            self._add_address_submenu(m, addrs)

    def _add_address_submenu(self, m: QMenu, addrs: dict) -> None:
        sub = self._submenu(m, "My addresses")

        def group(title: str, rows: list[tuple[str, str]]) -> None:
            if not rows:
                return
            head = sub.addAction(title)
            head.setEnabled(False)
            for value, note in rows:
                text = f"    {value}" + (f"  ·  {note}" if note else "")
                act = sub.addAction(text)
                act.triggered.connect(lambda _c=False, v=value:
                                      self._copy(v, f"{v} copied."))

        group("ZeroTier", [(a.split("/")[0],
                            f"{label}  ·  /{a.split('/')[1]}" if "/" in a else label)
                           for a, label in addrs["zerotier"]])
        group("Public", [(a, "") for a in addrs["public_v4"]]
                        + [(a, "IPv6") for a in addrs["public_v6"]])
        group("This machine", [(ip, dev) for ip, dev in addrs["lan"]])

        sub.addSeparator()
        sub.addAction("Copy every address",
                      lambda _c=False, a=addrs: self._copy_all_addresses(a))

    def _copy_all_addresses(self, addrs: dict) -> None:
        lines = []
        for addr, label in addrs["zerotier"]:
            lines.append(f"{addr}\t{label} (ZeroTier)")
        for addr in addrs["public_v4"] + addrs["public_v6"]:
            lines.append(f"{addr}\tpublic")
        for ip, dev in addrs["lan"]:
            lines.append(f"{ip}\t{dev}")
        QApplication.clipboard().setText("\n".join(lines))
        self._notify(APP_NAME, f"{len(lines)} addresses copied.")

    def _add_network_menu(self, parent: QMenu, net: dict) -> None:
        nwid = net.get("nwid", "")
        name = net.get("name") or nwid
        status = net.get("status", "")
        sub = self._submenu(parent, f"{name}  ({NETWORK_STATUS_TEXT.get(status, status)})")

        for addr in net.get("assignedAddresses") or []:
            act = sub.addAction(f"{addr}   (click to copy)")
            act.triggered.connect(lambda _c=False, v=addr.split("/")[0]:
                                  self._copy(v, "Address copied."))
        if not net.get("assignedAddresses"):
            none = sub.addAction("No address assigned")
            none.setEnabled(False)

        info = sub.addAction(f"{nwid}   (click to copy)")
        info.triggered.connect(lambda _c=False, v=nwid: self._copy(v, "Network ID copied."))
        iface = sub.addAction(f"Interface {net.get('portDeviceName', '?')}"
                              f"  ·  MTU {net.get('mtu', '?')}"
                              f"  ·  {net.get('type', '')}")
        iface.setEnabled(False)

        # members
        sub.addSeparator()
        members = self.monitor.members_of(net)
        limit = self._member_limit()
        if members:
            people = self._submenu(sub, "Members")

            def title(_s, menu=people, n=nwid) -> None:
                current = self.monitor.members_of({"nwid": n})
                reach = sum(1 for x in current if x["reachable"])
                menu.setTitle(f"Members ({reach} of {len(current)} reachable)")
            self._live_items.append(title)

            for mem in members[:limit]:
                key = mem["key"]
                act = self._live(people, lambda a, _s, k=key: self._sync_member(a, k))
                act.triggered.connect(lambda _c=False, k=key: self._copy_member(k))
            if len(members) > limit:
                more = people.addAction(f"...and {len(members) - limit} more")
                more.setEnabled(False)
            people.addSeparator()
            people.addAction("Scan the network for members",
                             lambda _c=False, n=net: self._scan(n))
        else:
            none = sub.addAction("No other members seen")
            none.setEnabled(False)
            sub.addAction("Scan the network for members",
                          lambda _c=False, n=net: self._scan(n))

        # per-network switches
        sub.addSeparator()
        for key, label, tip in zt.NETWORK_FLAGS:
            act = self._live(sub, lambda a, s, n=nwid, k=key: a.setChecked(bool(
                next((x for x in s["networks"] if x.get("nwid") == n), {}).get(k))),
                checkable=True)
            act.setText(label)
            act.setToolTip(tip)
            act.triggered.connect(
                lambda checked, n=nwid, k=key: self._set_flag(n, k, checked))

        sub.addSeparator()
        sub.addAction("Leave this network", lambda _c=False, n=nwid, t=name:
                      self._leave(n, t))

    def _sync_member(self, act: QAction, key: str) -> None:
        mem = self._members_now.get(key)
        if mem:
            act.setText(member_line(mem))

    def _copy_member(self, key: str) -> None:
        mem = self._members_now.get(key)
        if mem:
            self._copy(mem["ip"] or mem["address"], "Copied.")

    # ------------------------------------------------------------------ acts
    def _copy(self, value: str, note: str) -> None:
        if not value:
            return
        QApplication.clipboard().setText(value)
        self._notify(APP_NAME, note)

    def _set_quiet(self, quiet: bool) -> None:
        self.cfg["notifications_enabled"] = not quiet
        self.cfg.save()
        if self.settings_dialog is not None:
            self.settings_dialog.notifications_enabled.setChecked(not quiet)

    def _set_style(self, key: str) -> None:
        self.cfg["icon_style"] = key
        self.cfg.save()
        self.refresh()

    def _join(self) -> None:
        nwid, ok = QInputDialog.getText(
            None, f"{APP_NAME} - join a network",
            "Network ID (16 hexadecimal characters):")
        if not ok or not nwid.strip():
            return
        nwid = nwid.strip().lower().replace(" ", "")
        if not zt.valid_nwid(nwid):
            self._warn("That is not a network ID",
                       "A ZeroTier network ID is exactly 16 hexadecimal "
                       "characters, like 08752e18b160f353.")
            return

        def done(ok_: bool, err: str) -> None:
            if ok_:
                self._notify(APP_NAME, f"Joined {nwid}. Waiting for the controller.")
            else:
                self._warn("Could not join", err)

        self.monitor.join(nwid, done)

    def _leave(self, nwid: str, name: str) -> None:
        if self.cfg.get("confirm_leave", True):
            box = QMessageBox(QMessageBox.Question, APP_NAME,
                              f"Leave \"{name}\"?\n\n"
                              "The interface and its address go away. Rejoining "
                              "later needs the network ID and, on a private "
                              "network, another approval from its controller.",
                              QMessageBox.Yes | QMessageBox.No)
            box.setWindowIcon(icons.app_icon())
            if box.exec() != QMessageBox.Yes:
                return

        def done(ok_: bool, err: str) -> None:
            if ok_:
                self._notify(APP_NAME, f"Left {name}.")
            else:
                self._warn("Could not leave", err)

        self.monitor.leave(nwid, done)

    def _set_flag(self, nwid: str, key: str, value: bool) -> None:
        def done(ok_: bool, err: str) -> None:
            if not ok_:
                self._warn("Could not change that setting", err)

        self.monitor.set_flag(nwid, key, value, done)

    def _scan(self, net: dict) -> None:
        addrs = net.get("assignedAddresses") or []
        if not addrs:
            self._warn("Nothing to scan",
                       "This network has not assigned an address to this machine "
                       "yet, so there is no subnet to look at.")
            return
        if not self.monitor.scanner.start([net]):
            self._notify(APP_NAME, "A scan is already running.")
            return
        self._scan_mine = True

    def _scan_done(self, probed: int, label: str) -> None:
        if self._scan_mine:
            self._scan_mine = False
            self._notify(APP_NAME, f"Pinged {probed} addresses on {label}.")

    def _stop(self) -> None:
        if self.cfg.get("confirm_stop", True):
            box = QMessageBox(QMessageBox.Question, APP_NAME,
                              "Stop ZeroTier?\n\n"
                              "Every joined network goes down and anything "
                              "running over it is cut off.",
                              QMessageBox.Yes | QMessageBox.No)
            box.setWindowIcon(icons.app_icon())
            if box.exec() != QMessageBox.Yes:
                return
        self._privileged(["stop"])

    def _set_boot(self, enabled: bool) -> None:
        self._privileged(["enable" if enabled else "disable"])

    def _set_firewall(self, opened: bool) -> None:
        zone = self.monitor.firewall_zone()
        if not zone:
            self._warn("No firewall zone",
                       "firewalld did not report a zone to put the ports in.")
            return
        self._privileged(["firewall", "open" if opened else "close", zone])

    def _grant(self) -> None:
        box = QMessageBox(QMessageBox.Question, APP_NAME,
                          "Give this tray access to the ZeroTier service?\n\n"
                          "zerotier-one keeps its API token in a root-only file. "
                          "A copy goes into your config directory, readable only "
                          "by you, and from then on joining, leaving and the live "
                          "view work without a password.\n\n"
                          "Anyone who can read your home directory could then "
                          "connect this machine to any ZeroTier network.",
                          QMessageBox.Yes | QMessageBox.No)
        box.setWindowIcon(icons.app_icon())
        if box.exec() != QMessageBox.Yes:
            return
        self._privileged(["grant", getpass.getuser()])

    def start_service(self) -> None:
        self._privileged(["start"])

    def _privileged(self, args: list[str]) -> None:
        if not self.priv.run(args):
            self._notify(APP_NAME, "Still waiting on the last password prompt.")
            return
        self._pending = args[0]

    def _on_priv(self, ok: bool, message: str) -> None:
        pending, self._pending = self._pending, ""
        if pending == "firewall":
            self.monitor.forget_firewall()
        if ok:
            if pending == "grant":
                self.monitor.reload_token()
                self.monitor.poll_status()
                self.monitor.poll_peers()
                self._notify(APP_NAME, "Access granted. The tray is live now.")
            if pending in ("stop", "restart") and message:
                self._notify(APP_NAME, message)
            self.monitor.poll_service()
            QTimer.singleShot(1500, self.monitor.poll_service)
            QTimer.singleShot(2500, self.monitor.poll_service)
        elif message:
            self._warn("That did not work", message)
        self.refresh()

    # ---------------------------------------------------------------- updates
    def _check_updates(self) -> None:
        """The menu entry. Nothing checks on its own; this is the only path."""
        def answered(ok: bool, message: str) -> None:
            self.updates.checked.disconnect(answered)
            self.refresh()                         # let the banner in or out
            if not ok:
                self._warn("Could not check for updates", message)
            elif self.updates.available:
                self._offer_update()
            else:
                self._notify(APP_NAME, message)

        if not self.updates.check():
            return
        self.updates.checked.connect(answered)

    def _offer_update(self) -> None:
        version = self.updates.latest
        box = QMessageBox(QMessageBox.NoIcon, f"{APP_NAME} - update", "")
        box.setWindowIcon(icons.app_icon())
        box.setTextFormat(Qt.RichText)
        box.setIconPixmap(icons.render_pixmap(
            64, "zerotier_logo", icons.ZT_ORANGE, "none", 0.0,
            icons.RenderCtx(padding=0.02)))
        box.setText(
            f"<b>ZeroTier Tray {version} is out.</b><br>"
            f"You are running {__version__}.<br><br>"
            "This came from your package manager, so it updates the same way. "
            "The one-line installer picks the right package for this distro:"
            f"<br><br><tt>{updates.INSTALL_COMMAND}</tt>")
        copy_it = box.addButton("Copy that command", QMessageBox.ActionRole)
        page = box.addButton("Open the release page", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is copy_it:
            self._copy(updates.INSTALL_COMMAND, "Install command copied.")
        elif clicked is page:
            QDesktopServices.openUrl(QUrl(self.updates.page))

    def _copy_status(self) -> None:
        snap = self.monitor.snapshot()
        addrs = self.monitor.my_addresses()
        lines = [
            f"{APP_NAME} status",
            f"  state    : {STATE_LABELS.get(snap['state'], snap['state'])}",
            f"  service  : {snap['unit_state'] or 'unknown'}"
            f" (boot: {'enabled' if snap['boot_enabled'] else 'disabled'})",
            f"  node     : {snap['address'] or 'unknown'}  v{snap['version']}",
            f"  online   : {'yes' if snap['online'] else 'no'}",
            f"  ports    : {', '.join(str(p) for p in snap['ports'])}",
            f"  my IP    : {self.monitor.my_ip() or '-'}",
            f"  public   : {', '.join(addrs['public_v4'] + addrs['public_v6']) or '-'}",
            f"  local    : {', '.join(f'{ip} ({dev})' for ip, dev in addrs['lan']) or '-'}",
        ]
        for net in snap["networks"]:
            lines.append(f"  network  : {net.get('name') or ''} [{net.get('nwid')}]"
                         f" {net.get('status')}")
            lines.append(f"      ip   : {', '.join(net.get('assignedAddresses') or []) or '-'}")
            lines.append(f"      dev  : {net.get('portDeviceName', '-')}")
            for mem in self.monitor.members_of(net):
                lines.append(f"      peer : {member_line(mem)}")
        QApplication.clipboard().setText("\n".join(lines))
        self._notify(APP_NAME, "Status copied to the clipboard.")

    def _open_config(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_FILE.parent)))

    def open_settings(self) -> None:
        from .settings import SettingsDialog
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.cfg, self.monitor,
                                                 self.priv, self.updates)
            self.settings_dialog.applied.connect(self._on_settings_applied)
            self.settings_dialog.finished.connect(self._settings_closed)
        self.settings_dialog.load_from_config()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _settings_closed(self, _result: int) -> None:
        # With no tray to come back to, the window was the whole program.
        if self._no_tray:
            QApplication.quit()

    def _on_settings_applied(self) -> None:
        self.cfg.save()
        self.apply_settings()

    def _about(self) -> None:
        box = QMessageBox(QMessageBox.NoIcon, f"About {APP_NAME}", "")
        box.setWindowIcon(icons.app_icon())
        box.setTextFormat(Qt.RichText)
        box.setIconPixmap(icons.render_pixmap(
            72, "zerotier_logo", icons.ZT_ORANGE, "none", 0.0,
            icons.RenderCtx(padding=0.02)))
        box.setText(
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "A tray icon for ZeroTier One. Join and leave networks, see the "
            "address you were given, watch who else is reachable, flip the "
            "per-network switches, start the service at boot and open its UDP "
            "port - without a terminal.<br><br>"
            "Everything comes from zerotier-one's own local API; nothing is "
            "sent anywhere.<br><br>"
            "<b>Not affiliated with ZeroTier, Inc.</b><br>"
            "This is an independent front end, not made, endorsed or supported "
            "by them. ZeroTier is their trademark, and the mark is drawn here "
            "only to name the software this tray controls. Problems with the "
            "tray belong in its own tracker, not theirs.<br><br>"
            f"<a href='{PROJECT_URL}'>{PROJECT_URL}</a><br>"
            "<a href='https://www.zerotier.com/'>zerotier.com</a> - the daemon "
            "this controls"
        )
        box.exec()

    def _quit(self) -> None:
        self.monitor.shutdown()
        QApplication.quit()

    def _on_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.Trigger:
            return
        action = str(self.cfg.get("click_action", "menu"))
        if action == "settings":
            self.open_settings()
        elif action == "toggle":
            if self.monitor.running:
                self._stop()
            else:
                self._privileged(["start"])
        elif action == "menu":
            self._popup_menu()

    def _popup_menu(self) -> None:
        """Left click shows the menu - where a client is allowed to.

        A StatusNotifierItem tray (Plasma, and most panels now) never says
        where the icon is: geometry() is empty, and popping up at its "centre"
        meant the top-left corner of the screen. On X11 the cursor is the next
        best anchor. On Wayland a client cannot place a popup with nothing of
        its own to hang it on - Qt drops it and nothing appears - so the click
        opens the window instead; the panel shows the menu on right click.
        """
        geometry = self.tray.geometry()
        if geometry.isValid():
            self.menu.popup(geometry.center())
        elif QGuiApplication.platformName() != "wayland":
            self.menu.popup(QCursor.pos())
        else:
            self.open_settings()

    # ------------------------------------------------------------ notifications
    def _notify(self, title: str, body: str) -> None:
        if not self.cfg.get("notifications_enabled", True):
            return
        self.tray.showMessage(title, body, QSystemTrayIcon.Information, 5000)

    def _on_state(self, old: str, new: str) -> None:
        if new in ("denied", "notfound") and self.cfg.get("notify_on_denied", True):
            self._notify(APP_NAME,
                         "A controller has not authorised this machine yet."
                         if new == "denied" else
                         "A joined network ID does not exist.")
        elif self.cfg.get("notify_on_service", True):
            if new == "noauth" and self.monitor.token_rejected:
                self._notify(APP_NAME, "ZeroTier rejected this tray's token. "
                                       "Grant access again from the menu.")
            elif new == "stopped" and old != "stopped":
                self._notify(APP_NAME, "ZeroTier stopped.")
            elif new == "error":
                self._notify(APP_NAME, "The zerotier-one service failed.")
            elif new == "connected" and old in ("starting", "joining", "online",
                                                "stopped", "noauth"):
                self._notify(APP_NAME, "Connected.")
        self.refresh()

    def _on_networks(self) -> None:
        if self.cfg.get("notify_on_network", True):
            snap = self.monitor.snapshot()
            up = [n.get("name") or n.get("nwid") for n in snap["networks"]
                  if n.get("status") == "OK"]
            if up:
                self._notify(APP_NAME, "Networks up: " + ", ".join(up))
        self.refresh()

    def _on_members(self) -> None:
        if self.cfg.get("notify_on_member", False):
            n = self.monitor.member_count()
            self._notify(APP_NAME,
                         f"{n} other {'member' if n == 1 else 'members'} reachable.")
        self.refresh()

    def _warn(self, title: str, text: str) -> None:
        box = QMessageBox(QMessageBox.Warning, title, text or "Unknown error.")
        box.setWindowIcon(icons.app_icon())
        box.exec()
