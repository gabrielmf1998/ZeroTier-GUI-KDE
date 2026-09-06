"""The settings window: everything the tray can do, without touching a terminal."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import autostart, icons, system, updates, zt
from . import __version__
from .config import APP_NAME, COLOR_PRESETS, STATES

PREVIEW_PX = 40


class ColorButton(QPushButton):
    """A swatch that opens a colour picker."""

    changed = Signal(str)

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(46, 24)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color
        self._refresh()

    def _refresh(self) -> None:
        c = QColor(self._color)
        text = "#000000" if c.lightnessF() > 0.55 else "#ffffff"
        self.setStyleSheet(
            f"background-color:{self._color}; color:{text};"
            "border:1px solid rgba(128,128,128,0.6); border-radius:4px;"
            "font-size:10px; font-family:monospace;")
        self.setText(self._color.lstrip("#").upper())

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Pick a colour")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self._color)


class StatePreview(QLabel):
    """One animated swatch showing exactly what the tray will look like."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(PREVIEW_PX + 6, PREVIEW_PX + 6)
        self.setAlignment(Qt.AlignCenter)
        self.style_key = "zerotier"
        self.color = "#ffb354"
        self.animation = "none"
        self.ctx = icons.RenderCtx()

    def paint(self, phase: float) -> None:
        self.setPixmap(icons.render_pixmap(PREVIEW_PX, self.style_key, self.color,
                                           self.animation, phase, self.ctx))


TILE_PX = 40
TILE_W = 92


class GalleryTile(QToolButton):
    """One clickable, live-rendered option in a gallery.

    Fixed width so the grid stays square whatever the label says, and the full
    name goes in the tooltip when it has to be elided.
    """

    def __init__(self, key: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.full_label = label
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolTip(label)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(TILE_PX, TILE_PX))
        self.setFixedWidth(TILE_W)
        font = self.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        self.setFont(font)
        self.setText(QFontMetrics(font).elidedText(label, Qt.ElideRight, TILE_W - 8))


