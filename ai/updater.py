# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/updater.py
=============
Update *checking*, deliberately not update *installing*. Kestrel isn't
code-signed yet (see docs/BUILD_SECURE.md) - a self-installing update
mechanism for an unsigned Windows/macOS binary is a bigger liability
than the problem it solves (it's exactly the kind of thing SmartScreen/
Gatekeeper are designed to distrust, and it'd not go through the
signature-verification a real auto-updater relies on to prove an update
actually came from Kestrel and not from someone in the middle). Until a
code-signing certificate is in place, this only checks and tells the
user where to get the update themselves.

Compares paths.APP_VERSION against the latest RELEASE tag in a GitHub
repo. The repo defaults to branding.DEFAULT_UPDATE_REPO and can be
overridden with APP_UPDATE_REPO in .env for a fork. No auth is needed:
the releases endpoint is public for a public repo.

Two things have to be true for this to ever report an update, and
neither is about the code here:

  1. The repo has to have at least one published GitHub *Release*.
     Tags and commits are not releases. /releases/latest returns 404
     for a repo that has only tags, and a 404 is indistinguishable here
     from "you are up to date", so the check goes quiet rather than
     wrong.
  2. That release's tag has to parse to a higher version tuple than
     paths.APP_VERSION. "v0.2.2" beats "0.2.1"; "v0.2.1-hotfix" does
     not, because _parse_version drops the suffix and compares (0,2,1)
     against (0,2,1).

This defaulted to nothing at all for a long time, which meant _repo()
returned None and the function returned before making any request. The
feature was present, reachable and incapable of finding anything.
"""

import os

import requests

import branding
from ai.log import log
from paths import APP_VERSION


def _repo() -> "str | None":
    """The repo to ask about: .env first for a fork, then the project's
    own. Empty either way disables the check rather than guessing."""
    return (os.getenv("APP_UPDATE_REPO") or branding.DEFAULT_UPDATE_REPO or "").strip() or None


def _parse_version(v: str) -> tuple:
    """'0.1.0-dev' / 'v0.2.1' -> (0, 1, 0) - a plain numeric tuple so two
    versions can be compared with normal tuple ordering. Non-numeric
    trailing parts (the -dev suffix) are dropped rather than guessed
    at, since ordering pre-release/dev strings correctly against a
    numeric release isn't something worth getting subtly wrong here."""
    core = v.strip().lstrip("vV").split("-")[0]
    parts = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def check_for_update(timeout: float = 8.0) -> "dict | None":
    """
    Returns {"version": "1.2.0", "url": "https://github.com/.../releases/tag/v1.2.0"}
    if a newer release exists, else None (including on any network/
    config failure - this must never be allowed to block or crash
    startup over something as unimportant as an update check).
    """
    repo = _repo()
    if not repo:
        return None

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            log(f"[updater] {repo} has no published releases yet, "
                "so there is nothing to compare against.")
            return None
        if resp.status_code != 200:
            log(f"[updater] Update check got HTTP {resp.status_code} (non-fatal).",
                level="warn")
            return None
        data = resp.json()
        latest_tag = data.get("tag_name", "")
        if not latest_tag:
            return None
        if _parse_version(latest_tag) > _parse_version(APP_VERSION):
            return {"version": latest_tag, "url": data.get("html_url", f"https://github.com/{repo}/releases")}
    except requests.RequestException as e:
        log(f"[updater] Update check failed (non-fatal): {e}", level="warn")
    except (ValueError, KeyError) as e:
        log(f"[updater] Unexpected response from update check (non-fatal): {e}", level="warn")
    return None
