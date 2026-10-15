# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/icons.py
=============
Every icon in the app. They are Kestrel's own, drawn as vector SVGs in
assets/icons/: a 24x24 grid, 2px strokes with round caps and joins, and
a filled shape only where the filled/empty difference means something
(a pinned pin, a starred star, play, record).

They used to be two borrowed sets: Font Awesome glyphs through the
qtawesome icon font, and a separate batch of Font Awesome SVGs exported
to assets/ with a fixed light-grey fill, which went invisible in the
light theme. One set, drawn to one grid and tinted from the theme, is
what makes them look like they belong together.

Rendered as vectors at whatever size and screen scale they are shown
at, so they stay sharp at 125%, 150% and 200%. Each SVG is drawn in
`currentColor`, which is swapped for the theme colour asked for.

Usage:
    from app import icons
    btn.setIcon(icons.icon("copy", c))          # c = a theme.colors(...) dict
    label.setPixmap(icons.pixmap("lock", c, size=14))

To add one: draw it on the same grid, save it as assets/icons/<name>.svg
with stroke/fill "currentColor", and call it by <name>.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QByteArray, QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from paths import BASE_PATH

ICON_DIR = os.path.join(BASE_PATH, "assets", "icons")

# Opacity of an icon on a disabled control.
_DISABLED_OPACITY = 0.4

_sources: dict = {}
_icons: dict = {}


def has(name: str) -> bool:
    """Whether there is an icon called `name`. For names that come from
    stored data rather than from code, like a [[SYSTEM:<icon>|...]] note
    saved in a chat, where an unknown name must not break loading it."""
    return name in _sources or os.path.isfile(os.path.join(ICON_DIR, f"{name}.svg"))


def available() -> list:
    """Every icon name there is, for tests and for anyone adding a button."""
    try:
        return sorted(f[:-4] for f in os.listdir(ICON_DIR) if f.endswith(".svg"))
    except OSError:
        return []


def _source(name: str) -> str:
    text = _sources.get(name)
    if text is None:
        path = os.path.join(ICON_DIR, f"{name}.svg")
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise KeyError(
                f"No icon named {name!r}: there is no {path}. Draw it on the "
                f"24x24 grid the others use, in currentColor, and save it there."
            ) from None
        _sources[name] = text
    return text


class _SvgIconEngine(QIconEngine):
    """
    Draws one tinted SVG at exactly the size asked for. QIcon asks for
    size x device pixel ratio on a scaled screen, so this renders at the
    physical pixel size instead of stretching a small bitmap.
    """

    def __init__(self, svg: bytes, svg_on: "bytes | None" = None):
        super().__init__()
        self._svg = svg
        self._svg_on = svg_on
        self._renderer = QSvgRenderer(QByteArray(svg))
        # A checked button (a selected sidebar page) asks for the On state.
        self._renderer_on = QSvgRenderer(QByteArray(svg_on)) if svg_on else self._renderer

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(painter.opacity() * _DISABLED_OPACITY)
        renderer = self._renderer_on if state == QIcon.State.On else self._renderer
        renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        return result

    def clone(self) -> "QIconEngine":
        return _SvgIconEngine(self._svg, self._svg_on)


def _tinted(name: str, color: str) -> bytes:
    return _source(name).replace("currentColor", color).encode("utf-8")


def icon(name: str, colors: dict, color_key: str = "text_primary", checked_color_key: "str | None" = None) -> QIcon:
    """
    A QIcon for `name` in colors[color_key] (a theme.colors(...) dict), so
    it follows the theme like everything else. checked_color_key gives a
    checkable button a second colour for when it is checked, the way the
    sidebar's current page is brighter than the others.
    """
    color = colors[color_key]
    color_on = colors[checked_color_key] if checked_color_key else None
    key = (name, color, color_on)
    cached = _icons.get(key)
    if cached is None:
        svg_on = _tinted(name, color_on) if color_on else None
        cached = QIcon(_SvgIconEngine(_tinted(name, color), svg_on))
        _icons[key] = cached
    return QIcon(cached)


def pixmap(name: str, colors: dict, size: int = 14, color_key: str = "text_primary") -> QPixmap:
    """Same as icon(), as a QPixmap `size` pixels square (logical pixels;
    it carries the screen's scale so a QLabel shows it sharp)."""
    return icon(name, colors, color_key).pixmap(size, size)


def tinted_file(name: str, color: str) -> str:
    """
    The icon as an SVG file in `color`, for a style sheet's image:
    property (a check box's tick), which takes a file rather than a
    QIcon. Written once to the user data folder and reused. A forward-
    slash path, as style sheets want it; "" when it couldn't be written.
    """
    from paths import USER_DATA_PATH

    folder = os.path.join(USER_DATA_PATH, "cache", "icons")
    path = os.path.join(folder, f"{name}-{color.lstrip('#').lower()}.svg")
    if not os.path.isfile(path):
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(_tinted(name, color))
        except (OSError, KeyError):
            return ""
    return path.replace("\\", "/")