class Gallery(QWidget):
    """A grid of options you pick by looking at them rather than by name."""

    picked = Signal(str)

    def __init__(self, entries, columns: int, parent=None) -> None:
        super().__init__(parent)
        self.tiles: dict[str, GalleryTile] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, (key, label) in enumerate(entries):
            tile = GalleryTile(key, label)
            tile.clicked.connect(lambda _c=False, k=key: self.picked.emit(k))
            self._group.addButton(tile)
            self.tiles[key] = tile
            grid.addWidget(tile, i // columns, i % columns)

    def current(self) -> str:
        for key, tile in self.tiles.items():
            if tile.isChecked():
                return key
        return next(iter(self.tiles), "")

    def set_current(self, key: str | None) -> None:
        """None leaves nothing checked.

        An exclusive QButtonGroup refuses to let its last checked button go, so
        clearing has to happen with exclusivity off - otherwise a gallery whose
        options disagree would sit there claiming they all match the first one.
        """
        self._group.setExclusive(False)
        for candidate, tile in self.tiles.items():
            tile.setChecked(candidate == key)
        self._group.setExclusive(True)

    def repaint_tiles(self, render) -> None:
        """render(key) -> QPixmap, called for every tile."""
        for key, tile in self.tiles.items():
            tile.setIcon(QIcon(render(key)))


class StateStrip(QWidget):
    """Every state at once, drawn exactly the way the tray draws it."""

    def __init__(self, dialog, parent=None) -> None:
        super().__init__(parent)
        self.dialog = dialog
        self.phase = 0.0
        self.setMinimumHeight(78)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        size = 44
        step = self.width() / max(1, len(STATES))
        metrics = QFontMetrics(self.font())
        for i, (key, label) in enumerate(STATES):
            pixmap = self.dialog.render_state(key, size, self.phase)
            painter.drawPixmap(int(step * i + step / 2 - size / 2), 4, pixmap)
            painter.setPen(QColor(140, 140, 140))
            painter.drawText(
                QRectF(step * i, size + 8, step, 20),
                Qt.AlignHCenter | Qt.AlignTop,
                metrics.elidedText(label, Qt.ElideRight, int(step) - 4))
        painter.end()


def _scroll(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(inner)
    return area


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(mid);")
    return lab


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, cfg, monitor, priv, checker, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.monitor = monitor
        self.priv = priv
        self.updates = checker
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setWindowIcon(icons.app_icon())
        self.resize(880, 720)

        self._phase = 0.0
        self._previews: dict[str, StatePreview] = {}
        self._color_buttons: dict[str, ColorButton] = {}
        self._anim_combos: dict[str, QComboBox] = {}

        self.strip = StateStrip(self)
        self.tabs = tabs = QTabWidget(self)
        tabs.addTab(self._build_appearance(), "Appearance")
        tabs.addTab(self._build_states(), "States")
        tabs.addTab(self._build_networks(), "Networks")
        tabs.addTab(self._build_members(), "Members")
        tabs.addTab(self._build_behaviour(), "Behaviour")
        tabs.addTab(self._build_service(), "Service")
        tabs.currentChanged.connect(lambda _i: self._refresh_live())

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Apply |
            QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)

        root = QVBoxLayout(self)
        root.addWidget(self.strip)
        root.addWidget(tabs)
        root.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(50)

        self._live = QTimer(self)
        self._live.timeout.connect(self._refresh_live)
        self._live.start(2500)

        monitor.changed.connect(self._refresh_live)
        self.load_from_config()

    # ------------------------------------------------------------ appearance
    def _build_appearance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        picker = QGroupBox("Icon")
        pick_lay = QVBoxLayout(picker)
        self.style_gallery = Gallery(icons.ICON_STYLES, columns=7)
        self.style_gallery.picked.connect(lambda _k: self._sync_previews())
        pick_lay.addWidget(self.style_gallery)
        pick_lay.addWidget(_hint(
            "Every shape, drawn by the same painter the tray uses, at the "
            "colour of the Connected state. Click one."))
        lay.addWidget(picker)

        motion = QGroupBox("Motion")
        motion_lay = QVBoxLayout(motion)
        self.anim_gallery = Gallery(icons.ANIMATIONS, columns=7)
        self.anim_gallery.picked.connect(self._pick_animation_for_all)
        motion_lay.addWidget(self.anim_gallery)
        motion_lay.addWidget(_hint(
            "These are moving right now, in the shape you picked. Clicking one "
            "gives it to <b>every</b> state — the States tab sets them one by "
            "one, which is where a different animation per state is worth it."))
        lay.addWidget(motion)

        shape = QGroupBox("Shape")
        form = QFormLayout(shape)

        self.thickness = QDoubleSpinBox()
        self.thickness.setRange(0.4, 2.5)
        self.thickness.setSingleStep(0.05)
        self.thickness.valueChanged.connect(self._sync_previews)
        form.addRow("Stroke weight", self.thickness)

        self.padding = QDoubleSpinBox()
        self.padding.setRange(0.0, 0.30)
        self.padding.setSingleStep(0.01)
        self.padding.valueChanged.connect(self._sync_previews)
        form.addRow("Inner margin", self.padding)

        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.6, 1.6)
        self.scale.setSingleStep(0.02)
        self.scale.valueChanged.connect(self._sync_previews)
        form.addRow("Fill of the tray cell", self.scale)

        self.state_dot = QCheckBox(
            "Corner dot for the state when the icon keeps ZeroTier's colours")
        self.state_dot.toggled.connect(self._sync_previews)
        form.addRow("", self.state_dot)
        form.addRow("", _hint(
            "\"ZeroTier logo (official colours)\" is the upstream mark: an orange "
            "tile with the ⏁ glyph. It never changes colour, so the corner dot is "
            "what tells you the state."))
        lay.addWidget(shape)

        colour = QGroupBox("Colour")
        cform = QFormLayout(colour)
        self.monochrome = QCheckBox("Use one colour for every state")
        self.monochrome.toggled.connect(self._sync_previews)
        cform.addRow("", self.monochrome)
        self.mono_color = ColorButton("#c9d1d9")
        self.mono_color.changed.connect(lambda _c: self._sync_previews())
        cform.addRow("That colour", self.mono_color)
        lay.addWidget(colour)

        badge = QGroupBox("Count badge")
        bform = QFormLayout(badge)
        self.show_badge = QCheckBox("Show a number on the icon")
        self.show_badge.toggled.connect(self._sync_previews)
        bform.addRow("", self.show_badge)

        self.badge_source = QComboBox()
        self.badge_source.addItem("Reachable members", "members")
        self.badge_source.addItem("Joined networks", "networks")
        self.badge_source.currentIndexChanged.connect(self._sync_previews)
        bform.addRow("Count", self.badge_source)

        self.badge_style = QComboBox()
        for key, label in icons.BADGE_STYLES:
            self.badge_style.addItem(label, key)
        self.badge_style.currentIndexChanged.connect(self._sync_previews)
        bform.addRow("Badge shape", self.badge_style)

        self.badge_position = QComboBox()
        for key, label in icons.BADGE_POSITIONS:
            self.badge_position.addItem(label, key)
        self.badge_position.currentIndexChanged.connect(self._sync_previews)
        bform.addRow("Corner", self.badge_position)

        self.badge_when_zero = QCheckBox("Show it when the count is zero")
        self.badge_when_zero.toggled.connect(self._sync_previews)
        bform.addRow("", self.badge_when_zero)

        self.badge_color = ColorButton("#0d1117")
        self.badge_color.changed.connect(lambda _c: self._sync_previews())
        bform.addRow("Badge background", self.badge_color)
        self.badge_text_color = ColorButton("#ffffff")
        self.badge_text_color.changed.connect(lambda _c: self._sync_previews())
        bform.addRow("Badge text", self.badge_text_color)
        lay.addWidget(badge)

        motion = QGroupBox("Motion")
        mform = QFormLayout(motion)
        self.anim_speed = QDoubleSpinBox()
        self.anim_speed.setRange(0.25, 3.0)
        self.anim_speed.setSingleStep(0.05)
        mform.addRow("Animation speed", self.anim_speed)
        self.anim_fps = QSpinBox()
        self.anim_fps.setRange(5, 60)
        self.anim_fps.setSuffix(" fps")
        mform.addRow("Redraw rate", self.anim_fps)
        self.animate_idle = QCheckBox(
            "Keep animating in the resting states (connected, online, stopped)")
        mform.addRow("", self.animate_idle)
        lay.addWidget(motion)

        lay.addStretch(1)
        return _scroll(page)

    # ---------------------------------------------------------------- states
    def _build_states(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_hint(
            "A colour and an animation for every state the icon can be in. "
            "The swatch beside each row is the real renderer, at tray size."))

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Colour preset"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(keep my colours)", "")
        for name in COLOR_PRESETS:
            self.preset_combo.addItem(name, name)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self.preset_combo, 1)

        preset_row.addWidget(QLabel("Set every animation to"))
        self.bulk_anim = QComboBox()
        self.bulk_anim.addItem("(leave them alone)", "")
        for key, label in icons.ANIMATIONS:
            self.bulk_anim.addItem(label, key)
        self.bulk_anim.currentIndexChanged.connect(self._bulk_animation)
        preset_row.addWidget(self.bulk_anim, 1)
        lay.addLayout(preset_row)

        grid_box = QGroupBox("Per state")
        grid = QGridLayout(grid_box)
        grid.addWidget(QLabel("<b>State</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Colour</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Animation</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Preview</b>"), 0, 3)
        for row, (key, label) in enumerate(STATES, start=1):
            grid.addWidget(QLabel(label), row, 0)

            btn = ColorButton(self.cfg.color_for(key))
            btn.changed.connect(lambda _c, k=key: self._sync_previews())
            self._color_buttons[key] = btn
            grid.addWidget(btn, row, 1)

            combo = QComboBox()
            for akey, alabel in icons.ANIMATIONS:
                combo.addItem(alabel, akey)
            combo.currentIndexChanged.connect(lambda _i: self._sync_previews())
            self._anim_combos[key] = combo
            grid.addWidget(combo, row, 2)

            prev = StatePreview()
            self._previews[key] = prev
            grid.addWidget(prev, row, 3)
        grid.setColumnStretch(2, 1)
        lay.addWidget(grid_box)
        lay.addStretch(1)
        return _scroll(page)

    def _bulk_animation(self) -> None:
        key = self.bulk_anim.currentData()
        if not key:
            return
        for combo in self._anim_combos.values():
            _set_data(combo, key)
        self.bulk_anim.blockSignals(True)
        self.bulk_anim.setCurrentIndex(0)
        self.bulk_anim.blockSignals(False)
        self._sync_previews()

    def _apply_preset(self, _index: int) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        for key, value in COLOR_PRESETS[name].items():
            if key in self._color_buttons:
                self._color_buttons[key].set_color(value)
        self._sync_previews()

    # -------------------------------------------------------------- networks
    def _build_networks(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        join = QGroupBox("Join a network")
        jform = QHBoxLayout(join)
        self.join_edit = QLineEdit()
        self.join_edit.setPlaceholderText("16 hexadecimal characters, e.g. 08752e18b160f353")
        self.join_edit.setMaxLength(19)
        self.join_edit.returnPressed.connect(self._do_join)
        jform.addWidget(self.join_edit, 1)
        self.join_button = QPushButton("Join")
        self.join_button.clicked.connect(self._do_join)
        jform.addWidget(self.join_button)
        lay.addWidget(join)

        self.net_table = QTableWidget(0, 6)
        self.net_table.setHorizontalHeaderLabels(
            ["Name", "Network ID", "Status", "Address", "Interface", "Type"])
        _tidy_table(self.net_table)
        self.net_table.itemSelectionChanged.connect(self._sync_net_buttons)
        lay.addWidget(self.net_table, 1)

        row = QHBoxLayout()
        self.leave_button = QPushButton("Leave the selected network")
        self.leave_button.clicked.connect(self._do_leave)
        row.addWidget(self.leave_button)
        self.copy_nwid_button = QPushButton("Copy its ID")
        self.copy_nwid_button.clicked.connect(self._copy_nwid)
        row.addWidget(self.copy_nwid_button)
        row.addStretch(1)
        lay.addLayout(row)

        flags = QGroupBox("Settings for the selected network")
        fform = QVBoxLayout(flags)
        self._flag_boxes: dict[str, QCheckBox] = {}
        for key, label, tip in zt.NETWORK_FLAGS:
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.clicked.connect(lambda checked, k=key: self._do_flag(k, checked))
            self._flag_boxes[key] = box
            fform.addWidget(box)
        fform.addWidget(_hint(
            "These are the same switches as zerotier-cli set <network> allowX=. "
            "They take effect immediately - there is nothing to apply."))
        lay.addWidget(flags)
        return page

    def _selected_network(self) -> dict | None:
        rows = {i.row() for i in self.net_table.selectedIndexes()}
        if not rows:
            return None
        nets = self.monitor.networks
        row = sorted(rows)[0]
        return nets[row] if 0 <= row < len(nets) else None

    def _sync_net_buttons(self) -> None:
        net = self._selected_network()
        for button in (self.leave_button, self.copy_nwid_button):
            button.setEnabled(net is not None)
        for key, box in self._flag_boxes.items():
            box.setEnabled(net is not None)
            box.blockSignals(True)
            box.setChecked(bool(net.get(key, False)) if net else False)
            box.blockSignals(False)

    def _do_join(self) -> None:
        nwid = self.join_edit.text().strip().lower().replace(" ", "")
        if not zt.valid_nwid(nwid):
            self._warn("That is not a network ID",
                       "A ZeroTier network ID is exactly 16 hexadecimal "
                       "characters, like 08752e18b160f353.")
            return

        def done(ok: bool, err: str) -> None:
            if ok:
                self.join_edit.clear()
                self._refresh_live()
            else:
                self._warn("Could not join", err)

        self.monitor.join(nwid, done)

    def _do_leave(self) -> None:
        net = self._selected_network()
        if not net:
            return
        name = net.get("name") or net.get("nwid")
        if QMessageBox.question(
                self, APP_NAME,
                f"Leave \"{name}\"?\n\nThe interface and its address go away.") \
                != QMessageBox.Yes:
            return

        def done(ok: bool, err: str) -> None:
            if ok:
                self._refresh_live()
            else:
                self._warn("Could not leave", err)

        self.monitor.leave(net.get("nwid", ""), done)

    def _copy_nwid(self) -> None:
        net = self._selected_network()
        if net:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(net.get("nwid", ""))

    def _do_flag(self, key: str, checked: bool) -> None:
        net = self._selected_network()
        if not net:
            return

        def done(ok: bool, err: str) -> None:
            if not ok:
                self._warn("Could not change that setting", err)
            self._refresh_live()

        self.monitor.set_flag(net.get("nwid", ""), key, checked, done)

    # --------------------------------------------------------------- members
    def _build_members(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_hint(
            "Who else zerotier-one can currently see. The address comes from the "
            "kernel neighbour table, matched to a peer through the MAC ZeroTier "
            "derives from the network ID and the node ID - so a member only gets "
            "an IP here once it has actually exchanged a packet with this machine. "
            "Scan the subnet to make that happen."))

        self.member_table = QTableWidget(0, 7)
        self.member_table.setHorizontalHeaderLabels(
            ["Address", "Node", "Latency", "Path", "Version", "Endpoint", "Network"])
        _tidy_table(self.member_table)
        lay.addWidget(self.member_table, 1)

        row = QHBoxLayout()
        self.scan_button = QPushButton("Scan the subnet for members")
        self.scan_button.clicked.connect(self._do_scan)
        row.addWidget(self.scan_button)
        self.show_roots = QCheckBox("Also list ZeroTier's own roots and controllers")
        row.addWidget(self.show_roots)
        row.addStretch(1)
        lay.addLayout(row)

        self.resolve_ips = QCheckBox(
            "Look up member addresses in the neighbour table")
        lay.addWidget(self.resolve_ips)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Members to list in the tray menu"))
        self.member_limit = QSpinBox()
        self.member_limit.setRange(1, 200)
        limit_row.addWidget(self.member_limit)
        limit_row.addStretch(1)
        lay.addLayout(limit_row)
        return page

    def _do_scan(self) -> None:
        nets = [n for n in self.monitor.networks if n.get("assignedAddresses")]
        if not nets:
            self._warn("Nothing to scan",
                       "No joined network has given this machine an address yet.")
            return
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for net in nets:
                zt.scan_subnet(net)
        finally:
            QApplication.restoreOverrideCursor()
            self.scan_button.setEnabled(True)
            self.scan_button.setText("Scan the subnet for members")
        self.monitor.poll_peers()
        self._refresh_live()

    # ------------------------------------------------------------- behaviour
    def _build_behaviour(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        tray_box = QGroupBox("The tray icon")
        form = QFormLayout(tray_box)
        self.click_action = QComboBox()
        self.click_action.addItem("Open the menu", "menu")
        self.click_action.addItem("Open settings", "settings")
        self.click_action.addItem("Start or stop the service", "toggle")
        self.click_action.addItem("Do nothing", "nothing")
        form.addRow("Left click", self.click_action)

        self.show_addresses = QCheckBox(
            "Show my addresses at the top of the menu")
        self.show_addresses.setToolTip(
            "The ZeroTier address and the public address, each one click to "
            "copy, plus a submenu with every address this machine answers on.")
        form.addRow("", self.show_addresses)
        self.tooltip_details = QCheckBox("Put the whole status in the tooltip")
        form.addRow("", self.tooltip_details)
        self.hide_when_stopped = QCheckBox("Hide the icon while ZeroTier is stopped")
        form.addRow("", self.hide_when_stopped)
        self.tray_autostart = QCheckBox("Start this tray when I log in")
        form.addRow("", self.tray_autostart)
        self.start_service_with_tray = QCheckBox(
            "Start the ZeroTier service when this tray starts")
        self.start_service_with_tray.setToolTip(
            "Asks for a password each time. Enabling the unit at boot, on the "
            "Service tab, does the same thing without one.")
        form.addRow("", self.start_service_with_tray)
        lay.addWidget(tray_box)

        confirm = QGroupBox("Ask before")
        cform = QVBoxLayout(confirm)
        self.confirm_stop = QCheckBox("Stopping the service")
        cform.addWidget(self.confirm_stop)
        self.confirm_leave = QCheckBox("Leaving a network")
        cform.addWidget(self.confirm_leave)
        lay.addWidget(confirm)

        notif = QGroupBox("Notifications")
        nform = QVBoxLayout(notif)
        self.notifications_enabled = QCheckBox("Show notifications at all")
        self.notifications_enabled.toggled.connect(self._sync_notif_enabled)
        nform.addWidget(self.notifications_enabled)
        self.notify_on_service = QCheckBox("The service started, stopped or failed")
        nform.addWidget(self.notify_on_service)
        self.notify_on_network = QCheckBox("A network came up or went down")
        nform.addWidget(self.notify_on_network)
        self.notify_on_member = QCheckBox("Someone joined or left a network")
        nform.addWidget(self.notify_on_member)
        self.notify_on_denied = QCheckBox("A controller refused this machine")
        nform.addWidget(self.notify_on_denied)
        lay.addWidget(notif)

        poll = QGroupBox("How often to look")
        pform = QFormLayout(poll)
        self.status_poll = QSpinBox()
        self.status_poll.setRange(500, 60000)
        self.status_poll.setSingleStep(500)
        self.status_poll.setSuffix(" ms")
        pform.addRow("Status and networks", self.status_poll)
        self.peer_poll = QSpinBox()
        self.peer_poll.setRange(1000, 120000)
        self.peer_poll.setSingleStep(500)
        self.peer_poll.setSuffix(" ms")
        pform.addRow("Members", self.peer_poll)
        self.service_poll = QSpinBox()
        self.service_poll.setRange(1000, 120000)
        self.service_poll.setSingleStep(500)
        self.service_poll.setSuffix(" ms")
        pform.addRow("The systemd unit", self.service_poll)
        lay.addWidget(poll)

        lay.addStretch(1)
        return _scroll(page)

    def _sync_notif_enabled(self) -> None:
        on = self.notifications_enabled.isChecked()
        for box in (self.notify_on_service, self.notify_on_network,
                    self.notify_on_member, self.notify_on_denied):
            box.setEnabled(on)

    # --------------------------------------------------------------- service
    def _build_service(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        node = QGroupBox("This node")
        nform = QFormLayout(node)
        self.lbl_node = self._copyable_row(nform, "Node ID")
        self.lbl_my_ip = self._copyable_row(nform, "My ZeroTier address")
        self.lbl_public = self._copyable_row(nform, "Public address")
        self.lbl_public6 = self._copyable_row(nform, "Public IPv6")
        self.lbl_lan = self._copyable_row(nform, "On this machine")
        self.lbl_version = QLabel("-")
        nform.addRow("Version", self.lbl_version)
        self.lbl_online = QLabel("-")
        nform.addRow("Reachable roots", self.lbl_online)
        self.lbl_ports = QLabel("-")
        nform.addRow("Listening on", self.lbl_ports)
        copy_all = QPushButton("Copy every address")
        copy_all.clicked.connect(self._copy_all_addresses)
        row0 = QHBoxLayout()
        row0.addWidget(copy_all)
        row0.addStretch(1)
        nform.addRow("", row0)
        nform.addRow("", _hint(
            "The public address is what ZeroTier's own root servers report "
            "seeing you at, so finding it costs nothing and asks nobody."))
        lay.addWidget(node)

        svc = QGroupBox("The zerotier-one service")
        sform = QFormLayout(svc)
        self.lbl_unit = QLabel("-")
        sform.addRow("Unit", self.lbl_unit)
        row = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_start.clicked.connect(lambda: self._privileged(["start"]))
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(lambda: self._privileged(["stop"]))
        self.btn_restart = QPushButton("Restart")
        self.btn_restart.clicked.connect(lambda: self._privileged(["restart"]))
        for b in (self.btn_start, self.btn_stop, self.btn_restart):
            row.addWidget(b)
        row.addStretch(1)
        sform.addRow("", row)
        self.boot_box = QCheckBox("Start ZeroTier with the system")
        self.boot_box.clicked.connect(
            lambda checked: self._privileged(["enable" if checked else "disable"]))
        sform.addRow("", self.boot_box)
        sform.addRow("", _hint("Each of these asks for a password once, through "
                               "polkit."))
        lay.addWidget(svc)

        access = QGroupBox("Access to the local API")
        aform = QVBoxLayout(access)
        self.lbl_token = QLabel("-")
        self.lbl_token.setWordWrap(True)
        aform.addWidget(self.lbl_token)
        arow = QHBoxLayout()
        self.btn_grant = QPushButton("Grant access to this user")
        self.btn_grant.clicked.connect(self._do_grant)
        arow.addWidget(self.btn_grant)
        self.btn_revoke = QPushButton("Take it away again")
        self.btn_revoke.clicked.connect(self._do_revoke)
        arow.addWidget(self.btn_revoke)
        arow.addStretch(1)
        aform.addLayout(arow)
        aform.addWidget(_hint(
            "zerotier-one keeps its API token in a root-only file. Granting "
            "access puts a copy in your config directory, readable only by you, "
            "so joining, leaving and the live view stop asking for a password. "
            "It also lets zerotier-cli run without sudo, which is a real "
            "permission: anyone who can read your home directory could connect "
            "this machine to any ZeroTier network."))
        lay.addWidget(access)

        upd = QGroupBox("Updates")
        uform = QFormLayout(upd)
        uform.addRow("This tray", QLabel(f"version {__version__}"))
        self.lbl_update = QLabel("Not checked.")
        self.lbl_update.setWordWrap(True)
        uform.addRow("Latest release", self.lbl_update)
        urow = QHBoxLayout()
        self.btn_check_update = QPushButton("Check for updates")
        self.btn_check_update.clicked.connect(self._check_updates)
        urow.addWidget(self.btn_check_update)
        self.btn_copy_install = QPushButton("Copy the install command")
        self.btn_copy_install.clicked.connect(
            lambda: self._copy_text_value(updates.INSTALL_COMMAND))
        urow.addWidget(self.btn_copy_install)
        urow.addStretch(1)
        uform.addRow("", urow)
        uform.addRow("", _hint(
            "Only when you press the button. Nothing checks on a timer, at "
            "startup or in the background, so in normal use this program makes "
            "no outbound request at all. The check is one unauthenticated GET "
            "to the project's releases endpoint; nothing about your node, your "
            "networks or your peers goes with it. Updating itself is your "
            "package manager's job — this only tells you and hands you the "
            "command."))
        lay.addWidget(upd)

        fw = QGroupBox("Firewall")
        fform = QFormLayout(fw)
        self.fw_zone = QComboBox()
        self.fw_zone.addItem("(the default zone)", "")
        fform.addRow("Zone", self.fw_zone)
        self.lbl_fw = QLabel("-")
        self.lbl_fw.setWordWrap(True)
        fform.addRow("Status", self.lbl_fw)
        frow = QHBoxLayout()
        self.btn_fw_open = QPushButton("Allow the ZeroTier ports")
        self.btn_fw_open.clicked.connect(lambda: self._firewall(True))
        frow.addWidget(self.btn_fw_open)
        self.btn_fw_close = QPushButton("Remove them again")
        self.btn_fw_close.clicked.connect(lambda: self._firewall(False))
        frow.addWidget(self.btn_fw_close)
        frow.addStretch(1)
        fform.addRow("", frow)
        fform.addRow("", _hint(
            "ZeroTier works behind a firewall by relaying, which costs latency. "
            "Letting its UDP ports in on the zone that holds your real network "
            "card lets peers reach you directly instead. The change is written "
            "permanently and applied at once."))
        lay.addWidget(fw)

        lay.addStretch(1)
        return _scroll(page)

    def _copyable_row(self, form: QFormLayout, label: str) -> QLabel:
        """A value with its own Copy button, so an address is one click away.

        The button copies the bare value stashed on the label, never the
        annotated text beside it.
        """
        value = QLabel("-")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        button = QPushButton("Copy")
        button.setFixedWidth(60)
        button.clicked.connect(lambda _c=False, w=value: self._copy_value(w))
        row = QHBoxLayout()
        row.addWidget(value, 1)
        row.addWidget(button)
        form.addRow(label, row)
        return value

    @staticmethod
    def _set_value(label: QLabel, shown: str, bare: str = "") -> None:
        label.setText(shown)
        label.setProperty("bare", bare)

    @staticmethod
    def _copy_value(label: QLabel) -> None:
        from PySide6.QtWidgets import QApplication
        text = label.property("bare") or label.text()
        if text and text != "-":
            QApplication.clipboard().setText(text)

    def _copy_all_addresses(self) -> None:
        from PySide6.QtWidgets import QApplication
        addrs = self.monitor.my_addresses()
        lines = [f"{a}\t{label} (ZeroTier)" for a, label in addrs["zerotier"]]
        lines += [f"{a}\tpublic" for a in addrs["public_v4"] + addrs["public_v6"]]
        lines += [f"{ip}\t{dev}" for ip, dev in addrs["lan"]]
        QApplication.clipboard().setText("\n".join(lines))

    def _check_updates(self) -> None:
        def answered(ok: bool, message: str) -> None:
            self.updates.checked.disconnect(answered)
            self.btn_check_update.setEnabled(True)
            self.btn_check_update.setText("Check for updates")
            self.lbl_update.setText(message)
            self.lbl_update.setStyleSheet(
                "font-weight: bold;" if ok and self.updates.available else "")

        if not self.updates.check():
            return
        self.updates.checked.connect(answered)
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Checking...")

    @staticmethod
    def _copy_text_value(text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _privileged(self, args: list[str]) -> None:
        if not self.priv.run(args):
            self._warn("One at a time",
                       "A password prompt for the last action is still open.")

    def _do_grant(self) -> None:
        if QMessageBox.question(
                self, APP_NAME,
                "Give this user a copy of the ZeroTier API token?\n\n"
                "Anyone who can read your home directory could then connect this "
                "machine to any ZeroTier network.") != QMessageBox.Yes:
            return
        self._privileged(["grant", getpass.getuser()])

    def _do_revoke(self) -> None:
        self._privileged(["revoke", getpass.getuser()])

    def _firewall(self, open_it: bool) -> None:
        zone = self.fw_zone.currentData() or system.firewall_default_zone()
        if not zone:
            self._warn("No zone", "firewalld did not report a usable zone.")
            return
        self._privileged(["firewall", "open" if open_it else "close", zone])

    # ------------------------------------------------------------ load/save
    def load_from_config(self) -> None:
        cfg = self.cfg
        self.style_gallery.set_current(str(cfg.get("icon_style")))
        self.thickness.setValue(float(cfg.get("icon_thickness", 1.0)))
        self.padding.setValue(float(cfg.get("icon_padding", 0.04)))
        self.scale.setValue(float(cfg.get("icon_scale", 1.12)))
        self.state_dot.setChecked(bool(cfg.get("state_dot", True)))
        self.monochrome.setChecked(bool(cfg.get("monochrome", False)))
        self.mono_color.set_color(str(cfg.get("monochrome_color", "#c9d1d9")))

        self.show_badge.setChecked(bool(cfg.get("show_badge", False)))
        _set_data(self.badge_source, cfg.get("badge_source"))
        _set_data(self.badge_style, cfg.get("badge_style"))
        _set_data(self.badge_position, cfg.get("badge_position"))
        self.badge_when_zero.setChecked(bool(cfg.get("badge_when_zero", False)))
        self.badge_color.set_color(str(cfg.get("badge_color", "#0d1117")))
        self.badge_text_color.set_color(str(cfg.get("badge_text_color", "#ffffff")))

        self.anim_speed.setValue(float(cfg.get("animation_speed", 1.0)))
        self.anim_fps.setValue(int(cfg.get("animation_fps", 20)))
        self.animate_idle.setChecked(bool(cfg.get("animate_when_idle", True)))

        for key, _ in STATES:
            self._color_buttons[key].set_color(cfg.color_for(key))
            _set_data(self._anim_combos[key], cfg.animation_for(key))
        self._sync_anim_gallery()

        self.show_roots.setChecked(bool(cfg.get("show_roots", False)))
        self.resolve_ips.setChecked(bool(cfg.get("resolve_member_ips", True)))
        self.member_limit.setValue(int(cfg.get("member_limit", 24)))

        _set_data(self.click_action, cfg.get("click_action"))
        self.show_addresses.setChecked(bool(cfg.get("show_addresses_in_menu", True)))
        self.tooltip_details.setChecked(bool(cfg.get("tooltip_details", True)))
        self.hide_when_stopped.setChecked(bool(cfg.get("hide_when_stopped", False)))
        self.tray_autostart.setChecked(autostart.is_enabled())
        self.start_service_with_tray.setChecked(
            bool(cfg.get("start_service_with_tray", False)))
        self.confirm_stop.setChecked(bool(cfg.get("confirm_stop", True)))
        self.confirm_leave.setChecked(bool(cfg.get("confirm_leave", True)))

        self.notifications_enabled.setChecked(bool(cfg.get("notifications_enabled", True)))
        self.notify_on_service.setChecked(bool(cfg.get("notify_on_service", True)))
        self.notify_on_network.setChecked(bool(cfg.get("notify_on_network", True)))
        self.notify_on_member.setChecked(bool(cfg.get("notify_on_member", False)))
        self.notify_on_denied.setChecked(bool(cfg.get("notify_on_denied", True)))
        self._sync_notif_enabled()

        self.status_poll.setValue(int(cfg.get("status_poll_ms", 2000)))
        self.peer_poll.setValue(int(cfg.get("peer_poll_ms", 5000)))
        self.service_poll.setValue(int(cfg.get("service_poll_ms", 4000)))

        self._load_zones()
        self._sync_previews()
        self._refresh_live()

    def _load_zones(self) -> None:
        want = str(self.cfg.get("firewall_zone", ""))
        self.fw_zone.blockSignals(True)
        self.fw_zone.clear()
        self.fw_zone.addItem("(the default zone)", "")
        for zone in system.firewall_zones():
            self.fw_zone.addItem(zone, zone)
        _set_data(self.fw_zone, want)
        self.fw_zone.blockSignals(False)

    def _collect(self) -> dict:
        colors = {k: b.color() for k, b in self._color_buttons.items()}
        anims = {k: c.currentData() for k, c in self._anim_combos.items()}
        return {
            "icon_style": self.style_gallery.current(),
            "icon_thickness": self.thickness.value(),
            "icon_padding": self.padding.value(),
            "icon_scale": self.scale.value(),
            "state_dot": self.state_dot.isChecked(),
            "monochrome": self.monochrome.isChecked(),
            "monochrome_color": self.mono_color.color(),
            "colors": colors,
            "animations": anims,
            "animation_speed": self.anim_speed.value(),
            "animation_fps": self.anim_fps.value(),
            "animate_when_idle": self.animate_idle.isChecked(),

            "show_badge": self.show_badge.isChecked(),
            "badge_source": self.badge_source.currentData(),
            "badge_style": self.badge_style.currentData(),
            "badge_position": self.badge_position.currentData(),
            "badge_when_zero": self.badge_when_zero.isChecked(),
            "badge_color": self.badge_color.color(),
            "badge_text_color": self.badge_text_color.color(),

            "show_roots": self.show_roots.isChecked(),
            "resolve_member_ips": self.resolve_ips.isChecked(),
            "member_limit": self.member_limit.value(),

            "click_action": self.click_action.currentData(),
            "show_addresses_in_menu": self.show_addresses.isChecked(),
            "tooltip_details": self.tooltip_details.isChecked(),
            "hide_when_stopped": self.hide_when_stopped.isChecked(),
            "start_service_with_tray": self.start_service_with_tray.isChecked(),
            "confirm_stop": self.confirm_stop.isChecked(),
            "confirm_leave": self.confirm_leave.isChecked(),

            "notifications_enabled": self.notifications_enabled.isChecked(),
            "notify_on_service": self.notify_on_service.isChecked(),
            "notify_on_network": self.notify_on_network.isChecked(),
            "notify_on_member": self.notify_on_member.isChecked(),
            "notify_on_denied": self.notify_on_denied.isChecked(),

            "status_poll_ms": self.status_poll.value(),
            "peer_poll_ms": self.peer_poll.value(),
            "service_poll_ms": self.service_poll.value(),

            "firewall_zone": self.fw_zone.currentData() or "",
        }

    def _apply(self) -> bool:
        self.cfg.update(self._collect())
        if not autostart.set_enabled(self.tray_autostart.isChecked()):
            self._warn("Autostart", "Could not write the autostart entry.")
        self.applied.emit()
        return True

    def _ok(self) -> None:
        if self._apply():
            self.accept()

    def _restore(self) -> None:
        if QMessageBox.question(self, APP_NAME,
                                "Put every setting back to its default?") \
                != QMessageBox.Yes:
            return
        self.cfg.reset()
        self.load_from_config()
        self.applied.emit()

    # -------------------------------------------------------------- live view
    def _refresh_live(self) -> None:
        if not self.isVisible():
            return
        snap = self.monitor.snapshot()

        addrs = self.monitor.my_addresses()
        self._set_value(self.lbl_node, snap["address"] or "unknown",
                        snap["address"])
        self._set_value(
            self.lbl_my_ip,
            ", ".join(f"{a} ({label})" for a, label in addrs["zerotier"])
            or "no network has assigned one",
            self.monitor.my_ip())
        self._set_value(self.lbl_public,
                        ", ".join(addrs["public_v4"]) or "not measured yet",
                        addrs["public_v4"][0] if addrs["public_v4"] else "")
        self._set_value(self.lbl_public6,
                        ", ".join(addrs["public_v6"]) or "none",
                        addrs["public_v6"][0] if addrs["public_v6"] else "")
        self._set_value(
            self.lbl_lan,
            ", ".join(f"{ip} ({dev})" for ip, dev in addrs["lan"]) or "unknown",
            addrs["lan"][0][0] if addrs["lan"] else "")
        self.lbl_version.setText(snap["version"] or "unknown")
        self.lbl_online.setText("yes" if snap["online"] else "no root has answered")
        self.lbl_ports.setText(
            "UDP " + ", ".join(str(p) for p in snap["ports"]) +
            ("  (UPnP/NAT-PMP on)" if snap["port_mapping"] else ""))
        self.lbl_unit.setText(
            f"{self.monitor.unit}: {snap['unit_state'] or 'not found'}"
            + ("  ·  TCP relay in use" if snap["tcp_relay"] else ""))

        self.btn_start.setEnabled(snap["installed"] and not snap["running"])
        self.btn_stop.setEnabled(snap["running"])
        self.btn_restart.setEnabled(snap["installed"])
        self.boot_box.blockSignals(True)
        self.boot_box.setChecked(snap["boot_enabled"])
        self.boot_box.setEnabled(snap["installed"])
        self.boot_box.blockSignals(False)

        if snap["has_token"]:
            self.lbl_token.setText(
                f"Reading the token from {snap['token_source']}. "
                + ("The API is answering." if snap["api_ok"]
                   else f"The API is not answering: {snap['api_error']}"))
        else:
            self.lbl_token.setText(
                "No readable token. Networks and members stay blank until this "
                "user is granted access.")
        self.btn_grant.setEnabled(not snap["has_token"] or
                                  snap["token_source"] != "your config directory")
        self.btn_revoke.setEnabled(snap["token_source"] == "your config directory")

        self._refresh_firewall(snap)
        self._fill_networks(snap)
        self._fill_members()

    def _refresh_firewall(self, snap: dict) -> None:
        if not system.firewalld_running():
            self.lbl_fw.setText("firewalld is not running, so there is nothing "
                                "to open.")
            for b in (self.btn_fw_open, self.btn_fw_close):
                b.setEnabled(False)
            return
        zone = self.fw_zone.currentData() or system.firewall_default_zone()
        ports = snap["ports"]
        open_ports = [p for p in ports if system.firewall_port_open(zone, p)]
        self.lbl_fw.setText(
            f"Zone \"{zone}\": "
            + (f"UDP {', '.join(str(p) for p in open_ports)} allowed"
               if open_ports else "none of the ZeroTier ports are allowed")
            + f"  (of {', '.join(str(p) for p in ports)})")
        self.btn_fw_open.setEnabled(len(open_ports) < len(ports))
        self.btn_fw_close.setEnabled(bool(open_ports))

    def _fill_networks(self, snap: dict) -> None:
        nets = snap["networks"]
        table = self.net_table
        keep = {i.row() for i in table.selectedIndexes()}
        table.setRowCount(len(nets))
        for row, net in enumerate(nets):
            values = [
                net.get("name") or "(unnamed)",
                net.get("nwid", ""),
                net.get("status", ""),
                ", ".join(net.get("assignedAddresses") or []) or "-",
                net.get("portDeviceName", "-"),
                net.get("type", ""),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, _cell(value))
        if keep and max(keep) < len(nets):
            table.selectRow(min(keep))
        elif nets and not keep:
            table.selectRow(0)
        self._sync_net_buttons()

    def _fill_members(self) -> None:
        rows: list[tuple[dict, str]] = []
        for net in self.monitor.networks:
            label = net.get("name") or net.get("nwid", "")
            for mem in self.monitor.members_of(net):
                rows.append((mem, label))
        table = self.member_table
        table.setRowCount(len(rows))
        for row, (mem, label) in enumerate(rows):
            path = ("controller" if mem["controller"]
                    else "root" if mem["role"] in ("PLANET", "MOON")
                    else "direct" if mem["direct"]
                    else "relayed" if mem["reachable"] else "unreachable")
            values = [
                mem["ip"] or "-",
                mem["address"],
                "-" if mem["latency"] < 0 else f"{mem['latency']} ms",
                path,
                mem["version"] or "-",
                mem["endpoint"] or "-",
                label,
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, _cell(value))

    # -------------------------------------------------------------- previews
    def _sync_anim_gallery(self) -> None:
        """Tick a motion tile only when every state agrees on it.

        States are free to differ, and usually do, so "none of them" is a real
        answer here - showing one ticked would claim a uniformity that is not
        there.
        """
        chosen = {c.currentData() for c in self._anim_combos.values()}
        self.anim_gallery.set_current(chosen.pop() if len(chosen) == 1 else None)

    def _sync_previews(self) -> None:
        self._sync_anim_gallery()
        self._repaint_galleries()
        style = self.style_gallery.current() or "zerotier"
        mono = self.monochrome.isChecked()
        ctx = icons.RenderCtx(
            members=3,
            networks=1,
            thickness=self.thickness.value(),
            padding=self.padding.value(),
            scale=self.scale.value(),
            badge=self.show_badge.isChecked(),
            badge_text="3",
            badge_style=self.badge_style.currentData() or "circle",
            badge_position=self.badge_position.currentData() or "br",
            badge_color=self.badge_color.color(),
            badge_text_color=self.badge_text_color.color(),
        )
        for key, prev in self._previews.items():
            prev.style_key = style
            prev.color = (self.mono_color.color() if mono
                          else self._color_buttons[key].color())
            prev.animation = self._anim_combos[key].currentData() or "none"
            dot = ""
            if style in icons.FIXED_BRAND_STYLES and self.state_dot.isChecked():
                dot = prev.color
            prev.ctx = icons.RenderCtx(**{**ctx.__dict__, "state_dot": dot})

    # ------------------------------------------------------- live galleries
    def _base_ctx(self) -> icons.RenderCtx:
        """The geometry every preview shares, so they are all comparable."""
        return icons.RenderCtx(
            members=3,
            networks=1,
            thickness=self.thickness.value(),
            padding=self.padding.value(),
            scale=self.scale.value(),
        )

    def _preview_color(self, state: str) -> str:
        if self.monochrome.isChecked():
            return self.mono_color.color()
        button = self._color_buttons.get(state)
        return button.color() if button else icons.ZT_ORANGE

    def render_state(self, state: str, size: int, phase: float):
        """One state, exactly as the tray would paint it. Used by the strip."""
        style = self.style_gallery.current() or "zerotier"
        color = self._preview_color(state)
        ctx = self._base_ctx()
        ctx.badge = self.show_badge.isChecked()
        ctx.badge_text = "3"
        ctx.badge_style = self.badge_style.currentData() or "circle"
        ctx.badge_position = self.badge_position.currentData() or "br"
        ctx.badge_color = self.badge_color.color()
        ctx.badge_text_color = self.badge_text_color.color()
        if style in icons.FIXED_BRAND_STYLES and self.state_dot.isChecked():
            ctx.state_dot = color
        combo = self._anim_combos.get(state)
        animation = (combo.currentData() if combo else "none") or "none"
        return icons.render_pixmap(size, style, color, animation, phase, ctx)

    def _repaint_galleries(self) -> None:
        """Shapes stand still so they compare; motion moves, because that is
        the whole point of looking at it."""
        connected = self._preview_color("connected")
        ctx = self._base_ctx()
        style = self.style_gallery.current() or "zerotier"

        self.style_gallery.repaint_tiles(
            lambda key: icons.render_pixmap(TILE_PX, key, connected, "none", 0.0, ctx))
        self.anim_gallery.repaint_tiles(
            lambda key: icons.render_pixmap(TILE_PX, style, connected, key,
                                            self._phase, ctx))

    def _pick_animation_for_all(self, key: str) -> None:
        for combo in self._anim_combos.values():
            _set_data(combo, key)
        self._sync_previews()

    def _animate(self) -> None:
        if not self.isVisible():
            return
        speed = max(0.1, min(4.0, self.anim_speed.value()))
        self._phase = (self._phase + speed / 40.0) % 1.0
        for prev in self._previews.values():
            prev.paint(self._phase)
        self.strip.phase = self._phase
        self.strip.update()
        if self.tabs.currentIndex() == 0:      # only the Appearance tab is looking
            self._repaint_galleries()

    def _warn(self, title: str, text: str) -> None:
        box = QMessageBox(QMessageBox.Warning, title, text or "Unknown error.", parent=self)
        box.setWindowIcon(icons.app_icon())
        box.exec()


def _set_data(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item


def _tidy_table(table: QTableWidget) -> None:
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
