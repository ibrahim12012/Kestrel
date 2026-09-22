# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/name_gate_widget.py
==========================
The "name your assistant" first-run page - a page inside MainWindow's
own shell QStackedWidget (see MainWindow.run_name_gate), shown before
the real app content page ever becomes visible. Only shown once:
MainWindow only calls run_name_gate() when
ai.main.is_assistant_name_configured() is still False.

This is never fatal to startup, and it is the only page between
launching and using the app. Naming the assistant is a preference:
leaving the field blank and clicking Continue just accepts
ai.main.DEFAULT_ASSISTANT_NAME ("Kestrel") and moves on - either way,
ai.main.set_assistant_name() gets called once, which is what makes
is_assistant_name_configured() True and this page never show again.

The name chosen here is the single source of truth used throughout the
app afterward - the wake word, the system prompt identity, and the
various chat/dialog strings that refer to the assistant by name (see
ai.main.get_assistant_name()/set_assistant_name() for the full list).
It's also editable later from Settings > Assistant name, so nothing
here is a one-time-only decision from the user's perspective, even
though the page itself only appears the one time.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, QSize, pyqtSignal

import app.theme as theme
import app.icons as icons
import ai.main as ai_main
from ai.assistant_names import suggest_name
from app.i18n import tr

# What used to be pre-filled in the name field the first time this
# page shows was a single hardcoded name ("Aria"). Every install saw
# the exact same suggestion - harmless for one assistant, but once more
# than one AI profile can exist (planned), every new profile would keep
# suggesting that same taken name. Now pulled randomly from
# ai.assistant_names.NAME_POOL instead (still a human first name rather
# than ai_main.DEFAULT_ASSISTANT_NAME/"Kestrel" itself - "Kestrel" reads like
# a brand/product label, not something you'd naturally say out loud to
# an assistant or use as a wake word). Typing over it (or clearing it
# and clicking Continue) still falls back to DEFAULT_ASSISTANT_NAME
# exactly as before.


def _build_style(c: dict) -> str:
    return f"""
        QWidget#gatePage {{ background-color: {c['bg_base']}; }}
        QWidget#gateCard {{ background-color: {c['bg_elevated']}; border-radius: 14px; }}
        QLabel#gateTitle {{ color: {c['text_primary']}; font-size: 20px; font-weight: 600; background: transparent; }}
        QLabel#gateSubtitle {{ color: {c['text_muted']}; font-size: 13px; background: transparent; }}
        QLineEdit#gateNameInput {{
            background-color: {c['bg_panel']}; color: {c['text_primary']};
            border: 1px solid {c['border_strong']}; border-radius: 8px;
            padding: 10px 12px; font-size: 14px;
        }}
        QLineEdit#gateNameInput:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton#gateShuffleBtn {{
            background: transparent; color: {c['text_muted']}; border: none;
            font-size: 12px; text-align: left; padding: 0px;
        }}
        QPushButton#gateShuffleBtn:hover {{ color: {c['accent']}; }}
        QPushButton#gateContinueBtn {{
            background-color: {c['accent']}; color: white; border: none;
            border-radius: 8px; padding: 10px 18px; font-size: 14px; font-weight: 600;
        }}
        QPushButton#gateContinueBtn:hover {{ background-color: {c['accent_hover']}; }}
    """


