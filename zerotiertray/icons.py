"""Every icon is painted at runtime, so any style/colour/animation combo works.

Nothing here touches ZeroTier: it takes a state, a colour, an animation phase
and a member count, and gives back a QIcon.

The ZeroTier mark itself is Unicode U+23C1 (a bar, a stem and a circle) on a
rounded orange tile - upstream says so in artwork/logo.html, and it is drawn
here as geometry rather than text so it never depends on an installed font.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from .config import ZT_ORANGE

ICON_STYLES = [
    ("zerotier", "ZeroTier mark"),
    ("zerotier_logo", "ZeroTier logo (official colours)"),
    ("zerotier_tile", "ZeroTier tile (state colour)"),
    ("zerotier_round", "ZeroTier mark in a circle"),
    ("zerotier_hex", "ZeroTier mark in a hexagon"),
    ("hexagon", "Hexagon"),
    ("hexagon_solid", "Hexagon (solid)"),
    ("hex_grid", "Honeycomb"),
    ("globe", "Globe"),
    ("globe_solid", "Globe (solid)"),
    ("nodes", "Network nodes"),
    ("mesh", "Mesh"),
    ("link", "Chain link"),
    ("tunnel", "Tunnel"),
    ("shield", "Shield"),
    ("lock", "Padlock"),
    ("signal", "Signal waves"),
    ("bars", "Connection bars"),
    ("arrows", "Up and down"),
    ("cloud", "Cloud"),
    ("plug", "Plug"),
    ("ring", "Ring meter"),
    ("dot", "Dot"),
    ("dot_ring", "Dot with ring"),
    ("led", "LED tile"),
    ("count", "Number only"),
]

# Styles that carry ZeroTier's own colours and so must not be tinted per state.
FIXED_BRAND_STYLES = {"zerotier_logo"}

# Styles that paint right up to their box. Scaling them past the cell only
# crops the corners off, so the fill setting is capped for these.
EDGE_TO_EDGE_STYLES = {"zerotier_logo", "zerotier_tile", "led"}

ANIMATIONS = [
    ("none", "None"),
    ("spin", "Spin"),
    ("spin_slow", "Spin (slow)"),
    ("spin_reverse", "Spin (reverse)"),
    ("pulse", "Pulse (scale)"),
    ("breathe", "Breathe (fade)"),
    ("blink", "Blink"),
    ("flash", "Fast strobe"),
    ("glow", "Glow halo"),
    ("ripple", "Ripple rings"),
    ("sparkle", "Sparkle"),
    ("bounce", "Bounce"),
    ("wobble", "Wobble"),
    ("shimmer", "Shimmer sweep"),
    ("scan", "Scan line"),
    ("orbit", "Orbiting dots"),
    ("heartbeat", "Heartbeat"),
    ("fade_in", "Fade in"),
    ("scale_members", "Grow with the crowd"),
    ("glow_members", "Glow with the crowd"),
    ("rainbow", "Rainbow"),
]

BADGE_STYLES = [
    ("circle", "Circle"),
    ("pill", "Pill"),
    ("plain", "Plain number"),
    ("dot", "Dot only"),
]

BADGE_POSITIONS = [
    ("br", "Bottom right"),
    ("bl", "Bottom left"),
    ("tr", "Top right"),
    ("tl", "Top left"),
]


# --------------------------------------------------------------------------
# render context
# --------------------------------------------------------------------------
@dataclass
class RenderCtx:
    """Everything a style may want to know beyond colour and geometry."""
    members: int = 0
    networks: int = 0
    thickness: float = 1.0
    padding: float = 0.04
    scale: float = 1.0         # fills more or less of the tray cell
    badge: bool = False
    badge_text: str = ""
    badge_style: str = "circle"
    badge_position: str = "br"
    badge_color: str = "#0d1117"
    badge_text_color: str = "#ffffff"
    state_dot: str = ""        # colour of the corner dot, "" for none


@dataclass
class AnimState:
    scale: float = 1.0
    alpha: float = 1.0
    glow: float = 0.0
    rotation: float = 0.0
    dy: float = 0.0
    ripple: float = -1.0       # 0..1 while a ring expands, <0 when unused
    sparkle: float = 0.0       # 0..1 twinkle intensity
    shimmer: float = -1.0      # 0..1 sweep position, <0 when unused
    scan: float = -1.0         # 0..1 scan line position, <0 when unused
    hue_shift: float = 0.0     # degrees
    orbit: float = -1.0        # 0..1 orbit angle, <0 when unused
    extra: dict = field(default_factory=dict)


def anim_state(animation: str, phase: float, members: int = 0) -> AnimState:
    """Turn (animation, phase 0..1, member count) into drawing params."""
    t = phase % 1.0
    wave = 0.5 + 0.5 * math.sin(t * 2 * math.pi)
    crowd = min(1.0, members / 12.0)
    st = AnimState()

    if animation == "spin":
        st.rotation = t * 360.0
    elif animation == "spin_slow":
        st.rotation = t * 180.0
    elif animation == "spin_reverse":
        st.rotation = -t * 360.0
    elif animation == "pulse":
        st.scale = 0.88 + 0.22 * wave
    elif animation == "breathe":
        st.alpha = 0.45 + 0.55 * wave
        st.scale = 0.95 + 0.07 * wave
    elif animation == "blink":
        st.alpha = 1.0 if t < 0.5 else 0.18
    elif animation == "flash":
        st.alpha = 1.0 if (t * 4.0) % 1.0 < 0.5 else 0.15
    elif animation == "glow":
        st.glow = 0.25 + 0.75 * wave
    elif animation == "ripple":
        st.ripple = t
        st.scale = 0.94
    elif animation == "sparkle":
        st.sparkle = wave
        st.glow = 0.30 * wave
    elif animation == "bounce":
        st.dy = -abs(math.sin(t * math.pi)) * 0.16
    elif animation == "wobble":
        st.rotation = 16.0 * math.sin(t * 2 * math.pi)
    elif animation == "shimmer":
        st.shimmer = t
    elif animation == "scan":
        st.scan = t
    elif animation == "orbit":
        st.orbit = t
    elif animation == "heartbeat":
        # two quick beats, then a rest
        if t < 0.14:
            st.scale = 1.0 + 0.26 * math.sin(t / 0.14 * math.pi)
        elif t < 0.30:
            st.scale = 1.0 + 0.16 * math.sin((t - 0.14) / 0.16 * math.pi)
        else:
            st.scale = 1.0
    elif animation == "fade_in":
        st.alpha = min(1.0, t * 1.8)
        st.scale = 0.72 + 0.28 * min(1.0, t * 1.4)
    elif animation == "scale_members":
        st.scale = 0.80 + 0.35 * crowd
        st.glow = 0.20 * crowd
    elif animation == "glow_members":
        st.glow = 0.15 + 0.85 * crowd
        st.alpha = 0.75 + 0.25 * crowd
    elif animation == "rainbow":
        st.hue_shift = t * 360.0

    return st


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _pen(color: QColor, width: float, cap=Qt.RoundCap) -> QPen:
    pen = QPen(color)
    pen.setWidthF(max(0.8, width))
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _shift_hue(color: QColor, degrees: float) -> QColor:
    if not degrees:
        return color
    h, s, v, a = color.getHsv()
    if h < 0:            # achromatic; give it something to shift
        h, s = 32, max(s, 140)
    return QColor.fromHsv(int((h + degrees) % 360), s, v, a)


def _poly(cx: float, cy: float, r: float, n: int, rot: float = 0.0) -> list[QPointF]:
    return [
        QPointF(cx + r * math.cos(math.radians(rot + i * 360.0 / n)),
                cy + r * math.sin(math.radians(rot + i * 360.0 / n)))
        for i in range(n)
    ]


def _fmt_count(n: int) -> str:
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


# --------------------------------------------------------------------------
# the ZeroTier mark
# --------------------------------------------------------------------------
# Proportions measured off artwork/ZeroTierIcon.png upstream: a full-width bar
# near the top, a stem down the middle, and a circle hung on the stem.
_BAR_Y = 0.142
_BAR_X0 = 0.110
_BAR_X1 = 0.890
_STEM_Y1 = 0.902
_CIRCLE_CY = 0.515
_CIRCLE_R = 0.243
_MARK_STROKE = 0.030


def _draw_zt_mark(p: QPainter, rect: QRectF, color: QColor,
                  weight: float = 1.0) -> None:
    s = min(rect.width(), rect.height())
    x0 = rect.center().x() - s / 2.0
    y0 = rect.center().y() - s / 2.0
    cx = x0 + s * 0.497
    stroke = max(1.0, s * _MARK_STROKE * weight)

    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(color, stroke, cap=Qt.FlatCap))
    bar_y = y0 + s * _BAR_Y
    p.drawLine(QPointF(x0 + s * _BAR_X0, bar_y), QPointF(x0 + s * _BAR_X1, bar_y))
    p.drawLine(QPointF(cx, bar_y), QPointF(cx, y0 + s * _STEM_Y1))
    p.setPen(_pen(color, stroke))
    r = s * _CIRCLE_R
    p.drawEllipse(QPointF(cx, y0 + s * _CIRCLE_CY), r, r)


def _draw_zt_tile(p: QPainter, rect: QRectF, tile: QColor, glyph: QColor,
                  weight: float = 1.0) -> None:
    s = min(rect.width(), rect.height())
    p.setPen(Qt.NoPen)
    p.setBrush(tile)
    p.drawRoundedRect(rect, s * 0.115, s * 0.115)
    inner = QRectF(rect.left() + s * 0.06, rect.top() + s * 0.06,
                   s * 0.88, s * 0.88)
    _draw_zt_mark(p, inner, glyph, weight)


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------
def _paint_style(p: QPainter, style: str, rect: QRectF, color: QColor,
                 ctx: RenderCtx, st: AnimState) -> None:
    cx, cy = rect.center().x(), rect.center().y()
    r = rect.width() / 2.0
    w = max(1.0, rect.width() * 0.085 * ctx.thickness)
    p.setPen(_pen(color, w))
    p.setBrush(Qt.NoBrush)

    if style == "zerotier":
        _draw_zt_mark(p, rect, color, ctx.thickness)
    elif style == "zerotier_logo":
        tile = QColor(ZT_ORANGE)
        tile.setAlphaF(color.alphaF())
        glyph = QColor("#000000")
        glyph.setAlphaF(color.alphaF())
        _draw_zt_tile(p, rect, tile, glyph, ctx.thickness)
    elif style == "zerotier_tile":
        glyph = QColor("#000000" if color.lightnessF() > 0.5 else "#ffffff")
        glyph.setAlphaF(color.alphaF())
        _draw_zt_tile(p, rect, color, glyph, ctx.thickness)
    elif style == "zerotier_round":
        p.drawEllipse(rect)
        inner = QRectF(cx - r * 0.62, cy - r * 0.62, r * 1.24, r * 1.24)
        _draw_zt_mark(p, inner, color, ctx.thickness * 0.9)
    elif style == "zerotier_hex":
        p.drawPolygon(_poly(cx, cy, r, 6, rot=-90))
        inner = QRectF(cx - r * 0.55, cy - r * 0.55, r * 1.10, r * 1.10)
        _draw_zt_mark(p, inner, color, ctx.thickness * 0.9)
    elif style == "hexagon":
        p.drawPolygon(_poly(cx, cy, r, 6, rot=-90))
    elif style == "hexagon_solid":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(_poly(cx, cy, r, 6, rot=-90))
    elif style == "hex_grid":
        cell = r * 0.36
        p.setPen(_pen(color, w * 0.55))
        p.drawPolygon(_poly(cx, cy, cell, 6, rot=-90))
        for i in range(6):
            a = math.radians(i * 60.0 - 90.0)
            p.drawPolygon(_poly(cx + cell * 1.75 * math.cos(a),
                                cy + cell * 1.75 * math.sin(a), cell, 6, rot=-90))
    elif style in ("globe", "globe_solid"):
        if style == "globe_solid":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(rect)
            p.setBrush(Qt.NoBrush)
            grid = QColor(0, 0, 0)
            grid.setAlphaF(0.55 * color.alphaF())
            p.setPen(_pen(grid, w * 0.55))
        else:
            p.drawEllipse(rect)
            p.setPen(_pen(color, w * 0.55))
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        for f in (0.45,):
            p.drawArc(QRectF(cx - r * f, cy - r, r * 2 * f, r * 2), 0, 360 * 16)
        p.drawEllipse(QRectF(cx - r, cy - r * 0.5, r * 2, r))
    elif style == "nodes":
        hub = QPointF(cx, cy)
        ring = _poly(cx, cy, r * 0.78, 5, rot=-90)
        p.setPen(_pen(color, w * 0.5))
        for pt in ring:
            p.drawLine(hub, pt)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(hub, r * 0.22, r * 0.22)
        for pt in ring:
            p.drawEllipse(pt, r * 0.14, r * 0.14)
    elif style == "mesh":
        pts = _poly(cx, cy, r * 0.82, 6, rot=-90)
        p.setPen(_pen(color, w * 0.45))
        for i, a in enumerate(pts):
            for b in pts[i + 1:]:
                p.drawLine(a, b)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for pt in pts:
            p.drawEllipse(pt, r * 0.13, r * 0.13)
    elif style == "link":
        # two capsules on a diagonal joined by a bar; filled rather than
        # stroked, because an outlined ring closes up at 22 px
        hh = r * 0.27
        p.save()
        p.translate(cx, cy)
        p.rotate(-45)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(-r * 0.96, -hh, r * 0.80, hh * 2), hh, hh)
        p.drawRoundedRect(QRectF(r * 0.16, -hh, r * 0.80, hh * 2), hh, hh)
        p.drawRect(QRectF(-r * 0.28, -hh * 0.44, r * 0.56, hh * 0.88))
        p.restore()
    elif style == "tunnel":
        p.setPen(_pen(color, w * 0.6))
        for i, f in enumerate((1.0, 0.68, 0.38)):
            c = QColor(color)
            c.setAlphaF(color.alphaF() * (1.0 - i * 0.22))
            p.setPen(_pen(c, w * 0.6))
            p.drawEllipse(QRectF(cx - r * f, cy - r * f * 0.82, r * 2 * f, r * 1.64 * f))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), r * 0.16, r * 0.16)
    elif style == "shield":
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.lineTo(cx + r * 0.82, cy - r * 0.55)
        path.lineTo(cx + r * 0.82, cy + r * 0.20)
        path.quadTo(cx + r * 0.72, cy + r * 0.82, cx, cy + r)
        path.quadTo(cx - r * 0.72, cy + r * 0.82, cx - r * 0.82, cy + r * 0.20)
        path.lineTo(cx - r * 0.82, cy - r * 0.55)
        path.closeSubpath()
        p.drawPath(path)
        inner = QRectF(cx - r * 0.44, cy - r * 0.50, r * 0.88, r * 0.88)
        _draw_zt_mark(p, inner, color, ctx.thickness * 0.8)
    elif style == "lock":
        body = QRectF(cx - r * 0.66, cy - r * 0.10, r * 1.32, r * 0.92)
        p.setPen(_pen(color, w * 0.85))
        p.drawArc(QRectF(cx - r * 0.40, cy - r * 0.78, r * 0.80, r * 0.86),
                  0, 180 * 16)
        p.drawLine(QPointF(cx - r * 0.40, cy - r * 0.35), QPointF(cx - r * 0.40, cy - r * 0.10))
        p.drawLine(QPointF(cx + r * 0.40, cy - r * 0.35), QPointF(cx + r * 0.40, cy - r * 0.10))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(body, r * 0.18, r * 0.18)
    elif style == "signal":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), r * 0.20, r * 0.20)
        p.setBrush(Qt.NoBrush)
        for i, f in enumerate((0.48, 0.74, 1.0)):
            c = QColor(color)
            c.setAlphaF(color.alphaF() * (1.0 - i * 0.26))
            p.setPen(_pen(c, w * 0.62))
            p.drawArc(QRectF(cx - r * f, cy - r * f, r * 2 * f, r * 2 * f), -60 * 16, 120 * 16)
            p.drawArc(QRectF(cx - r * f, cy - r * f, r * 2 * f, r * 2 * f), 120 * 16, 120 * 16)
    elif style == "bars":
        n = 5
        lit = 0
        if ctx.members > 0:
            lit = max(1, min(n, int(math.log10(max(1, ctx.members)) * 2.2) + 1))
        bw = rect.width() / (n * 1.7)
        for i in range(n):
            h = rect.height() * (0.26 + 0.155 * i)
            x = rect.left() + i * bw * 1.7
            y = rect.bottom() - h
            c = QColor(color)
            if i >= lit:
                c.setAlphaF(color.alphaF() * 0.22)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, y, bw, h), bw * 0.3, bw * 0.3)
    elif style == "arrows":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for sign, dx in ((-1.0, -0.34), (1.0, 0.34)):
            path = QPainterPath()
            tipy = cy + sign * r * 0.86
            path.moveTo(cx + r * dx, tipy)
            path.lineTo(cx + r * dx - r * 0.30, tipy - sign * r * 0.42)
            path.lineTo(cx + r * dx - r * 0.13, tipy - sign * r * 0.42)
            path.lineTo(cx + r * dx - r * 0.13, cy - sign * r * 0.86)
            path.lineTo(cx + r * dx + r * 0.13, cy - sign * r * 0.86)
            path.lineTo(cx + r * dx + r * 0.13, tipy - sign * r * 0.42)
            path.lineTo(cx + r * dx + r * 0.30, tipy - sign * r * 0.42)
            path.closeSubpath()
            p.drawPath(path)
    elif style == "cloud":
        def blob(px, py, pr):
            q = QPainterPath()
            q.addEllipse(QPointF(px, py), pr, pr)
            return q
        path = blob(cx - r * 0.40, cy + r * 0.10, r * 0.40)
        path = path.united(blob(cx + r * 0.38, cy + r * 0.12, r * 0.36))
        path = path.united(blob(cx - r * 0.02, cy - r * 0.20, r * 0.50))
        box = QPainterPath()
        box.addRect(QRectF(cx - r * 0.80, cy + r * 0.06, r * 1.58, r * 0.44))
        path = path.united(box)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
    elif style == "plug":
        p.setPen(_pen(color, w * 0.8))
        p.drawLine(QPointF(cx - r * 0.30, cy - r * 0.92), QPointF(cx - r * 0.30, cy - r * 0.30))
        p.drawLine(QPointF(cx + r * 0.30, cy - r * 0.92), QPointF(cx + r * 0.30, cy - r * 0.30))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(cx - r * 0.62, cy - r * 0.32, r * 1.24, r * 0.62),
                          r * 0.16, r * 0.16)
        path = QPainterPath()
        path.moveTo(cx - r * 0.48, cy + r * 0.30)
        path.quadTo(cx, cy + r * 1.05, cx + r * 0.48, cy + r * 0.30)
        path.closeSubpath()
        p.drawPath(path)
    elif style == "ring":
        frac = min(1.0, ctx.members / 12.0) if ctx.members else 0.0
        track = QColor(color)
        track.setAlphaF(color.alphaF() * 0.25)
        band = QRectF(cx - r * 0.86, cy - r * 0.86, r * 1.72, r * 1.72)
        p.setPen(_pen(track, w))
        p.drawEllipse(band)
        p.setPen(_pen(color, w))
        p.drawArc(band, 90 * 16, -int(360 * 16 * frac))
    elif style == "dot":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), r * 0.62, r * 0.62)
    elif style == "dot_ring":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), r * 0.40, r * 0.40)
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(color, w * 0.7))
        p.drawEllipse(QPointF(cx, cy), r * 0.84, r * 0.84)
    elif style == "led":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(rect.adjusted(r * 0.1, r * 0.1, -r * 0.1, -r * 0.1),
                          r * 0.32, r * 0.32)
    elif style == "count":
        text = _fmt_count(ctx.members)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(8, int(rect.height() * (0.92 if len(text) < 2 else 0.70))))
        p.setFont(f)
        p.setPen(color)
        p.drawText(rect, Qt.AlignCenter, text)
    else:
        _draw_zt_mark(p, rect, color, ctx.thickness)


# --------------------------------------------------------------------------
# badge and state dot
# --------------------------------------------------------------------------
def _paint_badge(p: QPainter, size: int, ctx: RenderCtx, state_color: QColor) -> None:
    if not ctx.badge or not ctx.badge_text:
        return
    text = ctx.badge_text
    bs = ctx.badge_style
    d = size * (0.46 if bs != "dot" else 0.30)
    wide = bs == "pill" and len(text) > 1
    bw = d * (1.55 if wide else 1.0)

    pos = ctx.badge_position
    x = size - bw if pos in ("br", "tr") else 0.0
    y = size - d if pos in ("br", "bl") else 0.0
    box = QRectF(x, y, bw, d)

    bg = QColor(ctx.badge_color)
    fg = QColor(ctx.badge_text_color)

    if bs == "dot":
        p.setPen(Qt.NoPen)
        p.setBrush(state_color)
        p.drawEllipse(box)
        return

    if bs != "plain":
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(box, d / 2, d / 2)
        ring = QPen(state_color)
        ring.setWidthF(max(1.0, size * 0.035))
        p.setPen(ring)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, d / 2, d / 2)

    f = QFont()
    f.setBold(True)
    f.setPixelSize(max(7, int(d * (0.68 if len(text) < 3 else 0.52))))
    p.setFont(f)
    p.setPen(fg if bs != "plain" else state_color)
    p.drawText(box, Qt.AlignCenter, text)


def _paint_state_dot(p: QPainter, size: int, color: str) -> None:
    """A corner pip, so a brand-coloured icon still says what the state is."""
    if not color:
        return
    d = size * 0.34
    box = QRectF(size - d, size - d, d, d)
    ring = QColor("#0d1117")
    ring.setAlphaF(0.75)
    p.setPen(Qt.NoPen)
    p.setBrush(ring)
    p.drawEllipse(box)
    p.setBrush(QColor(color))
    p.drawEllipse(box.adjusted(d * 0.17, d * 0.17, -d * 0.17, -d * 0.17))


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------
def render_pixmap(size: int, style: str, color: str | QColor, animation: str,
                  phase: float, ctx: RenderCtx | None = None) -> QPixmap:
    ctx = ctx or RenderCtx()
    st = anim_state(animation, phase, ctx.members)

    base = QColor(color)
    base = _shift_hue(base, st.hue_shift)
    base.setAlphaF(max(0.0, min(1.0, base.alphaF() * st.alpha)))

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    pad = size * max(0.0, min(0.30, ctx.padding))
    inner = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    cx, cy = size / 2.0, size / 2.0

    # glow halo, painted underneath everything
    if st.glow > 0.01:
        halo = QRadialGradient(QPointF(cx, cy), size / 2.0)
        g = QColor(base)
        g.setAlphaF(0.55 * st.glow)
        halo.setColorAt(0.0, g)
        g2 = QColor(base)
        g2.setAlphaF(0.0)
        halo.setColorAt(1.0, g2)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(0, 0, size, size))

    # expanding ripple ring
    if st.ripple >= 0.0:
        rc = QColor(base)
        rc.setAlphaF(base.alphaF() * max(0.0, 1.0 - st.ripple))
        pen = QPen(rc)
        pen.setWidthF(max(1.0, size * 0.05))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rr = (size / 2.0) * (0.35 + 0.65 * st.ripple)
        p.drawEllipse(QPointF(cx, cy), rr, rr)

    p.save()
    p.translate(cx, cy + st.dy * size)
    if st.rotation:
        p.rotate(st.rotation)
    fill = max(0.3, min(2.0, ctx.scale))
    if style in EDGE_TO_EDGE_STYLES:
        fill = min(fill, 1.0)
    base_scale = st.scale * fill
    p.scale(base_scale, base_scale)
    p.translate(-cx, -cy)
    _paint_style(p, style, inner, base, ctx, st)
    p.restore()

    # sparkle: a few twinkling points around the mark
    if st.sparkle > 0.02:
        sc = QColor(255, 255, 255)
        sc.setAlphaF(0.85 * st.sparkle)
        p.setPen(Qt.NoPen)
        p.setBrush(sc)
        for i in range(3):
            a = math.radians(i * 120.0 - 60.0)
            rr = size * 0.34
            p.drawEllipse(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)),
                          size * 0.045 * st.sparkle, size * 0.045 * st.sparkle)

    # shimmer: a bright diagonal sweep
    if st.shimmer >= 0.0:
        p.save()
        p.setClipRect(QRectF(0, 0, size, size))
        sc = QColor(255, 255, 255)
        sc.setAlphaF(0.30)
        p.setPen(Qt.NoPen)
        p.setBrush(sc)
        x = -size * 0.4 + st.shimmer * size * 1.8
        p.translate(x, 0)
        p.rotate(20)
        p.drawRect(QRectF(0, -size * 0.3, size * 0.16, size * 1.6))
        p.restore()

    # scan: a horizontal line sweeping top to bottom
    if st.scan >= 0.0:
        sc = QColor(base)
        sc.setAlphaF(0.75)
        pen = QPen(sc)
        pen.setWidthF(max(1.0, size * 0.055))
        p.setPen(pen)
        y = size * st.scan
        p.drawLine(QPointF(0, y), QPointF(size, y))

    # orbit: dots circling the mark, one per member up to eight
    if st.orbit >= 0.0:
        dots = max(1, min(8, ctx.members or 1))
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        for i in range(dots):
            a = math.radians(st.orbit * 360.0 + i * 360.0 / dots)
            p.drawEllipse(QPointF(cx + size * 0.42 * math.cos(a),
                                  cy + size * 0.42 * math.sin(a)),
                          size * 0.055, size * 0.055)

    if style != "count":
        _paint_badge(p, size, ctx, base)
    if ctx.state_dot and not ctx.badge:
        _paint_state_dot(p, size, ctx.state_dot)
    p.end()
    return pm


# Hand the tray a pixmap at each size it may ask for, so it never has to
# downscale a 64px one into a 22px cell and blur it.
TRAY_SIZES = (22, 24, 32, 48, 64, 128)


def render_icon(style: str, color: str | QColor, animation: str, phase: float,
                ctx: RenderCtx | None = None, sizes=TRAY_SIZES) -> QIcon:
    icon = QIcon()
    for s in sizes:
        icon.addPixmap(render_pixmap(s, style, color, animation, phase, ctx))
    return icon


def app_icon(size: int = 128) -> QIcon:
    """The window/desktop icon: ZeroTier's own logo, no animation, no badge."""
    return QIcon(render_pixmap(size, "zerotier_logo", ZT_ORANGE, "none", 0.0,
                               RenderCtx(padding=0.02, scale=1.0)))
