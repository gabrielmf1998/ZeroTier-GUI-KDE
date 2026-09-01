#!/usr/bin/env python3
"""Regenerate the application icons from the painter in zerotiertray.icons."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from zerotiertray import icons  # noqa: E402
from zerotiertray.config import ZT_ORANGE  # noqa: E402

SIZES = (48, 64, 128, 256, 512)
NAME = "zerotier-tray"

# The same geometry the painter uses, so the SVG and the PNGs cannot drift.
SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <rect x="0" y="0" width="128" height="128" rx="14.7" ry="14.7" fill="{tile}"/>
  <g fill="none" stroke="{glyph}" stroke-width="{sw:.2f}" stroke-linecap="butt">
    <line x1="{bx0:.2f}" y1="{by:.2f}" x2="{bx1:.2f}" y2="{by:.2f}"/>
    <line x1="{cx:.2f}" y1="{by:.2f}" x2="{cx:.2f}" y2="{sy:.2f}"/>
    <circle cx="{cx:.2f}" cy="{ccy:.2f}" r="{cr:.2f}" stroke-linecap="round"/>
  </g>
</svg>
"""


def svg_text() -> str:
    s = 128.0 * 0.88          # the painter insets the glyph by 6% each side
    o = (128.0 - s) / 2.0
    return SVG.format(
        tile=ZT_ORANGE, glyph="#000000",
        sw=s * icons._MARK_STROKE,
        bx0=o + s * icons._BAR_X0, bx1=o + s * icons._BAR_X1,
        by=o + s * icons._BAR_Y, sy=o + s * icons._STEM_Y1,
        cx=o + s * 0.497, ccy=o + s * icons._CIRCLE_CY, cr=s * icons._CIRCLE_R)


def main() -> int:
    QGuiApplication([])
    for size in SIZES:
        pm = icons.render_pixmap(size, "zerotier_logo", ZT_ORANGE, "none", 0.0,
                                 icons.RenderCtx(padding=0.02))
        out = HERE / f"{NAME}-{size}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out.name}")
    svg = HERE / f"{NAME}.svg"
    svg.write_text(svg_text(), encoding="utf-8")
    print(f"wrote {svg.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