class NameGateWidget(QWidget):
    gate_finished = pyqtSignal()  # always "continue" - see this module's own docstring

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("gatePage")

        c = theme.colors(ai_main.get_theme())
        self.setStyleSheet(_build_style(c))

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)

        card = QWidget()
        card.setObjectName("gateCard")
        card.setFixedWidth(380)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(10)

        title = QLabel(tr("What should I call myself?"))
        title.setObjectName("gateTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            tr("This is how the assistant refers to itself in chat, and the "
            "wake word it'll listen for. You can change it any time later "
            "in Settings.")
        )
        subtitle.setObjectName("gateSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addStretch(1)

        # Names already shown this session, so clicking "suggest another"
        # repeatedly cycles through fresh options instead of occasionally
        # handing back the one just rejected. Also the seam future
        # multi-profile support hooks into: pass the OTHER profiles'
        # already-chosen names into suggest_name(taken=...) here too once
        # more than one profile can exist, so a new profile never gets
        # suggested a name that's already someone else's wake word.
        self._suggested_name = suggest_name()
        self._shown_names = {self._suggested_name}

        self._name_input = QLineEdit()
        self._name_input.setObjectName("gateNameInput")
        self._name_input.setPlaceholderText(self._suggested_name)
        self._name_input.setText(self._suggested_name)
        self._name_input.selectAll()
        self._name_input.returnPressed.connect(self._on_continue)
        layout.addWidget(self._name_input)

        shuffle_row = QHBoxLayout()
        shuffle_row.setContentsMargins(0, 0, 0, 0)
        btn_shuffle = QPushButton(tr(" Suggest another name"))
        btn_shuffle.setObjectName("gateShuffleBtn")
        btn_shuffle.setIcon(icons.icon("dice", c, "text_muted"))
        btn_shuffle.setIconSize(QSize(11, 11))
        btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_shuffle.clicked.connect(self._on_shuffle_name)
        shuffle_row.addWidget(btn_shuffle)
        shuffle_row.addStretch(1)
        layout.addLayout(shuffle_row)

        btn_continue = QPushButton(tr("Continue"))
        btn_continue.setObjectName("gateContinueBtn")
        btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_continue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_continue.clicked.connect(self._on_continue)
        layout.addWidget(btn_continue)

    def reset_for_display(self) -> None:
        """Called every time MainWindow switches TO this page."""
        self._name_input.setFocus()
        self._name_input.selectAll()

    def _on_shuffle_name(self) -> None:
        """
        Swaps in a fresh random suggestion - skips every name already
        shown this session (self._shown_names) so repeated clicks don't
        loop back to one just rejected. Only replaces the field if the
        user hasn't already typed something of their own over the
        current suggestion (checked against self._suggested_name, the
        last name THIS method or __init__ put there) - otherwise
        clicking it would blow away a name someone was already typing.
        """
        if self._name_input.text().strip() != self._suggested_name:
            return
        self._suggested_name = suggest_name(taken=self._shown_names)
        self._shown_names.add(self._suggested_name)
        self._name_input.setText(self._suggested_name)
        self._name_input.selectAll()

    def _on_continue(self) -> None:
        chosen = self._name_input.text().strip() or ai_main.DEFAULT_ASSISTANT_NAME
        ai_main.set_assistant_name(chosen)

        # Bug fix: MainWindow builds ChatWidget (and its topbar profile
        # picker) as part of its own single-window shell BEFORE this
        # gate ever gets a chance to run (see main.py's startup order
        # `window = MainWindow()` happens first, run_name_gate() after).
        # ChatWidget's profile picker reads ai.profiles.list_profiles(),
        # and ai.profiles auto-migrates whatever ai_main.get_assistant_name()
        # returns into profiles.json's very first profile the moment that
        # file doesn't exist yet - which at that point is still
        # DEFAULT_ASSISTANT_NAME ("Kestrel"), since this gate hasn't run.
        # That one-time migration never runs again, so without this, the
        # name typed here only ever updated ai_main's own global value
        # every OTHER place that shows "the AI profile" by name (Settings'
        # profile list, the chat topbar's own profile picker) stayed
        # stuck showing "Kestrel" forever, no matter what was typed here.
        # Renaming the active profile to match - a no-op if it already
        # does, e.g. if profiles.json didn't exist until this very call
        # keeps the two in sync for what is, on a fresh install, always
        # that same single auto-migrated profile.
        try:
            import ai.profiles as profiles
            active = profiles.get_active_profile()
            if active is not None and active.name != chosen:
                profiles.rename_profile(active.id, chosen)
        except Exception as e:
            print(f"[name_gate] Couldn't sync the active profile's name (continuing anyway): {e}")

        self.gate_finished.emit()
