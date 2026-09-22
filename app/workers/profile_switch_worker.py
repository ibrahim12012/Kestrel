# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/profile_switch_worker.py
=======================================
Runs ai.profiles.activate_profile() off the UI thread. Same
QObject+signals-moved-to-QThread pattern as every other worker in this
package.

Why a worker at all, for what looks like a settings flip: switching
profile calls ai.main.set_active_provider_and_model(), which rebuilds
the live chat session from scratch - two .env writes, an instructions
file read, a fresh provider client (which for OpenAI/Anthropic means
constructing an SDK client),
and reseeding the conversation's history into the new session. On the
UI thread that's a visible freeze of the whole window every time
somebody picks a different profile from the chat's own dropdown.

The UI doesn't wait for any of it: app/chat_widget.py's
_on_profile_picker_changed updates the picker, the assistant-name copy
and the model picker IMMEDIATELY (the switch is presented as already
done, because from the user's point of view it is), and only rolls
that back if `failed` fires - which in practice means the profile's
provider has no API key configured yet.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import ai.profiles as profiles


class ProfileSwitchWorker(QObject):
    # (profile_id, profile_name) - the switch is fully live by the time
    # this fires; the UI has already shown it as done.
    succeeded = pyqtSignal(str, str)
    # (profile_id, user-facing error) - the UI reverts to whichever
    # profile is still actually active.
    failed = pyqtSignal(str, str)
    start = pyqtSignal()

    def __init__(self, profile_id: str):
        super().__init__()
        self._profile_id = profile_id
        self.start.connect(self.run)

    @pyqtSlot()
    def run(self):
        try:
            profile = profiles.activate_profile(self._profile_id)
            self.succeeded.emit(profile.id, profile.name)
        except Exception as e:  # noqa: BLE001 - surfaced to the user as-is
            # Most often ai.providers.ProviderClientError ("no API key
            # configured for this provider"), which reads fine on its
            # own; anything else still gets shown rather than swallowed.
            self.failed.emit(self._profile_id, str(e))
