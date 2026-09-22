# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/styled_dropdown.py
=========================
StyledDropdown: a custom-styled replacement for QComboBox. Started out
living inside settings_widget.py as ThemeDropdown (the Dark/Light theme
picker's own widget, before it needed to be anything more generic than
"exactly two hardcoded options"); pulled out into its own module once
chat_widget.py's topbar model picker needed the SAME look - the point
being one dropdown style used in two different files, not two files
each with their own slightly-different reimplementation of the same
popup mechanics.

QComboBox's native popup view is genuinely hard to fully theme via QSS
alone across platforms - this app has already hit that twice elsewhere
(QKeySequenceEdit needing both QPalette AND setStyleSheet to reliably
paint text on every Qt build; the sidebar nav buttons needing every
single button explicitly listed in the selector or falling back to
native OS chrome) - so rather than keep fighting a native popup's
platform-styled arrow/borders/row-highlight, this builds the popup
itself: a frameless Qt.WindowType.Popup widget positioned under the
button, so every pixel - corners, spacing, hover, selected-row color -
is plain QSS on a QWidget we made, not something we're hoping the
platform style respects.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy,
    QScrollArea, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

import app.theme as theme
import app.icons as icons


class StyledDropdown(QWidget):
    """
    Handles any number of (value, label) options - the theme picker only
    ever has two, a model picker (chat_widget.py's topbar, or
    settings_widget.py's per-AI-profile row) has however many
    ai.providers.base.MODEL_CHOICES does (six as of writing) - but still
    no search or keyboard nav, just a plain popup list; if this ever
    needs to hold dozens of options, that'd be worth revisiting.
    """
    currentValueChanged = pyqtSignal(str)

    # More than this many options and the popup scrolls instead of
    # growing tall enough to run off the bottom of the screen - the
    # model picker (ai.providers.base.MODEL_CHOICES) is the option list
    # most likely to actually hit this as more providers/models get added.
    MAX_VISIBLE_ROWS = 6

    def __init__(self, options: "list[tuple[str, str]]", current: str, theme_name: str, *, min_width: int = 140, expanding: bool = True, parent: "QWidget | None" = None):
        super().__init__(parent)
        self._options = options
        self._value = current
        self._min_width = min_width
        self._popup: "QWidget | None" = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._button = QPushButton()
        self._button.setObjectName("styledDropdownButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        # `expanding=True` (the default) is what settings_widget.py's
        # profile rows want - those add this widget with an explicit
        # stretch factor (row.addWidget(model_dropdown, 1)) so it fills
        # the remaining row width on purpose. The chat topbar's
        # profile/model pickers pass expanding=False instead: sitting
        # right after a stretch spacer, an Expanding policy here was
        # meant to stop them competing for the SAME leftover space that
        # spacer was supposed to soak up.
        #
        # That alone didn't actually fix it, though: this only set the
        # policy on `self._button` - the inner QPushButton. The widget
        # topbarLayout.addWidget() actually sees is `self` (this
        # StyledDropdown QWidget wrapping the button), and a plain
        # QWidget's default sizePolicy is (Preferred, Preferred), which
        # was never touched. Preferred still has Qt's GrowFlag set, so
        # it still shared in the topbar's leftover space alongside the
        # stretch spacer - the button never even had a chance to enforce
        # its own narrower policy, since ITS parent (this widget) was
        # already the one being stretched. That's what "the dropdown is
        # too wide" actually was, not the min-width number, and not
        # (only) the button's own policy. Using Fixed - not Preferred
        # for the non-expanding case on BOTH this widget and the button
        # is what actually stops either of them from growing past their
        # own sizeHint/min-width, regardless of leftover space.
        h_policy = QSizePolicy.Policy.Expanding if expanding else QSizePolicy.Policy.Fixed
        self.setSizePolicy(h_policy, QSizePolicy.Policy.Fixed)
        self._button.setSizePolicy(h_policy, QSizePolicy.Policy.Fixed)
        self._button.clicked.connect(self._toggle_popup)
        layout.addWidget(self._button)

        # Label left, chevron pinned to the far right of the box - was
        # previously just appended right after the label text
        # ("Dark   ▾"), which sits wherever the label's text happens to
        # end, not at the actual right edge of the button. Both child
        # labels are WA_TransparentForMouseEvents so a click anywhere on
        # either of them still reaches the QPushButton underneath instead
        # of being swallowed by the label sitting on top of it.
        btn_layout = QHBoxLayout(self._button)
        btn_layout.setContentsMargins(10, 0, 10, 0)
        self._button_label = QLabel()
        self._button_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        btn_layout.addWidget(self._button_label)
        btn_layout.addStretch()
        self._button_chevron = QLabel()
        self._button_chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        btn_layout.addWidget(self._button_chevron)

        self.apply_theme(theme_name)

    def set_fixed_height(self, height: int) -> None:
        """
        Pins this dropdown's rendered height to an exact pixel value -
        both the outer wrapper widget AND the inner QPushButton, since
        the button (Fixed vertical size policy, sized off its own
        padding+font sizeHint - see apply_theme's stylesheet, which sets
        no explicit height at all) is what's actually visibly drawn as
        "the box", while the outer widget is a zero-margin QHBoxLayout
        wrapper around it; setting only one of the two leaves either an
        invisible mismatch (wrapper taller than the button it contains)
        or no visible effect at all (button ignoring a resize of its
        parent because Fixed policies don't stretch to fill).

        Opt-in and per-instance rather than a change to the shared
        stylesheet in apply_theme(), so it doesn't alter every other
        StyledDropdown call site (chat topbar's model picker, Settings'
        per-profile model picker, the theme picker) - none of which sit
        directly beside another input they need to pixel-match.
        Call this AFTER construction, with the sibling input's own
        rendered height (e.g. `some_line_edit.sizeHint().height()`)
        rather than a hand-guessed number, so it stays correct even if
        that sibling's own padding/border/font ever changes.
        """
        self.setFixedHeight(height)
        self._button.setFixedHeight(height)

    def current_value(self) -> str:
        return self._value

    def set_options(self, options: "list[tuple[str, str]]") -> None:
        """Replaces the option list (e.g. a profile switching provider,
        changing which models are relevant) - doesn't touch the current
        value/colors, just what the popup will show next time it opens."""
        self._options = options
        self._refresh_button_label()

    def set_value(self, value: str) -> None:
        """Selects `value` without touching colors - the counterpart to
        apply_theme() below, kept separate because for a generic dropdown
        (unlike the theme picker, where "selected value" and "app theme"
        happen to be the same thing) picking a value and the app's theme
        changing are two completely unrelated events."""
        self._value = value
        self._refresh_button_label()
        self._close_popup()

    def apply_theme(self, theme_name: str) -> None:
        """Recolors the button/popup to `theme_name`'s palette - doesn't
        touch which value is selected."""
        self._colors = theme.colors(theme_name)
        self._refresh_button_label()
        self._close_popup()

    def _refresh_button_label(self) -> None:
        c = self._colors
        label = dict(self._options).get(self._value, self._value)
        self._button_label.setText(label)
        self._button_label.setStyleSheet(
            f'color: {c["text_primary"]}; background: transparent; font-family: "Segoe UI"; font-size: 12px;'
        )
        self._button_chevron.setPixmap(icons.pixmap("chevron-down", c, size=10, color_key="text_muted"))
        self._button_chevron.setStyleSheet("background: transparent;")
        self._button.setStyleSheet(f"""
            QPushButton#styledDropdownButton {{
                /* bg_input, not bg_panel. In the light theme bg_panel is
                   pure white and the page behind it is #f5f5f7, so the
                   control had almost no edge - it read as a floating
                   label rather than something clickable. bg_input is the
                   token every other field on the page uses, and it is
                   the one that differs from the page in BOTH themes. */
                background-color: {c['bg_input']};
                color: {c['text_primary']};
                /* border, not bg_hover_strong: the hover token is a fill
                   colour and is far too faint to read as a border on a
                   light background. */
                border: 1px solid {c['border']};
                border-radius: 7px;
                padding: 6px 10px;
                font-family: "Segoe UI";
                font-size: 12px;
                min-width: {self._min_width}px;
                text-align: left;
            }}
            QPushButton#styledDropdownButton:hover {{
                border-color: {c['accent']};
                background-color: {c['bg_hover']};
            }}
        """)
        # setStyleSheet() alone doesn't always force an already-visible
        # widget to actually repaint with the new colors right away on
        # every Qt build/platform - this app has hit that exact class of
        # "the stylesheet doesn't seem to apply" bug before (see this
        # module's own docstring on QKeySequenceEdit) - so explicitly
        # unpolish+polish here rather than trust the cascade alone.
        self._button.style().unpolish(self._button)
        self._button.style().polish(self._button)

    def _toggle_popup(self) -> None:
        self._close_popup() if self._popup is not None else self._open_popup()

    def _open_popup(self) -> None:
        c = self._colors
        popup = QWidget(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(popup)
        # Room for the shadow on all four sides - a graphics effect is
        # painted inside the widget's own bounds and is simply clipped
        # where there is no margin for it.
        outer.setContentsMargins(12, 6, 12, 14)

        frame = QWidget(popup)
        frame.setObjectName("styledDropdownFrame")

        # Elevation. In the dark theme a popup separates from the page by
        # being lighter than it; in the light theme it cannot be, because
        # the page is already near-white and the popup is white. A shadow
        # is the only thing that says "this is above the page" in both -
        # without it the light-theme popup merged into the page and the
        # language list looked like it had been pasted onto the form.
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(24)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 90))
        frame.setGraphicsEffect(shadow)

        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(4, 4, 4, 4)
        frame_layout.setSpacing(0)
        outer.addWidget(frame)

        # Rows live inside a QScrollArea rather than directly in `frame`,
        # so a long option list (MODEL_CHOICES, once there are enough
        # providers/models to matter) scrolls instead of growing the
        # popup down off the bottom of the screen.
        rows_widget = QWidget()
        rows_widget.setObjectName("styledDropdownRows")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)

        for value, label in self._options:
            # Bug fix: an option with a blank/whitespace-only label (seen
            # with a corrupted or not-yet-fully-migrated data source
            # e.g. ai.profiles.list_profiles() before its own self-heal
            # existed) used to still get a full row built for it here
            # a real QPushButton, just with nothing visibly in it - so
            # the popup showed one filled row plus one dead blank one
            # per bad entry, with no way to tell what a click on it
            # would even do. Skipping it here is a last line of defense
            # regardless of which caller's data produced it; nothing
            # about it can ever be a meaningful choice to show.
            if not label.strip():
                continue
            row = QPushButton(label)
            row.setObjectName("styledDropdownRow")
            row.setProperty("selected", "true" if value == self._value else "false")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.clicked.connect(lambda _checked=False, v=value: self._select(v))
            rows_layout.addWidget(row)

        scroll = QScrollArea(frame)
        scroll.setObjectName("styledDropdownScroll")
        scroll.setWidget(rows_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        frame_layout.addWidget(scroll)

        frame.setStyleSheet(f"""
            QWidget#styledDropdownFrame {{
                /* bg_elevated is the token for something floating above
                   the page. In the dark theme it is lighter than the
                   page; in the light theme the separation has to come
                   from the border and the shadow instead, since a popup
                   cannot be lighter than white. */
                background-color: {c['bg_elevated']};
                border: 1px solid {c['border_strong']};
                border-radius: 9px;
            }}
            QScrollArea#styledDropdownScroll {{ background: transparent; border: none; }}
            QWidget#styledDropdownRows {{ background: transparent; }}
            QPushButton#styledDropdownRow {{
                background-color: transparent;
                color: {c['text_primary']};
                border: none;
                border-radius: 6px;
                padding: 7px 10px;
                text-align: left;
                font-family: "Segoe UI";
                font-size: 12px;
            }}
            QPushButton#styledDropdownRow:hover {{ background-color: {c['bg_hover']}; }}
            QPushButton#styledDropdownRow[selected="true"] {{
                background-color: rgba({c['accent_rgb']},0.16);
                /* text_primary, not the accent. Accent-on-accent-tint is
                   low contrast in the light theme - the selected row was
                   the hardest one to read, which is the wrong way round.
                   The tint and the weight already say which row it is. */
                color: {c['text_primary']};
                font-weight: 600;
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{
                background: {c['bg_hover_strong']};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)

        # Cap the popup's height at MAX_VISIBLE_ROWS worth of rows
        # measured from an actual row's own sizeHint (post-stylesheet)
        # rather than a guessed pixel number, so this tracks the real
        # row height if the padding/font above ever changes. Counts
        # actual rows built above (rows_layout.count()), not
        # len(self._options) - a blank-label option above adds nothing
        # to rows_layout, so counting self._options here would still
        # reserve height for a row that was never actually added.
        first_row = rows_widget.findChild(QPushButton, "styledDropdownRow")
        if first_row is not None:
            row_h = first_row.sizeHint().height() + rows_layout.spacing()
            visible_rows = min(rows_layout.count(), self.MAX_VISIBLE_ROWS)
            scroll.setFixedHeight(visible_rows * row_h)

        # The popup window is larger than the visible frame inside it,
        # by the margins reserved for the drop shadow. Both the position
        # and the width have to compensate, or the frame lands offset to
        # the right of the button and comes out narrower than it.
        margins = popup.layout().contentsMargins()
        pos = self._button.mapToGlobal(self._button.rect().bottomLeft())
        popup.move(pos.x() - margins.left(), pos.y() + 4 - margins.top())
        popup.setFixedWidth(
            max(self._button.width(), self._min_width)
            + margins.left() + margins.right()
        )
        popup.show()
        self._popup = popup

    def _close_popup(self) -> None:
        if self._popup is not None:
            popup, self._popup = self._popup, None
            popup.close()
            popup.deleteLater()

    def _select(self, value: str) -> None:
        self._close_popup()
        if value == self._value:
            return
        self._value = value
        self._refresh_button_label()
        self.currentValueChanged.emit(value)
