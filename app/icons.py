# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/icons.py
=============
Icon helper for the whole app - QIcon/QPixmap built from qtawesome
(Font Awesome Free + Material Design Icons, both bundled with the
package under permissive open-source licenses: Font Awesome Free is
SIL OFL 1.1 for the fonts / CC BY 4.0 for the icons / MIT for the code,
Material Design Icons is Apache 2.0). Used instead of Unicode emoji
characters throughout the app - emoji render inconsistently across
platforms/fonts and read as a generic, unpolished "default app" look;
these are the same crisp, consistent, purpose-built icons Kestrel already
uses for the sidebar (see assets/*.svg, which is the same Font Awesome
Free set, just pre-exported as static files instead of via this font).

Usage:
    from app.icons import icon, pixmap
    btn.setIcon(icon("copy", c))          # c = a theme.colors(...) dict
    label.setPixmap(pixmap("lock", c, size=14))
"""

import qtawesome as qta
from PyQt6.QtGui import QIcon, QPixmap

# Semantic name -> qtawesome identifier. Centralized so call sites never
# hardcode a raw "fa5s.whatever" string - if an icon ever needs to change,
# it changes once, here.
_ICON_MAP = {
    "copy":        "fa5s.copy",
    "speaker":     "fa5s.volume-up",
    "edit":        "fa5s.pen",
    "trash":       "fa5s.trash-alt",
    "close":       "fa5s.times",
    "lock":        "fa5s.lock",
    "camera":      "fa5s.camera",
    "network-off": "fa5s.wifi",          # paired with a warning color at the call site - qtawesome's free set has no built-in "slash" wifi variant
    "sparkles":    "fa5s.magic",
    "file":        "fa5s.file-alt",
    "folder-open": "fa5s.folder-open",
    "image":       "fa5s.file-image",
    "stop":        "fa5s.stop-circle",
    "play":        "fa5s.play",
    "dots":        "fa5s.ellipsis-h",
    "quit":        "fa5s.power-off",
    "new-chat":    "fa5s.edit",
    "window":      "fa5s.window-restore",
    "record":      "fa5s.circle",
    "search":      "fa5s.search",
    "settings":    "fa5s.cog",
    "pin":         "fa5s.thumbtack",
    "star":        "fa5s.star",
    "share":       "fa5s.share-square",
    "plus":        "fa5s.plus",
    "arrow-up":    "fa5s.arrow-up",
    "arrow-down":  "fa5s.arrow-down",
    "chevron-up":  "fa5s.chevron-up",
    "chevron-down":"fa5s.chevron-down",
    "mic":         "fa5s.microphone",
    "bolt":        "fa5s.bolt",
    "target":      "fa5s.bullseye",
    "terminal":    "fa5s.terminal",
    "refresh":     "fa5s.sync-alt",
    "check":       "fa5s.check",
    "warning":     "fa5s.exclamation-triangle",
    "dice":        "fa5s.dice",
    "brain":       "fa5s.brain",
}


def _spec(name: str) -> str:
    try:
        return _ICON_MAP[name]
    except KeyError:
        raise KeyError(
            f"No icon registered for {name!r} - add it to app.icons._ICON_MAP "
            f"(pick a Font Awesome Free solid name from https://fontawesome.com/search?o=r&s=solid&f=classic)"
        )


def icon(name: str, colors: dict, color_key: str = "text_primary") -> QIcon:
    """Returns a QIcon for `name`, tinted with colors[color_key] (a theme.colors(...) dict) so it matches the current theme like everything else."""
    return qta.icon(_spec(name), color=colors[color_key])


def pixmap(name: str, colors: dict, size: int = 14, color_key: str = "text_primary") -> QPixmap:
    """Same as icon(), rendered straight to a QPixmap at `size`x`size` - for QLabel.setPixmap() where a plain QIcon doesn't apply."""
    return icon(name, colors, color_key).pixmap(size, size)
