# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/chat_export.py
====================
Getting your data out: one conversation to a file, or everything to a
backup archive.

Two different jobs, deliberately not the same file format.

An export is for reading and sharing, so Markdown is the default: it
opens anywhere, reads fine as plain text, and survives this app being
uninstalled. JSON is there for feeding a conversation to something else.

A backup is for restoring, so it is a zip of the actual files, byte for
byte, with nothing reformatted. The chat database, the profiles, the
settings and the saved macros. Anything that reformats on the way out is
something that can fail to come back.

What a backup does NOT contain is API keys. Those live in the OS
credential store and this deliberately leaves them there: a backup is a
file that gets copied to a USB stick and emailed to yourself, and a
plaintext key inside one is a key that leaks. The restore says so, and
re-entering a key is a thirty-second job.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import zipfile

import branding
from paths import USER_DATA_PATH
from ai.log import log as print

MARKDOWN_SUFFIX = ".md"
JSON_SUFFIX = ".json"
BACKUP_SUFFIX = ".zip"

# What a backup holds, as (path relative to USER_DATA_PATH, required).
# Missing optional entries are skipped rather than failing the backup:
# an install with no macros yet should still be backup-able.
BACKUP_CONTENTS = (
    ("chats.db", True),
    ("profiles.json", False),
    (".env", False),
    ("macros", False),
    ("ai/discovery/trees", False),
)


class ExportError(Exception):
    """Raised with a sentence meant to be shown to the user as-is."""


# ══════════════════════════════════════════════════════════════════
# One conversation
# ══════════════════════════════════════════════════════════════════

def chat_to_markdown(chat: dict, assistant_name: str = "Assistant") -> str:
    """A conversation as a document someone can read without this app."""
    lines = [
        f"# {chat.get('title') or 'Conversation'}",
        "",
        f"*{len(chat.get('messages') or [])} messages*",
    ]
    created = chat.get("created_at")
    if created:
        lines.append(f"*Started {_readable_date(created)}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    for message in chat.get("messages") or []:
        who = "You" if message.get("role") == "user" else assistant_name
        lines.append(f"### {who}")
        lines.append("")
        lines.append((message.get("content") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def chat_to_json(chat: dict) -> str:
    return json.dumps(chat, indent=2, ensure_ascii=False) + "\n"


def write_chat(chat: dict, path: str, assistant_name: str = "Assistant") -> str:
    """Write one conversation to `path`. The format follows the
    extension, so the file dialog's filter is what chooses it."""
    text = (chat_to_json(chat) if path.lower().endswith(JSON_SUFFIX)
            else chat_to_markdown(chat, assistant_name))
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        raise ExportError(f"Couldn't write {path}: {e}") from e
    return path


def suggested_filename(title: str, suffix: str = MARKDOWN_SUFFIX) -> str:
    """A filename from a chat's title that Windows will actually accept."""
    safe = "".join(c if (c.isalnum() or c in " -_") else " " for c in (title or ""))
    safe = " ".join(safe.split())[:60].strip() or "conversation"
    return safe + suffix


# ══════════════════════════════════════════════════════════════════
# Everything
# ══════════════════════════════════════════════════════════════════

def default_backup_name() -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    return f"{branding.APP_SLUG}-backup-{stamp}{BACKUP_SUFFIX}"


def create_backup(path: str, source_dir: str = None) -> "tuple[str, list]":
    """
    Zip up chats, profiles, settings and macros. Returns (path, what went
    in), so the UI can say what was saved rather than just that something
    was.

    Written to a temporary file next to the target and moved into place
    at the end, so an interrupted backup cannot leave a half-written zip
    sitting where a good one used to be.
    """
    source_dir = source_dir or USER_DATA_PATH
    included = []
    temporary = path + ".part"

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("backup.json", json.dumps({
                "app": branding.APP_NAME,
                "format": f"{branding.APP_SLUG}.backup",
                "version": 1,
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }, indent=2))

            for relative, required in BACKUP_CONTENTS:
                absolute = os.path.join(source_dir, relative)
                if os.path.isfile(absolute):
                    archive.write(absolute, relative)
                    included.append(relative)
                elif os.path.isdir(absolute):
                    count = 0
                    for base, _dirs, files in os.walk(absolute):
                        for name in files:
                            full = os.path.join(base, name)
                            archive.write(full, os.path.relpath(full, source_dir))
                            count += 1
                    if count:
                        included.append(f"{relative} ({count} file(s))")
                elif required:
                    raise ExportError(
                        f"{relative} is missing from {source_dir}, so there is "
                        "nothing to back up yet."
                    )
    except ExportError:
        _discard(temporary)
        raise
    except (OSError, zipfile.BadZipFile) as e:
        _discard(temporary)
        raise ExportError(f"Couldn't write the backup: {e}") from e

    try:
        os.replace(temporary, path)
    except OSError as e:
        _discard(temporary)
        raise ExportError(f"Couldn't save the backup to {path}: {e}") from e
    return path, included


def read_backup(path: str) -> dict:
    """What a backup holds, checked enough to fail with a sentence."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            try:
                meta = json.loads(archive.read("backup.json"))
            except KeyError:
                raise ExportError(
                    f"{os.path.basename(path)} is a zip, but not a "
                    f"{branding.APP_NAME} backup."
                ) from None
    except zipfile.BadZipFile as e:
        raise ExportError(f"{os.path.basename(path)} isn't a readable zip: {e}") from e
    except OSError as e:
        raise ExportError(f"Couldn't open {path}: {e}") from e

    meta["contents"] = [n for n in names if n != "backup.json"]
    return meta


def restore_backup(path: str, target_dir: str = None) -> list:
    """
    Unpack a backup over the live data folder. Returns what it wrote.

    The current files are moved aside first, into a timestamped folder
    rather than deleted, because restoring the wrong backup is a thing
    people do and "your old chats are in this folder" is a far better
    outcome than "your old chats are gone".

    The app has to restart afterwards: the database and the settings are
    already open and cached in this process.
    """
    target_dir = target_dir or USER_DATA_PATH
    meta = read_backup(path)
    if not meta.get("contents"):
        raise ExportError("That backup is empty.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = os.path.join(target_dir, f"before-restore-{stamp}")
    restored = []

    try:
        with zipfile.ZipFile(path) as archive:
            for name in meta["contents"]:
                # A zip can name a path that climbs out of the folder it
                # is being unpacked into. Refuse rather than write there.
                destination = os.path.normpath(os.path.join(target_dir, name))
                if not destination.startswith(os.path.normpath(target_dir) + os.sep):
                    raise ExportError(f"That backup contains an unsafe path: {name}")

                if os.path.exists(destination):
                    kept = os.path.join(aside, name)
                    os.makedirs(os.path.dirname(kept), exist_ok=True)
                    shutil.move(destination, kept)

                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with archive.open(name) as src, open(destination, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored.append(name)
    except ExportError:
        raise
    except (OSError, zipfile.BadZipFile) as e:
        raise ExportError(f"The restore failed partway through: {e}") from e

    if restored:
        print(f"[backup] Restored {len(restored)} file(s); the previous ones are in {aside}")
    return restored


def _discard(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        print(f"[backup] Couldn't remove the partial file {path}: {e}")


def _readable_date(iso: str) -> str:
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d %B %Y")
    except (ValueError, TypeError):
        return iso
