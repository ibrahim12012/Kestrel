# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
branding.py
=============
The app's name and the assistant's, in one place.

Imported by paths.py, so this module must import nothing from the
project: keep it to the standard library.

Changing APP_NAME and APP_SLUG here renames the app everywhere, with one
caveat. APP_SLUG is the name of the folder holding a packaged install's
macros, chat history and settings, and the service name its API keys are
stored under in the OS credential store. Changing it moves both, so
LEGACY_APP_SLUGS lists every name they may already be under; paths.py
migrates the folder on first launch and ai/main.py falls back through
the list when reading a key.

The window chrome deliberately shows none of this. The title bar is
blank and applicationDisplayName is cleared in main.py, because a title
bar reading the product's own name back at you is the least useful line
on screen.
"""

# The product.
APP_NAME = "Kestrel"

# Lowercase, identifier safe: the credential-store service name and the
# macro export extension. The data folder uses APP_NAME instead, because
# %LOCALAPPDATA% holds capitalised app names by convention.
APP_SLUG = "kestrel"

# Names this app has shipped under before, newest first, used to move an
# existing data folder across on first launch. Empty: nothing was ever
# released under an earlier name, so there is no install out there to
# migrate. Add an entry here if the app is ever renamed again.
LEGACY_APP_SLUGS = ()

# What the assistant is called until someone renames it on the first-run
# screen (app/name_gate_widget.py). Deliberately not APP_NAME: the app is
# a product and the assistant is who you talk to, and naming them the
# same thing is why the assistant read as not having a name at all.
# Picked to the same bar as ai/assistant_names.py's pool, since it is
# also the wake word.
ASSISTANT_DEFAULT_NAME = "Vera"

# Prefix for the app's own settings keys in .env. Renaming it would
# reset everyone's theme, shortcuts and model choice, so ai/main.py
# migrates the old keys across on first launch.
ENV_PREFIX = APP_SLUG.upper()
# Prefixes used by an earlier name, migrated across on first launch.
# Empty for the same reason as LEGACY_APP_SLUGS.
LEGACY_ENV_PREFIXES = ()

# Macro export files (ai/macro_store.py).
#
# Short on purpose. This was ".kestrelmacros", which is thirteen
# characters and gets truncated or wrapped in most file dialogs, in
# Explorer's Type column and in the "always open with" prompt. Three or
# four characters is what every other application uses, and what the
# shell is laid out for.
EXPORT_SUFFIX = ".ktl"

# The identifier written INSIDE the file, which is not the extension and
# does not have to be short. It stays long and explicit, because this is
# what tells a reader (or a future importer) what they are looking at.
EXPORT_FORMAT = f"{APP_SLUG}.macros"

# Suffixes and format ids written by earlier versions, still accepted on
# import so an export made before the extension was shortened keeps
# working. Both lists only ever describe files written by THIS app
# under a previous Kestrel build; a name the app never shipped under
# does not belong here.
LEGACY_EXPORT_SUFFIXES = (".kestrelmacros",)
LEGACY_EXPORT_FORMATS = ()

# What Windows shows in Explorer's Type column, and the ProgID the
# installer registers the extension under. A ProgID is conventionally
# <Vendor>.<Type>.<version>, and it must not collide with another
# application's.
FILE_TYPE_NAME = f"{APP_NAME} macro pack"
FILE_TYPE_PROGID = f"{APP_NAME}.MacroPack.1"

# The app icon, relative to BASE_PATH. The all-purple mark rather than
# the black-and-purple one, which disappears against a dark taskbar.
# Transparent because an icon is composited onto whatever colour the
# taskbar, alt-tab switcher or title bar happens to be.
ICON_PATH = "assets/icon/Purple-transparent-1000.png"
WINDOWS_ICO_PATH = "assets/icon/Kestrel.ico"

# The other half of the tray's "working" blink: the same all-purple mark
# at 40% opacity, so the icon pulses rather than switching to a different
# logo. It only has to be visibly different from ICON_PATH; both being the
# same file is a blink that does nothing. Every place the app shows its
# logo uses the all-purple mark (the .exe and installer icon, Kestrel.ico,
# is generated from ICON_PATH too).
ICON_ALT_PATH = "assets/icon/PurpleDim-transparent-1000.png"

# What the window reports as its title.
#
# This is not shown anywhere in the app: the window is frameless and
# draws its own title bar (app/window_chrome.py), which deliberately
# prints no name. It exists for the OS, which reads it for the taskbar
# tooltip, the alt-tab switcher and Task Manager's Apps group.
#
# It was a single space for a while, to keep the name off the native
# title bar back when there was one. The cost was that every one of
# those OS surfaces fell back to the process filename instead, so the
# window announced itself as "Kestrel.exe". Now that no title bar
# renders this, a real name is free.
#
# Note that EMPTY would not work even now: Qt substitutes
# applicationName for an empty windowTitle, so clearing it would put the
# name back by a less predictable route.
WINDOW_TITLE = APP_NAME

# Windows taskbar grouping id (see main.py).
WINDOWS_APP_ID = f"{APP_NAME}.DesktopApp"

# The project's own site and repository. Both are overridable from .env
# for a fork, via KESTREL_WEBSITE_URL and APP_UPDATE_REPO.
#
# The site is this repository's docs/ folder, served by GitHub Pages.
DEFAULT_WEBSITE_URL = "https://ibrahim12012.github.io/Kestrel/"

# "owner/repo", read by ai/updater.py. It was blank, which meant the
# update check returned None before it made a request: the feature
# existed but could not ever find anything. See ai/updater.py.
DEFAULT_UPDATE_REPO = "ibrahim12012/Kestrel"
REPO_URL = f"https://github.com/{DEFAULT_UPDATE_REPO}"

# ── Windows executable metadata ───────────────────────────────────
# Kestrel.spec compiles these into the .exe as a VERSIONINFO resource:
# the Publisher, Product name and Description that Windows shows in the
# file's Properties > Details tab, in the SmartScreen prompt and in Task
# Manager. An executable with no version resource at all is a signal
# antivirus heuristics weigh on its own, because almost nothing built by
# a real publisher ships without one. See docs/ANTIVIRUS.md.
#
# PUBLISHER must match the legal entity on the code-signing certificate
# once there is one. A mismatch between the signature's subject and this
# string is worse than leaving it generic, so change both together.
PUBLISHER = "Kestrel"
PRODUCT_DESCRIPTION = "AI desktop assistant and automation for Windows"
COPYRIGHT = "Copyright (C) 2026 the Kestrel authors. GPL v3 or commercial."
