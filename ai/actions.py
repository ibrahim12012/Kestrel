# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/actions.py
=============
All tool_* functions available to the AI.
Any function starting with "tool_" is automatically detected
by get_all_tools() and passed to Gemini as a callable tool.

Also contains:
  - Macro recording / playback system
  - File utilities (load_file, save_file)
  - get_all_tools()
  - play_actions()
"""

import keyboard
import inspect
import time
# Imported on first use, not at startup: see ai/lazy_import.py. The
# fail-safe kill switch (pyautogui.FAILSAFE = True) that used to be set
# here is applied there, the moment the real module loads, so it is on
# before any pyautogui call can run. Slamming the mouse into ANY screen
# corner raises pyautogui.FailSafeException from inside whatever
# pyautogui call is running at that instant; run_autonomous_mode() in
# ai/main.py is what turns that into a full stop.
from ai.lazy_import import pyautogui
# NOTE: deliberately no `from typing import Literal` here any more - a
# Literal[...] annotation on a tool_* function breaks Gemini's automatic
# function calling at call time. See the comment under
# tool_generate_file for the full story.

# Emergency stop, set the moment a fail-safe triggers anywhere (mouse
# slammed into a screen corner mid-action). This has to be a flag, not
# just a re-raised exception: the google-genai SDK's automatic function
# calling wraps every tool call in its own broad except and turns
# whatever we raise into a normal-looking function response telling the
# model "that tool failed, try something else" - the exception never
# reaches our own code at all. A flag checked proactively at the START of
# every action tool is the only thing that actually stops the model from
# just trying again on the next call.
emergency_stop = False


def _check_emergency_stop() -> str | None:
    """
    Call at the very top of any tool that moves the mouse, clicks, or
    types. Returns a refusal string (the tool should do nothing and
    return this) if the emergency stop is active, or None to proceed
    normally.
    """
    if emergency_stop:
        return (
            "EMERGENCY STOP ACTIVE: the user triggered the fail-safe (moved "
            "the mouse to a screen corner). Do not attempt any further "
            "actions - tell the user you've stopped and are waiting for "
            "their next instruction."
        )
    return None


# ══════════════════════════════════════════════════════════════════
# Human handoff (tier 2 of the automation pipeline - see the
# human_handoff_* globals above for the full picture)
# ══════════════════════════════════════════════════════════════════

def tool_request_human_handoff(reason: str) -> str:
    """
    Pause autonomous mode and hand control to a human - call this instead
    of attempting to click/type through a CAPTCHA, an "I'm not a robot"
    checkbox, a 2FA/MFA code prompt, an authenticator-app approval, an
    SMS/email verification step, or any other challenge that exists
    specifically to tell a human apart from an automated script. Trying
    to solve these yourself will fail (that's their entire purpose) and
    can lock the account or burn through retry attempts.

    reason: one short sentence describing what's on screen and what the
    human needs to do (e.g. "Google is showing an 'I'm not a robot'
    checkbox - please complete it", "The site sent a 6-digit code to
    +1***-***-1234 - please enter it and submit").

    This BLOCKS - autonomous mode will not take another screenshot or
    call another tool until the human confirms they've handled it (there
    is no timeout; take as long as needed). Once confirmed, you'll be
    given a fresh screenshot on the next step and should continue the
    original goal from there - don't repeat whatever step led to the
    challenge, that part is already done.

    Call this ONLY for genuine identity-verification challenges - not as
    a way to ask a clarifying question or hand off a step that's merely
    difficult; use tool_run_command/other tools or just proceed for
    anything you can actually do yourself.
    """
    global human_handoff_requested, human_handoff_reason
    human_handoff_requested = True
    human_handoff_reason = reason
    human_handoff_event.clear()
    print(f"[human_handoff] Requested - waiting for a human: {reason}")
    return "Waiting for the human to resolve this. Do not call any other tool right now."


def resume_human_handoff() -> None:
    """
    Called from the UI (the chat's "I've handled it - Continue" card, see
    MainWindow) once the human has resolved whatever tool_request_human_handoff
    flagged. Unblocks run_autonomous_mode()'s wait so it continues on to
    the next step.
    """
    global human_handoff_requested, human_handoff_reason
    human_handoff_requested = False
    human_handoff_reason = None
    human_handoff_event.set()


def _handle_pyautogui_exception(exc: Exception) -> str | None:
    """
    Call inside any `except Exception as e:` that wraps pyautogui calls,
    in place of the old re-raise approach (see _check_emergency_stop's
    docstring for why re-raising alone doesn't work against AFC). Sets
    the emergency_stop flag and returns a refusal string if `exc` was the
    fail-safe; returns None otherwise so the caller falls through to its
    normal error handling.
    """
    global emergency_stop
    failsafe_type = getattr(pyautogui, "FailSafeException", None)
    if isinstance(failsafe_type, type) and isinstance(exc, failsafe_type):
        emergency_stop = True
        print("[EMERGENCY STOP] Fail-safe triggered - mouse moved to a screen corner. Halting further actions.")
        return (
            "EMERGENCY STOP: fail-safe triggered (mouse moved to a screen "
            "corner). No further actions will be taken until the user says "
            "to continue."
        )
    return None

import difflib
import subprocess
import shlex
import string
import threading
import pyperclip
import os
import re
import keyword
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from ai.lazy_import import hidden_console_windows
# Clicks, keys and typing without the user's mouse; see that module.
from ai import direct_input

# Kestrel.exe has no console, so every console program it starts (tasklist,
# taskkill, cmd.exe) gets a console window of its own and flashes it on
# screen unless it is told not to. 0 outside Windows, where it is ignored.
_NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

# ── AppOpener, loaded on first use ────────────────────────────────
# This was `from AppOpener import open as appopen`, which made AppOpener
# the most expensive import in the app. Importing it runs cmd.exe every
# time (AppOpener/features.py does `subprocess.call("", shell=True)` to
# switch on terminal colours), and the first import in an install also
# runs PowerShell's Get-StartApps to build its list of apps. All of that
# happened before Kestrel's window existed, and because Kestrel.exe has
# no console, each of those programs flashed a console window of its own.
#
# Now the import waits until an app is actually opened, or until the
# background warm-up main.py starts once the window is up, and both the
# import and every call run with console windows hidden.
_appopen_function = None
_appopen_lock = threading.Lock()


def _load_appopener():
    global _appopen_function
    with _appopen_lock:
        if _appopen_function is None:
            with hidden_console_windows():
                from AppOpener import open as appopener_open
            _appopen_function = appopener_open
    return _appopen_function


def appopen(*args, **kwargs):
    """AppOpener's open(), imported on first use. See the note above."""
    function = _load_appopener()
    with hidden_console_windows():
        return function(*args, **kwargs)


def prewarm_appopener() -> None:
    """
    Import AppOpener ahead of the first "open <app>" request. Meant for
    a background thread once the window is showing. Never raises: a
    failure here just means the first request pays for the import.
    """
    try:
        _load_appopener()
    except Exception as e:
        log_debug(f"[actions] AppOpener prewarm skipped: {e}")

import ai.safety as safety
from ai.safety import RiskLevel
import ai.risk_gate as risk_gate
import ai.file_generation as file_generation
import ai.file_merging as file_merging
import ai.file_context as file_context
import ai.web_search as web_search

# ── Optional: used by tool_open_application to reliably detect an
# already-running app and bring it to the foreground, instead of trusting
# AppOpener's own (undocumented) behavior for that case. Neither is a hard
# requirement - without them, tool_open_application just always calls
# AppOpener's open(), same as before.
try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32gui
    import win32process
    import win32con
    import win32api
except ImportError:
    win32gui = None
    win32process = None
    win32con = None
    win32api = None

# ── Path setup ────────────────────────────────────────────────────
from paths import BASE_PATH, USER_DATA_PATH
from ai.log import log as print
from ai.log import log_debug
rel_path = os.path.join(BASE_PATH, "ai")

# ── Global state ──────────────────────────────────────────────────
actions_list = []     # Buffer of recorded actions for macro creation
fcn_name     = False  # Set to True by tool_name_action when macro is saved
single_fcn   = False  # Set to True by single-step tools when done

# ── Human handoff (2-tier automation pipeline) ─────────────────────
# Tier 1 is the normal autonomous loop (screenshot → Gemini → tool call,
# visual targeting throughout). Tier 2 is this: when the model recognizes
# something it should not try to click/type through itself - a CAPTCHA,
# an "I'm not a robot" checkbox, a 2FA/MFA code or authenticator prompt,
# an SMS/email verification step - it calls tool_request_human_handoff()
# instead of guessing at it, run_autonomous_mode() pauses and surfaces a
# card in the chat, and a human resolves it by hand. Confirming the card
# sets human_handoff_event, the loop's wait unblocks, and it continues
# normally from there (see run_autonomous_mode / resume_human_handoff).
human_handoff_requested = False
human_handoff_reason = None
human_handoff_event = threading.Event()

# ── Keyword lists (used by main.py for routing) ───────────────────
command_open = [
    "open", "launch", "start", "run", "activate", "execute",
    "show"
]
command_close = [
    "close", "quit", "stop", "kill", "terminate", "exit",
    "shut down"
]


# ══════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════

def load_file(file_path: str) -> str:
    """
    Load a text file. If it doesn't exist, create it with default
    instructions (using the {{ASSISTANT_NAME}} placeholder - see
    ai.main._render_instructions/get_assistant_name) and return the
    raw, unrendered content.
    """
    if not os.path.exists(file_path):
        default_content = """### IDENTITY & ROLE
You are "{{ASSISTANT_NAME}}", a specialized System Automator integrated into the user's environment.
You act as the bridge between the user's intent and the physical computer interface via provided tools.

### OPERATIONAL CONSTRAINTS (STRICT)
1. TOOL USE: Use tools for ANY system action (click, write, open, close).
2. VISION: Analyse provided screenshots to locate elements.
3. BREVITY: Do not provide long explanations. Be concise and direct.
4. AMBIGUITY: If an application or target name is unclear, ask for clarification.
5. SELF-MAINTENANCE: You are responsible for maintaining this instruction file.

### SINGLE-CALL UPDATE PROTOCOL (MANDATORY)
Each response must end with:
[[UPDATE_START]]
[Only the ONE new preference or habit just learned, if any - the app
appends it to the list below automatically and keeps every earlier one]
[[UPDATE_END]]

### OUTPUT FORMAT
- Action: [Tool Name or "None"]
- Status: [Current state of execution]
- Message: [Direct response to user]

### USER PROFILE & PREFERENCES
[PREFERENCES START HERE]
- The user likes a polite, direct tone.
[END OF PREFERENCES]
"""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(default_content)
        print(f"[actions] Created default file: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def save_file(file_path: str, content: str) -> None:
    """Save content to a file, creating it if needed."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True) if os.path.dirname(file_path) else None
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[actions] Saved: {file_path}")


def _queue_file_card(path: str, action: str) -> None:
    """
    Tells the UI a file was just written so the chat can show a "Show in
    folder" card for it - see ai.main.queue_file_output. Never allowed to
    fail the tool itself: the file is already on disk by the time this
    runs, and a UI nicety erroring out shouldn't turn a successful save
    into a reported failure. Deferred import for the same
    circular-import reason tool_run_command's own queue call has one.
    """
    try:
        import ai.main as ai_main
        ai_main.queue_file_output(path, action)
    except Exception as e:
        print(f"[actions] Couldn't queue file card for {path}: {e}")


GENERATE_FILE_FORMATS = ("txt", "md", "csv", "docx", "pdf", "xlsx")


def tool_generate_file(
    filename: str,
    format: str,
    content: str,
    open_after: bool = True,
) -> str:
    """
    Creates a REAL file for the user - a document, spreadsheet, or plain
    text/Markdown/CSV file - and saves it to their Documents\\Kestrel Files
    folder (created the first time this runs). Returns the full path it
    was saved to, and by default opens it immediately in whatever app
    Windows has associated with that file type, so the user sees the
    finished result without having to go find it themselves.

    WHEN TO USE THIS:
    Whenever the user wants something they'd open, save, print, edit
    further, or share afterward - a report, a letter, meeting notes, a
    table of data, a summary of this conversation, an invoice, a list
    to print, etc. Phrases like "write me a...", "make a document/file/
    spreadsheet", "save this as a...", "export this", or "I need a PDF
    of..." all mean call this, not just paste the text into the chat
    reply. If it's a quick answer that's naturally read in the moment -
    "what's a good subject line for this email" - just answer in chat
    instead; don't reach for a file every time text comes out of you.
    Don't do both for the same request (pasting the full content in
    chat AND ALSO generating a file) unless the user actually asked to
    see it in both places - that's just noise the second time.

    format: which kind of file to make - - "txt"  plain text, written exactly as given.
      - "md"   Markdown, written exactly as given (most apps show the
               raw "# " / "**bold**" characters rather than a rendered
               preview - that's expected, not a bug).
      - "csv"  spreadsheet-shaped data as comma-separated text. `content`
               must already be real CSV (one row per line, fields
               separated by commas, quote a field yourself if it needs
               to contain a comma) - build that text yourself, don't
               hand this a prose description of a table.
      - "docx" an actual Word document (needs the optional python-docx
               package - if it's missing you'll get a clear error
               naming the pip install command, don't retry blindly).
      - "pdf"  an actual PDF (needs the optional reportlab package,
               same missing-dependency behavior as docx above).
      - "xlsx" an actual Excel spreadsheet - same CSV-shaped `content`
               convention as "csv" above, parsed into real rows/columns
               with a bolded header row (needs the optional openpyxl
               package, same missing-dependency behavior as above).

      For docx/pdf specifically, `content` uses one lightweight
      convention: a line starting with "# " becomes the document's
      title, a line starting with "## " starts a new heading/section,
      a blank line separates paragraphs, and everything else is normal
      body text. Write real structure this way rather than trying to
      fake bold/headings with asterisks or capital letters - those
      generators render it as an actual Word/PDF heading style, not
      styled text pretending to be one.

    filename: just a name, no path or folder - this always saves under
      Documents\\Kestrel Files regardless of what's passed. The right
      extension for `format` is added automatically if left off or
      wrong. If a file with that exact name already exists, this saves
      alongside it as "name (2)", "name (3)", etc. rather than
      overwriting something the user might still want - it never
      silently replaces an existing file.

    content: the COMPLETE text/data for the file, written out in full -
    there's no follow-up step to append or edit it later, so don't
      call this with a placeholder or an outline meant to be filled in
      afterward. Write the whole thing first, then call this once with
      the finished result.

    open_after: leave this True (the default) unless the user
      specifically doesn't want it opened right away (e.g. generating
      several files in a row and only wants to look at them at the
      end) - most of the time seeing the result immediately is exactly
      what "make me a file" implies.
    """
    if not filename or not filename.strip():
        return "Couldn't create the file: no filename given."
    if not content or not content.strip():
        return "Couldn't create the file: no content given - nothing to write."

    try:
        path = file_generation.generate_file(filename, format, content)
    except file_generation.GenerationError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't create the file: {e}"

    print(f"[tool_generate_file] Created: {path}")
    _queue_file_card(path, "created")

    if open_after:
        try:
            os.startfile(path)  # Windows-only, matches the rest of this codebase
        except Exception as e:
            return f"File saved to {path}, but couldn't open it automatically: {e}"

    return f"File saved to {path}" + (" and opened." if open_after else ".")


# NEVER put a typing.Literal[...] (or any other subscripted generic
# list[str], dict[str, int], Optional[X]...) in a tool_* signature.
#
# `format` used to be annotated `Literal["txt", "md", ...]`, which made
# this the ONE tool in this file that failed every single time it was
# called, for every format including a plain .txt, with:
#
#     TypeError: Subscripted generics cannot be used with class and
#                instance checks
#
# Gemini's automatic function calling (ai/providers/gemini_provider.py
# hands these raw callables straight to google-genai as `tools`, which
# is the whole point of that SDK's AFC) introspects each signature AND
# validates the model's returned arguments against those same
# annotations with isinstance() before invoking the function. Python
# raises that exact TypeError for `isinstance(value, Literal[...])`
# it isn't a python-docx/reportlab version mismatch, and it never
# reached this function's body at all, which is why swapping the
# format or simplifying the content never helped.
#
# The enum still reaches OpenAI/Anthropic (which need an explicit JSON
# Schema rather than SDK introspection) via __enum_params__ below
# ai/providers/schema.py reads it and emits the same "enum": [...] it
# used to infer from the Literal, so nothing is lost on that side.
tool_generate_file.__enum_params__ = {"format": list(GENERATE_FILE_FORMATS)}


def tool_read_file(path: str) -> str:
    """
    Opens and reads an EXISTING file already on the user's PC, and
    returns its text content - the on-demand counterpart to dragging a
    file into the chat input (same extraction, same supported types,
    see ai.file_context's own docstring for the exact list: plain text/
    config/code formats, .pdf, .docx, .xlsx). This is a READ ONLY tool -
    there is no matching "edit this file" tool yet, so don't imply one
    is coming or offer to modify the file itself; if the user wants
    changes made, read it with this, then either answer in chat with
    what changed or write a NEW file with tool_generate_file - never
    claim to have edited the original.

    WHEN TO USE THIS: the user references a specific existing file by
    name/path and wants you to look at, summarize, extract from, or
    answer questions about its content - "read my notes.txt", "what
    does this docx say", "pull the numbers out of this spreadsheet".
    If they haven't given a path, ask for one (or use tool_run_command's
    `dir`/`cd` to help them find it) rather than guessing a location.

    Images have no text to extract (there's no OCR here) - this will
    say so rather than pretend to have read one; for images, describe
    what a screenshot shows instead if that's the actual need.

    Sends the file's content to the AI provider to answer the request,
    same as attaching a file in chat - worth a heads-up to the user if
    what they're pointing this at is sensitive.

    path: the full path to the file to read.
    """
    if not path or not path.strip():
        return "Couldn't read the file: no path given."

    text, err = file_context.extract_text_from_file(path)
    if err:
        return f"Couldn't read {path}: {err}"

    return file_context.format_attachment_block(os.path.basename(path), text)


def tool_merge_files_to_pdf(paths: list[str], output_filename: str, open_after: bool = True) -> str:
    """
    Combines several EXISTING files already on the user's PC - any mix
    of images and documents, auto-detected by extension, no need to say
    what each one is - into ONE merged PDF, in the order given, and
    saves it to the same Documents\\Kestrel Files folder tool_generate_file
    uses. Returns the full path it was saved to (plus a note of
    anything that had to be skipped), and by default opens the result
    immediately, same as tool_generate_file.

    THIS is the tool for "merge these into a PDF", "combine these
    images and this Word doc into one file", "put these scans together"
    - tool_generate_file can't do this (it only ever writes brand-new
    content the AI itself authors, it has no existing files as input);
    this one reads files that already exist and stitches them together.
    Likewise tool_read_file only reads ONE file back as text for you to
    see - it doesn't produce an output file at all.

    paths: full paths to the files to merge, IN THE ORDER they should
      appear in the output. Ask the user for these (or use
      tool_run_command's `dir` to help find them) if they weren't
      given - don't guess a folder to search on your own.

      Auto-detected per file from its extension:
        - Images (.png, .jpg, .jpeg, .bmp, .gif, .tiff, .tif, .webp) -
        each becomes one full page.
        - .pdf - its pages are copied in as-is.
        - .docx and common plain-text formats (.txt, .md, .csv, .log,
          .json, and more - see ai.file_context.PLAIN_TEXT_EXTENSIONS)
          - text is extracted and rendered into new PDF page(s),
          titled with that file's own name.
        - Anything else, or a file that can't be found or read, is
          SKIPPED - not a hard failure. The merge still proceeds with
          everything that could be included; tell the user plainly
          which files didn't make it in and why, using what this
          returns, rather than staying silent about it.

    output_filename: just a name, no path or folder - same convention
      as tool_generate_file (".pdf" added automatically, never
      overwrites an existing file with that name).

    open_after: leave this True (the default) unless the user
      specifically doesn't want it opened right away.

    Sends the content of every merged file to the AI provider as part
    of producing this, same as tool_read_file - worth a heads-up if
    what's being merged is sensitive.
    """
    if not paths:
        return "Couldn't merge: no files given."
    if not output_filename or not output_filename.strip():
        return "Couldn't merge: no output filename given."

    try:
        path, skipped = file_merging.merge_to_pdf(paths, output_filename)
    except file_merging.MergeError as e:
        return f"Couldn't merge files: {e}"
    except Exception as e:
        return f"Couldn't merge files: {e}"

    print(f"[tool_merge_files_to_pdf] Created: {path}")
    _queue_file_card(path, "merged")

    if open_after:
        try:
            os.startfile(path)  # Windows-only, matches the rest of this codebase
        except Exception as e:
            result = f"Merged PDF saved to {path}, but couldn't open it automatically: {e}"
            if skipped:
                result += "\nSkipped: " + "; ".join(skipped)
            return result

    result = f"Merged PDF saved to {path}" + (" and opened." if open_after else ".")
    if skipped:
        result += "\nSkipped: " + "; ".join(skipped)
    return result


def tool_web_search(query: str, max_results: int = 5) -> str:
    """
    Searches the web and returns a short list of results - title,
    snippet, and URL - for `query`. No API key needed and no setup
    required; it's ready to use as soon as this tool exists.

    WHEN TO USE THIS:
    Anything current or specific that you can't already be confident
    about - news, prices, sports scores, who currently holds some role
    or position, a recent release, software version numbers, or
    anything that could plausibly have changed since your own training.
    Also reach for this whenever you're just not sure of something and
    getting it wrong would matter, rather than guessing or saying "I
    think" about a fact that's actually easy to check. Don't use it for
    stable, well-known things you're already confident about - a
    historical date, a basic definition, an established scientific
    fact - searching those wastes a turn on something you already know.

    Only returns the results LISTING (title/snippet/URL for each hit) -
    it does NOT fetch or read the full text of any of those pages. A
    snippet is often enough to answer directly; when it isn't, say
    what you found and that the full page would have more, rather than
    inventing detail the snippet didn't actually give you.

    query: keep it short and specific, the way you'd type it into a
      search box yourself - 2-6 words, not a full sentence. If the
      first search doesn't answer what you need, search AGAIN with
      different/more specific terms rather than giving up after one
      try or guessing at the rest.

    max_results: how many hits to return (default 5). Rarely worth
      changing - raise it only for a genuinely broad query where you
      expect to need to compare several sources.

    Returns numbered "N. Title - snippet (url)" lines, or a plain
    message if the search failed (no internet connection, DuckDuckGo
    unreachable) or genuinely found nothing for that query.
    """
    query = (query or "").strip()
    if not query:
        return "No search query given."

    try:
        results = web_search.search(query, max_results=max_results)
    except Exception as e:
        return f"Web search failed: {e}"

    if not results:
        return f"No results found for: {query}"

    _queue_sources([{"url": r.get("url"), "title": r.get("title")} for r in results], "found")
    return "\n".join(
        f"{i}. {r['title']} - {r['snippet']} ({r['url']})"
        for i, r in enumerate(results, start=1)
    )


def tool_read_url(url: str, include_images: bool = True) -> str:
    """
    Opens a web page and returns what is actually on it: the text, the
    headings, and a marker at each point an image appears.

    WHEN TO USE THIS:
    Any time a link is mentioned and what it says matters. A URL on its
    own tells you almost nothing, and guessing from the address is how
    you end up confidently describing a page you have never seen. Also
    use it after tool_web_search when a snippet is not enough to answer
    properly: search finds the page, this reads it.

    url: the full address, including https://.

    include_images: when True (the default), the page's images are
      downloaded, shrunk, and attached to the NEXT message in this
      conversation, so you can look at them from your next turn. The
      text you get back now already marks where each one sits, as
      "[image 3: ...]" lines, and carries its caption or alt text. Set
      this False when the page is plainly all prose and the pictures
      would only cost time.

    Returns the page title, its text with image markers in place, and a
    list of the images attached. A page that could not be read comes
    back as a sentence saying why, not an exception.

    A page that builds itself with JavaScript is read in an off-screen
    browser automatically; there is no need to open one on screen.

    A site that answers with a CAPTCHA or a bot check ("verify you are
    human") is not read, and you are told so. Don't try to get around
    it: no other tool, no retrying, no browser automation to solve it.
    Tell the user; they can open the page in their own browser.
    """
    from ai import web_reader

    url = (url or "").strip()
    if not url:
        return "No URL given."
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    page = web_reader.read_page(url)
    if page.error:
        return page.error
    if not page.text.strip():
        return f"{url} loaded, but there was no readable text on it."
    _queue_sources([{"url": page.url, "title": page.title}], "read")

    sections = [f"# {page.title}" if page.title else "", f"Source: {page.url}", "", page.text]
    if include_images and page.images:
        sections.append(_attach_page_images(page, url))
    return "\n".join(part for part in sections if part).strip()


def _queue_sources(entries: list, kind: str) -> None:
    """For the chat's Sources card (ai.main.queue_sources). Imported late:
    ai.main imports this module."""
    try:
        import ai.main as ai_main
        ai_main.queue_sources(entries, kind)
    except Exception as e:
        log_debug(f"[actions] couldn't record sources: {e}")


def tool_crawl_site(url: str, max_pages: int = 10, include_images: bool = False) -> str:
    """
    Reads several pages of one site, so you can answer from the site as
    a whole rather than from whichever page happened to be linked.

    Finds the pages the way the site itself says to: robots.txt, then
    its sitemap, then the links on the starting page if it has neither.
    A site whose robots.txt asks not to be crawled is not crawled, and
    its Crawl-delay is respected. Pages that build themselves with
    JavaScript are read in an off-screen browser, as with tool_read_url.
    The crawl stops at the first CAPTCHA or bot check; as with
    tool_read_url, don't try to get around one, tell the user.

    This is how to read a site: never through a macro or autonomous
    mode, which drive the user's own browser on screen.

    WHEN TO USE THIS:
    A question about a whole site rather than one page. "What does this
    company charge", "does their documentation cover X", "summarise this
    blog". For a single link, use tool_read_url; this one makes many
    requests and takes noticeably longer.

    url: any address on the site. Its whole domain is what gets read.

    max_pages: how many pages to read, 1 to 50. The default of 10 is
      usually the right amount of context; raise it only when the answer
      really could be on any page of a large site.

    include_images: off by default here, unlike tool_read_url. Across
      ten pages the images add up fast, and a crawl is usually about
      what a site says rather than what it shows.

    Returns each page's title, URL and text, one after another, and says
    plainly which pages could not be read.
    """
    from ai import web_reader

    url = (url or "").strip()
    if not url:
        return "No URL given."
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    try:
        pages = web_reader.crawl_site(url, max_pages=max_pages)
    except web_reader.WebReadError as e:
        return str(e)
    except Exception as e:
        return f"Crawling {url} failed: {e}"

    readable = [p for p in pages if not p.error and p.text.strip()]
    stopped_at = next((p for p in pages if p.blocked), None)
    failed = [p for p in pages if p.error and not p.blocked]
    _queue_sources([{"url": p.url, "title": p.title} for p in readable], "read")

    if not readable:
        if stopped_at is not None:
            return stopped_at.error
        detail = "; ".join(f"{p.url}: {p.error}" for p in failed[:3])
        return f"Nothing readable was found at {url}. {detail}".strip()

    chunks = [f"Read {len(readable)} page(s) of {url}.", ""]
    for page in readable:
        chunks.append(f"## {page.title or page.url}")
        chunks.append(f"Source: {page.url}")
        chunks.append(page.text)
        chunks.append("")

    if include_images:
        every_image = [image for page in readable for image in page.images]
        if every_image:
            combined = web_reader.Page(url=url, images=every_image)
            chunks.append(_attach_page_images(combined, url))

    if failed:
        chunks.append(f"{len(failed)} page(s) couldn't be read:")
        chunks.extend(f"  - {p.url}: {p.error}" for p in failed[:10])
    if stopped_at is not None:
        chunks.append(f"The crawl stopped there. {stopped_at.error}")

    return "\n".join(chunks).strip()


def _attach_page_images(page, source_url: str) -> str:
    """
    Download, shrink and queue a page's images, and describe what was
    queued.

    Queued rather than returned: a tool hands the model a string, and
    there is no way to put a picture inside one. ai.main.queue_chat_images
    attaches them to the next message instead, which is one turn later
    than ideal but is the whole difference between the model being able
    to look at a diagram and not. The wording below says so, so the model
    does not claim to have seen something it has not yet.
    """
    from ai import web_reader

    try:
        from PIL import Image
    except ImportError:
        return "(Pillow isn't installed, so the page's images were skipped.)"

    out_dir = os.path.join(USER_DATA_PATH, "web_images", _sanitize_macro_name(
        urlparse(source_url).netloc or "page"))
    try:
        prepared = web_reader.prepare_images(page.images, out_dir)
    except Exception as e:
        print(f"[web] Preparing images for {source_url} failed: {e}")
        return "(The page's images couldn't be downloaded.)"

    if not prepared:
        return ""

    images = []
    for ref in prepared:
        try:
            images.append(Image.open(ref.local_path))
        except Exception as e:
            print(f"[web] Couldn't open {ref.local_path}: {e}")

    if not images:
        return ""

    import ai.main as ai_main
    ai_main.queue_chat_images(images)

    lines = [
        "",
        f"{len(images)} image(s) from this page are attached to the NEXT message, "
        "in this order. You can look at them from your next turn, not this one. "
        "Each matches the [image N: ...] marker of the same number above:",
    ]
    lines.extend(
        f"  image {ref.number} ({ref.width}x{ref.height}): {ref.label()}"
        for ref in prepared
    )

    # A marker with no image behind it would otherwise read as an
    # attachment that failed to arrive. Most of these are icons and
    # spacers that only turned out to be too small once downloaded.
    attached = {ref.number for ref in prepared}
    dropped = [ref.number for ref in page.images if ref.number not in attached]
    if dropped:
        listed = ", ".join(str(n) for n in dropped[:12])
        lines.append(
            f"Markers {listed} have no image attached: too small to be worth "
            "looking at, or they couldn't be downloaded."
        )
    return "\n".join(lines)


def get_all_tools(module) -> list:
    """
    Scan a module and return all functions whose name starts with 'tool_'.
    These are passed directly to Gemini as callable tools.
    """
    tools = []
    print("Getting Tools...")
    for name, obj in inspect.getmembers(module):
        if (inspect.isfunction(obj)
                and obj.__module__ == module.__name__
                and name.startswith("tool")):
            tools.append(obj)
    print(f"[actions] {len(tools)} tools loaded")
    return tools


# ══════════════════════════════════════════════════════════════════
# MACRO PLAYBACK
# ══════════════════════════════════════════════════════════════════

def screen_metrics() -> dict:
    """
    The screen geometry a macro is being recorded or replayed on.

    Recorded clicks are absolute pixel coordinates, so they only mean
    anything on a screen the same size as the one they were captured
    on. Storing this with the macro is what lets a replay say "this was
    recorded at 1920x1080, you are at 3840x2160" instead of silently
    clicking the wrong things, which is how a docked laptop turns a
    working macro into a mystery.
    """
    try:
        width, height = pyautogui.size()
    except Exception as e:
        print(f"[actions] Couldn't read the screen size: {e}")
        return {}
    metrics = {"width": int(width), "height": int(height)}
    try:
        import ctypes
        # The scaling factor Windows is applying (125%, 150%...). Two
        # machines at the same resolution but different scaling put the
        # same UI element in different places.
        metrics["scale"] = round(ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100, 2)
    except Exception as e:
        log_debug(f"[actions] screen_metrics ignored: {e}")
    return metrics


def screen_mismatch(recorded: dict) -> str:
    """
    "" when the current screen matches what a macro was recorded on,
    otherwise a sentence describing the difference, for the UI to show
    before anything gets clicked.
    """
    if not recorded:
        return ""
    current = screen_metrics()
    if not current:
        return ""
    if (recorded.get("width"), recorded.get("height")) != (current.get("width"), current.get("height")):
        return (
            f"This macro was recorded on a {recorded.get('width')}x{recorded.get('height')} "
            f"screen; yours is {current.get('width')}x{current.get('height')}. "
            "Its clicks are fixed pixel positions, so they will land in the wrong place."
        )
    if recorded.get("scale") and current.get("scale") and recorded["scale"] != current["scale"]:
        return (
            f"This macro was recorded at {int(recorded['scale'] * 100)}% display scaling; "
            f"yours is {int(current['scale'] * 100)}%. Its clicks may land in the wrong place."
        )
    return ""


class MacroStopped(Exception):
    """Raised to unwind out of however many nested repeat/forever blocks
    a macro is currently inside - by a "stop this macro" block, or by a
    colour-watch block landing on the colour it was told to treat as a
    failure (see _wait_for_color_change). Caught by play_actions and by
    a compiled generalized macro's own wrapper, both of which turn it
    into a normal "here's why it stopped" result rather than a
    traceback. A plain `return` can't do this job: these blocks can sit
    arbitrarily deep inside loops."""


def sample_pixel(x: int, y: int) -> "tuple[int, int, int]":
    """
    The RGB of one screen pixel - the primitive behind every colour
    block (see ai/macro_blocks.py's "sensing" category).

    pyautogui.pixel() rather than grabbing a full screenshot: this gets
    called in a polling loop several times a second by
    _wait_for_color_change below, and capturing an entire multi-monitor
    desktop to read three bytes off it would make the poll interval
    meaningless.
    """
    r, g, b = pyautogui.pixel(int(x), int(y))[:3]
    return int(r), int(g), int(b)


def _wait_for_color_change(
    x: int,
    y: int,
    forbidden: str = "",
    tolerance: int = 12,
    timeout: float = 30.0,
    poll_interval: float = 0.15,
) -> str:
    """
    Blocks until the pixel at (x, y) is no longer the colour it was
    when this started, and reports what it became.

    `forbidden` is the colour it must NOT become - the whole point of
    the block. A screen going to an error state (a field turning red, a
    status light going grey) is exactly the case where a macro should
    stop rather than carry on clicking through whatever comes next, so
    landing on it raises MacroStopped instead of returning normally.
    Leave it empty to just wait for any change at all.

    Returns a short description of what happened (the new colour, or
    that it timed out) - macro steps in this codebase all return
    something sayable, since the AI may be running this and will need
    to report back on it.
    """
    import ai.macro_blocks as blocks

    start = sample_pixel(x, y)
    forbidden_rgb = blocks.hex_to_rgb(forbidden) if forbidden else None
    deadline = time.time() + max(0.1, float(timeout))
    print(f"[play] watching colour at ({x}, {y}) - currently {blocks.rgb_to_hex(start)}")

    while time.time() < deadline:
        if emergency_stop:
            raise MacroStopped("Emergency stop while waiting for a colour change.")
        current = sample_pixel(x, y)
        if not blocks.colors_match(current, start, tolerance):
            found = blocks.rgb_to_hex(current)
            if forbidden_rgb is not None and blocks.colors_match(current, forbidden_rgb, tolerance):
                raise MacroStopped(
                    f"Colour at ({x}, {y}) changed to {found}, which this macro was told "
                    f"to treat as a failure (excluded colour {forbidden}). Stopped."
                )
            print(f"[play] colour at ({x}, {y}) changed to {found}")
            return f"Colour at ({x}, {y}) changed to {found}."
        time.sleep(poll_interval)

    print(f"[play] colour at ({x}, {y}) never changed within {timeout}s")
    return f"Colour at ({x}, {y}) never changed within {timeout:g}s."


def color_matches_at(x: int, y: int, color: str, tolerance: int = 12) -> bool:
    """Whether the pixel at (x, y) currently matches `color` (a #rrggbb
    string) - backs the "if colour at … is …" block."""
    import ai.macro_blocks as blocks
    try:
        return blocks.colors_match(sample_pixel(x, y), blocks.hex_to_rgb(color), tolerance)
    except Exception as e:
        print(f"[play] Could not read the pixel at ({x}, {y}): {e}")
        return False


def _legacy_action_to_step(action) -> dict:
    """One legacy recorded tuple → the block dict shape the interpreter
    works in. Mirrors ai.macro_store.literal_action_to_editor_step (same
    irregular input shapes, same output), duplicated here only to keep
    the replay path free of a UI-layer import."""
    if len(action) == 3 and action[0] in ("click", "right_click"):
        return {"type": action[0], "x": action[1], "y": action[2]}
    if len(action) == 2 and action[1] == "write":
        return {"type": "write", "value": action[0]}
    if len(action) == 2 and action[0] == "enter":
        return {"type": "enter"}
    if len(action) == 2 and action[0] == "hotkey":
        return {"type": "hotkey", "keys": action[1]}
    if len(action) == 2 and action[0] == "new_page":
        return {"type": "new_tab"}
    if len(action) == 2 and action[1] == "open":
        return {"type": "open_app", "app": action[0]}
    return {"type": "unknown", "_raw": list(action)}


def _get_action_delay() -> float:
    """
    Seconds to pause between macro steps, from Settings.

    Read from the environment rather than through ai.main (which pulls
    in the whole Gemini client) just for one float - the .env value is
    already in the process environment by the time any macro can be
    played. ai.main.get_macro_action_delay() is the source of truth for
    the Settings UI; this mirrors its default, clamp and env key.
    """
    try:
        delay = float(os.getenv("KESTREL_MACRO_ACTION_DELAY", "1.0"))
    except ValueError:
        delay = 1.0
    return min(max(delay, 0.1), 10.0)


# Macros currently part-way through running, innermost last. A macro
# that calls itself - directly, or through a chain of others - would
# otherwise recurse until the interpreter's stack gave out, and the
# symptom would be the app disappearing mid-macro with no message.
_macro_call_stack: list = []

# How deep one macro may call another. Not a safety limit (the cycle
# check below is what prevents runaway recursion); a legibility one. A
# macro nested eight deep is a macro nobody can reason about, and the
# ceiling makes that a clear refusal rather than a confusing failure.
MAX_MACRO_CALL_DEPTH = 8


def _run_saved_macro(name: str) -> None:
    """
    Run another saved macro as one step of this one.

    The block this backs (`call_macro`) is what makes a macro library
    composable: a login sequence recorded once can be the first step of
    everything that needs a login, and fixing it fixes all of them.

    Resolves the name at RUN time rather than when the block was added,
    so renaming or re-recording the callee is picked up. A name that no
    longer resolves is reported and skipped, not raised - one missing
    step should not abandon the rest of a macro that was otherwise fine.
    """
    from ai import macro_store

    if not name:
        print("[play] run macro: no macro named, skipping")
        return

    resolved = macro_store.resolve_macro_name(name)
    if resolved is None:
        print(f"[play] run macro: no saved macro matching {name!r}, skipping")
        return

    if resolved in _macro_call_stack:
        chain = " -> ".join([*_macro_call_stack, resolved])
        print(f"[play] run macro: refusing to recurse ({chain})")
        return

    if len(_macro_call_stack) >= MAX_MACRO_CALL_DEPTH:
        print(
            f"[play] run macro: {MAX_MACRO_CALL_DEPTH} levels deep already, "
            f"refusing to call {resolved!r}"
        )
        return

    spec = macro_store.load_spec(resolved)
    if not spec:
        print(f"[play] run macro: {resolved!r} has no readable definition, skipping")
        return

    _macro_call_stack.append(resolved)
    try:
        print(f"[play] run macro: {resolved} ({len(_macro_call_stack)} deep)")
        steps = spec.get("steps") or spec.get("blocks")
        if steps:
            # A block macro: run its nodes through this same interpreter,
            # so a called macro gets the same stop checks, the same
            # action delay and the same error handling as the caller.
            _run_block_nodes(steps, _get_action_delay())
        else:
            play_actions(spec.get("recorded_actions") or [])
    finally:
        # Popped even when the macro raises MacroStopped, or a stop
        # inside a nested macro would leave the stack dirty and every
        # later call to that macro would look like recursion.
        _macro_call_stack.pop()


def _run_block_nodes(nodes: list, action_delay: float) -> None:
    """
    Executes a parsed block tree (ai.macro_blocks.parse_program) - the
    replay half of the macro editor's Scratch-style blocks.

    Every loop checks `emergency_stop` on each iteration as well as
    each step, so the corner fail-safe and the global stop shortcut
    both break out of a `forever` immediately rather than only at the
    end of a pass. `forever` additionally carries its own iteration cap
    from the block itself: an unattended macro clicking in a genuinely
    endless loop, with no way to reach the mouse corner, is exactly the
    thing this app should not make easy to build by accident.
    """
    import ai.macro_blocks as blocks

    for node in nodes:
        if emergency_stop:
            print("[play] EMERGENCY STOP ACTIVE - aborting macro replay.")
            return
        step = node.step
        step_type = node.type

        try:
            if step_type == "repeat":
                times = max(0, int(step.get("times", 1)))
                for i in range(times):
                    if emergency_stop:
                        return
                    print(f"[play] repeat {i + 1}/{times}")
                    _run_block_nodes(node.body, action_delay)
                continue

            if step_type == "forever":
                cap = max(1, int(step.get("max_loops", 100)))
                for i in range(cap):
                    if emergency_stop:
                        return
                    print(f"[play] forever (pass {i + 1}/{cap})")
                    _run_block_nodes(node.body, action_delay)
                else:
                    print(f"[play] forever hit its {cap}-iteration cap - stopping that loop.")
                continue

            if step_type == "repeat_until_color":
                cap = max(1, int(step.get("max_loops", 100)))
                x, y = int(step.get("x", 0)), int(step.get("y", 0))
                color = step.get("color", "#00ff00")
                tolerance = int(step.get("tolerance", 12))
                for i in range(cap):
                    if emergency_stop:
                        return
                    if color_matches_at(x, y, color, tolerance):
                        print(f"[play] colour at ({x}, {y}) reached {color} after {i} pass(es)")
                        break
                    _run_block_nodes(node.body, action_delay)
                else:
                    print(f"[play] gave up waiting for {color} at ({x}, {y}) after {cap} pass(es)")
                continue

            if step_type == "if_color":
                matched = color_matches_at(
                    int(step.get("x", 0)), int(step.get("y", 0)),
                    step.get("color", "#ffffff"), int(step.get("tolerance", 12)),
                )
                print(f"[play] if colour at ({step.get('x')}, {step.get('y')}) → {matched}")
                _run_block_nodes(node.body if matched else node.else_body, action_delay)
                continue

            if step_type == "wait":
                seconds = max(0.0, float(step.get("seconds", 1.0)))
                print(f"[play] wait {seconds:g}s")
                # Slept in slices so a stop request lands within a
                # fraction of a second instead of after a 10-minute
                # wait block has run its course.
                remaining = seconds
                while remaining > 0:
                    if emergency_stop:
                        return
                    time.sleep(min(0.2, remaining))
                    remaining -= 0.2
                continue

            if step_type == "wait_color_change":
                _wait_for_color_change(
                    int(step.get("x", 0)), int(step.get("y", 0)),
                    step.get("forbidden", ""), int(step.get("tolerance", 12)),
                    float(step.get("timeout", 30.0)),
                )
                continue

            if step_type == "stop":
                raise MacroStopped("Macro stopped by a “stop this macro” block.")

            # ── Plain actions ────────────────────────────────────────
            if step_type in ("click", "right_click"):
                x, y = int(step.get("x", 0)), int(step.get("y", 0))
                if not _play_click(x, y, "right" if step_type == "right_click" else "left"):
                    if emergency_stop:
                        return
                    raise MacroStopped(f"Couldn't click ({x}, {y}), so the steps after it were not played.")
            elif step_type == "write":
                value = step.get("value", "")
                typed = _type_text(value)
                if not typed:
                    if emergency_stop:
                        return
                    raise MacroStopped(f"Couldn't type {_preview(value)} ({typed.note}), "
                                       f"so the steps after it were not played.")
                print(f"[play] write: {value!r}")
            elif step_type == "enter":
                pressed = _press_key("enter")
                if not pressed:
                    if emergency_stop:
                        return
                    raise MacroStopped(f"Couldn't press Enter ({pressed.note}), "
                                       f"so the steps after it were not played.")
                print("[play] enter")
            elif step_type == "hotkey":
                combo = str(step.get("keys", "")).strip()
                if combo:
                    pressed = _press_hotkey(combo)
                    if not pressed:
                        if emergency_stop:
                            return
                        raise MacroStopped(f"Couldn't press {combo} ({pressed.note}), "
                                           f"so the steps after it were not played.")
                    print(f"[play] hotkey {combo}")
            elif step_type == "new_tab":
                pressed = _new_tab()
                if not pressed:
                    if emergency_stop:
                        return
                    raise MacroStopped(f"Couldn't open a new tab ({pressed.note}), "
                                       f"so the steps after it were not played.")
                print("[play] new tab")
            elif step_type == "open_app":
                tool_open_application(step.get("app", ""))
                print(f"[play] open app: {step.get('app', '')}")
            elif step_type == "call_macro":
                _run_saved_macro(str(step.get("macro", "")).strip())
            else:
                print(f"[play] skipping unrecognized step: {blocks.describe(step)}")

            time.sleep(action_delay)

        except MacroStopped:
            raise  # unwinds every enclosing loop - see MacroStopped
        except Exception as e:
            if _handle_pyautogui_exception(e):
                return
            print(f"[play] Error on step {blocks.describe(step)}: {e}")


def _type_char_fallback(ch: str) -> None:
    """
    Type one character without SendInput.

    The clipboard route is layout-independent and handles anything, but
    it overwrites whatever the user had copied and some fields refuse
    paste, so it is used only for the characters pyautogui actually
    cannot type: punctuation it mishandles on non-US layouts, and
    everything outside ASCII.
    """
    if ch in string.punctuation or not ch.isascii():
        pyperclip.copy(ch)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(ch)


class Typed:
    """
    What became of some typing or a key: true when it all went through.
    When not, `note` says why (automation stopped, the window it was
    going into left the front, it couldn't be brought forward...), and
    `started` whether some of it had already gone in before that.
    """

    def __init__(self, done: bool, note: str = "", started: bool = False):
        self.done, self.note, self.started = done, note, started

    def __bool__(self) -> bool:
        return self.done

    def __repr__(self) -> str:
        return f"Typed(done={self.done}, note={self.note!r}, started={self.started})"


def _type_text(text: str) -> Typed:
    """
    Type text into whatever has focus.

    Goes through Windows' SendInput with KEYEVENTF_UNICODE, which
    delivers a character by its code point rather than by a key on the
    current layout - see ai/unicode_input.py. That is what makes
    accented characters work at all.

    They did not before. pyautogui.write() looks each character up in a
    table built from ASCII plus named keys, and a character that is not
    in it types nothing at all, with no error: "café" came out "caf",
    "Crème brûlée" came out "Crme brle". The previous workaround pasted
    `string.punctuation` through the clipboard, which is 32 ASCII
    characters and contains no accent, no curly quote, no en dash, no
    currency symbol and nothing non-Latin.

    Falls back per character if SendInput is unavailable or refused, so
    a machine without ctypes still types what it can.

    Shared by play_actions() and macros generalized by
    ai/macro_generalizer.py.

    Finishes with a Delete keypress to clear any autocomplete suggestion
    the OS/browser attached after the last typed character. Address bars
    in particular select-and-append their top history match as you type
    (e.g. typing "youtube" can leave a full previous-search URL
    highlighted right after it) - if the next macro step presses Enter,
    THAT highlighted suggestion submits instead of the literal text just
    typed, silently sending the macro somewhere else entirely. Delete
    removes only a trailing selection like that; with nothing selected
    (cursor already at the end of the text, the normal case everywhere
    else) it's a no-op, so this is safe to always do, not just for URLs.

    Into the window the AI is working in, which needn't be in front: see
    ai/direct_input.py. The keyboard is used when that window is in front,
    or once it has been brought there; if it can't be, nothing is typed.
    Before every key, that window is checked to still be in front: when
    the user clicks into another one halfway through, typing stops
    there, so the rest doesn't land in their window.

    A line break or a tab in the text is pressed as the Enter or Tab key
    it stands for, the way pyautogui typed them, so a "\n" still submits
    a field and a "\t" still moves to the next one. Text that ends with
    one gets no Delete after it: that Enter may have closed a dialog, and
    the Delete would land on whatever is behind it.

    Returns a Typed, true when all of it went in. When it is false, the
    caller doesn't press Enter after it, and a macro stops.
    """
    from ai import unicode_input

    if not text:
        return Typed(True)
    outcome = direct_input.type_text(text)
    if not outcome.done and outcome.how != "keyboard":
        return _not_typed(outcome)
    if outcome.done:
        # It went in without the keyboard; so does the Delete, if it can.
        time.sleep(0.05)  # let an async autocomplete suggestion actually land first
        cleared = _press_key("delete", outcome.window)
        if not cleared and direct_input.is_stopped():
            return Typed(False, cleared.note, started=True)
        return Typed(True)
    # The windows there as typing starts: a window the keys may be handed
    # over to has to be a new one (see direct_input.handed_over).
    front, shown, last_key_at = outcome.front, direct_input.shown_windows(), None
    for kind, piece in _typing_pieces(text):
        for item in ((piece,) if kind == "key" else piece):
            going, front = _keys_reach(front, shown, last_key_at)
            if not going:
                return _typing_interrupted(last_key_at is not None)
            if kind == "key":
                keyboard.press_and_release(item)
            elif not unicode_input.send_char(item):
                _type_char_fallback(item)
            last_key_at = time.monotonic()
    if text[-1] in "\r\n\t":
        return Typed(True)
    time.sleep(0.05)  # let an async autocomplete suggestion actually land first
    # Only into a text field: anywhere else (a file list, a mail list) a
    # Delete acts on what is selected.
    if not direct_input.focus_takes_text():
        return Typed(True)
    going, front = _keys_reach(front, shown, last_key_at)
    if not going:
        return _typing_interrupted(True)
    keyboard.press_and_release("delete")
    return Typed(True)


def _keys_reach(front, shown, last_key_at):
    """Whether the next key may go, and the window it goes into: `front`
    while it is still the window in front, or the one it handed over to
    (the Start menu turning into Search: see direct_input.handed_over)."""
    if direct_input.keyboard_reaches(front):
        return True, front
    new = direct_input.handed_over(front, shown, last_key_at)
    return new is not None, new


def _not_typed(outcome) -> Typed:
    note = outcome.note or "automation is stopped"
    print(f"[type] Nothing typed: {note}.")
    return Typed(False, note)


def _typing_interrupted(started: bool) -> Typed:
    if direct_input.is_stopped():
        note = "automation was stopped"
    else:
        now = direct_input.describe_front()
        note = ("the window it was typing into is no longer the one in front (the user may have "
                "clicked into another window, or a dialog came up)"
                + (f"; in front now: {now}" if now else ""))
    print(f"[type] Stopped typing{' partway' if started else ''}: {note}.")
    return Typed(False, note, started)


def _typing_pieces(text: str):
    """`text` as runs of characters, and the keys its line breaks ("\r\n",
    "\n", "\r") and tabs stand for."""
    run = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\r\n\t":
            if run:
                yield "text", "".join(run)
                run = []
            if ch == "\r" and text[i + 1:i + 2] == "\n":
                i += 1
            yield "key", "tab" if ch == "\t" else "enter"
        else:
            run.append(ch)
        i += 1
    if run:
        yield "text", "".join(run)


def _press_key(key: str, window=None) -> Typed:
    """
    One key: Enter, Delete, Escape, Tab, an arrow. Posted straight to the
    window the AI is working in when that window is in the background and
    can take it that way (ai/direct_input.py); otherwise the keyboard,
    which then reaches that window. Not done when it wasn't pressed at
    all: automation stopped, or the window couldn't be brought forward.
    `window`: that window, when the caller already knows it.

    press_and_release, never press: tool_enter and tool_write_and_enter
    used keyboard.press("enter"), which holds the key down and never lets
    it go.
    """
    outcome = direct_input.press_key(key, window)
    if outcome.done:
        return Typed(True)
    if outcome.how != "keyboard":
        note = outcome.note or "automation is stopped"
        print(f"[keys] {key} not pressed: {note}.")
        return Typed(False, note)
    name = key.strip().lower()
    if name not in _KEYS_THAT_OPEN:
        keyboard.press_and_release(key)
        return Typed(True)
    # Enter in a text field (a search box, a document) rarely opens a
    # window; on a file or a button it may (see direct_input.follow_keys).
    into_text = name in ("enter", "return") and direct_input.focus_takes_text()
    before = direct_input.before_keys(outcome.front, into_text=into_text)
    keyboard.press_and_release(key)
    direct_input.follow_keys(before)
    return Typed(True)


# Single keys that may bring up a window: Enter opening a file or
# pressing a button, F1's help. The window they bring up is where the next
# keys go (direct_input.follow_keys).
_KEYS_THAT_OPEN = frozenset({"enter", "return", "f1"})


# Names the keyboard module gives the Windows key. A combination with it
# is for Windows itself (Win+R, Win+D), whichever window is in front.
_WINDOWS_KEY_NAMES = frozenset({"win", "windows", "left windows", "right windows", "super", "cmd", "command"})


def _press_hotkey(combo: str) -> Typed:
    """
    A key combination such as ctrl+t. Ctrl, Alt and Shift have to be
    really held down for an app to see them, so this always goes through
    the keyboard, with the AI's window brought to the front first when it
    isn't already there. Not pressed at all when automation is stopped or
    that window can't be brought forward: it would go to another window.
    A combination with the Windows key is for Windows itself, and only
    waits for the stop.
    """
    parts = {part.strip().lower() for part in combo.split("+")}
    # Alt+Tab and Alt+Esc switch to a window that was on screen, on purpose.
    switch = "alt" in parts and bool(parts & {"tab", "esc", "escape"})
    if parts & _WINDOWS_KEY_NAMES or switch:
        # For Windows itself, whichever window is in front.
        if direct_input.is_stopped():
            return Typed(False, "automation is stopped")
        before = direct_input.before_keys()
        keyboard.press_and_release(combo)
        direct_input.follow_keys(before, system=not switch, switch=switch)
        return Typed(True)
    outcome = direct_input.keyboard_window()
    if outcome.how != "keyboard":
        note = outcome.note or "automation is stopped"
        print(f"[keys] {combo} not pressed: {note}.")
        return Typed(False, note)
    if "+".join(sorted(parts)) in _STAYS_IN_WINDOW:
        keyboard.press_and_release(combo)
        return Typed(True)
    # A window it brings up (a new window from Ctrl+N, Properties from
    # Alt+Enter) is where the next keys go.
    before = direct_input.before_keys(outcome.front)
    keyboard.press_and_release(combo)
    direct_input.follow_keys(before)
    return Typed(True)


# Combinations that act inside the window they go to and bring up no new
# one (a dialog they open belongs to that window): nothing is waited for
# after them. Parts sorted, as _press_hotkey compares them.
_STAYS_IN_WINDOW = frozenset("+".join(sorted(combo.split("+"))) for combo in (
    "ctrl+c", "ctrl+x", "ctrl+v", "ctrl+a", "ctrl+z", "ctrl+y", "ctrl+shift+z",
    "ctrl+b", "ctrl+i", "ctrl+u", "ctrl+s", "ctrl+f", "ctrl+h", "ctrl+l", "ctrl+t", "ctrl+w",
    "ctrl+tab", "ctrl+shift+tab", "shift+tab", "ctrl+home", "ctrl+end",
    "ctrl+backspace", "ctrl+delete", "ctrl+left", "ctrl+right", "shift+left", "shift+right",
    "shift+up", "shift+down", "shift+home", "shift+end", "ctrl+shift+left", "ctrl+shift+right",
    "ctrl+plus", "ctrl+minus", "ctrl+0", "ctrl+r", "f5",
))


def _new_tab() -> Typed:
    """Ctrl+T for a new browser tab, into the window the AI is working in,
    the way _press_hotkey sends a combination."""
    outcome = direct_input.keyboard_window()
    if outcome.how != "keyboard":
        note = outcome.note or "automation is stopped"
        print(f"[keys] Ctrl+T not pressed: {note}.")
        return Typed(False, note)
    pyautogui.hotkey("ctrl", "t")
    return Typed(True)


def _macro_type(text: str) -> None:
    """A generalized macro's typing (ai/macro_generalizer.py): stops the
    macro when it couldn't all go in, since every step after it expects
    it there."""
    typed = _type_text(text)
    if not typed:
        raise MacroStopped(f"couldn't type {_preview(text)}: {typed.note}")


def _macro_key(key: str) -> None:
    pressed = _press_key(key)
    if not pressed:
        raise MacroStopped(f"couldn't press {key}: {pressed.note}")


def _macro_hotkey(combo: str) -> None:
    pressed = _press_hotkey(combo)
    if not pressed:
        raise MacroStopped(f"couldn't press {combo}: {pressed.note}")


def _preview(text: str) -> str:
    return repr(text if len(text) <= 60 else text[:57] + "...")


def _start_macro() -> None:
    """
    The first step of every macro run, generalized ones included: typing
    goes to the window the macro's own clicks pick, never to one an
    earlier run left behind (see ai/direct_input.py's target).
    """
    direct_input.forget_target()


def _play_click(x: int, y: int, button: str = "left") -> bool:
    """
    A macro's click, without the user's mouse when there is any other way
    (ai/direct_input.py). False when it couldn't be made at all, and the
    caller stops the macro there: every step after it was recorded for
    the screen that click led to.
    """
    outcome = direct_input.click(x, y, button=button)
    what = "right click" if button == "right" else "click"
    if outcome.done:
        print(f"[play] {what} ({x}, {y})")
        return True
    if not outcome.stopped:
        print(f"[play] Couldn't {what} ({x}, {y}): {outcome.note}.")
    return False


def click_at_target_or_position(target: str, x: int, y: int, button: str = "left") -> str:
    """
    Click a described UI element using vision (get_position_object_and_click)
    if possible, falling back to the fixed (x, y) coordinates recorded when
    the macro was made - in case the layout changed since then, or the
    vision call fails for any reason. button="right" opens a context menu.
    Used by macros generalized via ai/macro_generalizer.py.

    A click that can't be made at all raises MacroStopped, which the
    generated macro turns into its result: the steps after it were made
    for the screen that click leads to.
    """
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    try:
        import ai.main as ai_main  # local import: ai.main already imports this module
        result = ai_main.get_position_object_and_click(target, button=button)
        if isinstance(result, str) and result.lower().startswith("could not locate"):
            raise RuntimeError(result)
        if isinstance(result, str) and "kestrel's own window is there" in result.lower():
            # Kestrel's window covers the target, so the screenshot can't
            # show it: the recorded position is clicked, through Kestrel's
            # window, as a recorded macro's click is (ai/direct_input.py).
            raise RuntimeError(result)
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        if handled:
            return handled
        print(f"[macro] Vision click for {target!r} failed ({e}) - falling back to ({x}, {y})")
    else:
        if isinstance(result, str) and result.lower().startswith("could not click"):
            # Found, but it couldn't be clicked (see ai/direct_input.py).
            raise MacroStopped(result)
        return result
    outcome = direct_input.click(x, y, button=button)
    if outcome.stopped:
        return _check_emergency_stop() or "Stopped."
    if not outcome.done:
        raise MacroStopped(f"couldn't click {target or 'the recorded position'} at ({x}, {y}): {outcome.note}")
    return f"Clicked fallback position ({x}, {y})"


def play_actions(recorded_actions: list) -> None:
    """
    Replay a list of recorded actions.

    Two entry shapes live side by side in one list, because macros
    recorded before the block editor existed must keep replaying
    byte-for-byte:

      - A legacy tuple, exactly as the recorder has always appended it:
          ("click",  x, y)          → left click
          ("right_click", x, y)     → right click (opens context menu)
          ("write",  text)           → type text
          ("enter",  "enter")        → press Enter
          ("new_page", "ctrl+t")     → new browser tab
          (app_name, "open")         → open application
      - A block dict - {"type": "repeat", "times": 3}, {"type": "end"},
        {"type": "wait_color_change", ...} - as added in the macro
        editor. See ai/macro_blocks.py for the vocabulary; control
        blocks nest via flat `end`/`else` markers, which
        blocks.parse_program() turns into a tree before anything runs.

    Storing new blocks as dicts rather than inventing positional tuple
    encodings for them (["repeat", 3], ["if_color", x, y, hex, tol]...)
    is deliberate: those would be write-only, and JSON round-trips a
    dict just as happily.
    """
    import ai.macro_blocks as blocks

    # Read the configurable delay directly rather than importing ai.main
    # (which pulls in the whole Gemini client) just for one float - the
    # .env value is already loaded into the process environment by the
    # time any macro can actually be played (the app can't get this far
    # without ai.main having initialized first), so os.getenv alone is
    # enough; ai.main.get_macro_action_delay() is the source of truth for
    # the Settings UI, this just mirrors its default/clamp/env-key logic.
    action_delay = _get_action_delay()
    started = time.monotonic()
    _start_macro()

    print(f"Playing Actions... (delay between steps: {action_delay}s)")

    # Anything with control blocks in it goes through the interpreter;
    # a plain recording (the overwhelmingly common case, and every
    # macro saved before this existed) takes the original flat loop
    # below completely unchanged.
    if any(isinstance(a, dict) for a in recorded_actions):
        program = blocks.parse_program(
            [a if isinstance(a, dict) else _legacy_action_to_step(a) for a in recorded_actions]
        )
        stopped = False
        try:
            _run_block_nodes(program, action_delay)
        except MacroStopped as e:
            stopped = True
            print(f"[play] {e}")
        _announce_macro_end(stopped or emergency_stop, started)
        return

    for action in recorded_actions:
        if emergency_stop:
            print("[play] EMERGENCY STOP ACTIVE - aborting macro replay.")
            break
        try:
            if len(action) == 3 and action[0] in ("click", "right_click"):
                kind, x, y = action
                if not _play_click(x, y, "right" if kind == "right_click" else "left"):
                    if not emergency_stop:
                        print("[play] Stopping the macro here: the steps after that click were "
                              "recorded for the screen it leads to.")
                    break

            elif len(action) == 2 and action[1] == "write":
                text, _ = action
                typed = _type_text(text)
                if not typed:
                    if not emergency_stop:
                        print(f"[play] Stopping the macro here: couldn't type {_preview(text)} "
                              f"({typed.note}).")
                    break
                print(f"[play] write: {text!r}")

            elif len(action) == 2 and action[0] == "enter":
                pressed = _press_key("enter")
                if not pressed:
                    if not emergency_stop:
                        print(f"[play] Stopping the macro here: couldn't press Enter ({pressed.note}).")
                    break
                print("[play] enter")

            elif len(action) == 2 and action[0] == "new_page":
                pressed = _new_tab()
                if not pressed:
                    if not emergency_stop:
                        print(f"[play] Stopping the macro here: couldn't open a new tab ({pressed.note}).")
                    break
                print("[play] new tab")

            elif len(action) == 2 and action[1] == "open":
                app_name, _ = action
                tool_open_application(app_name)
                print(f"[play] open app: {app_name}")

            time.sleep(action_delay)

        except Exception as e:
            if _handle_pyautogui_exception(e):
                break
            print(f"[play] Error on action {action}: {e}")

    _announce_macro_end(emergency_stop, started)


def _announce_macro_end(stopped: bool, started: float) -> None:
    """
    One sound for how a macro run ended, or none.

    None inside an autonomous run: the run announces its own outcome
    once, at its end, and a macro it plays is one of its steps. None
    either when the stop shortcut ended it, because the shortcut already
    played the stop sound when it was pressed. It used to play "finished"
    even then, straight after the stop sound: two sounds, one of them
    wrong.
    """
    # Local import: ai.main imports this module at module load time, so
    # importing it back at module level here would be circular (same
    # reason click_at_target_or_position/tool_screenshot import it locally
    # instead of at the top of the file).
    import ai.main as ai_main
    if ai_main.autonomous_run_active():
        return
    if not stopped:
        ai_main.play_notification_sound("macro_finished")
    elif ai_main._stop_sound_played_at < started:
        # Stopped some other way (the mouse-corner fail-safe).
        ai_main.play_notification_sound("automation_stopped")


# ══════════════════════════════════════════════════════════════════
# SYSTEM TOOLS
# ══════════════════════════════════════════════════════════════════

def _get_running_processes() -> list:
    """Return list of currently running process names."""
    try:
        # Argument list, no shell - tasklist is a fixed, hardcoded call
        # (not built from any AI/user-provided string), but there's no
        # reason to route even a fixed call through a shell when the
        # argv form works exactly the same and carries no injection
        # surface at all.
        out = subprocess.check_output(
            ["tasklist", "/FI", "STATUS eq RUNNING", "/NH"],
            shell=False, text=True, creationflags=_NO_CONSOLE_WINDOW,
        )
        return list(set([
            line.split()[0]
            for line in out.splitlines()
            if line.strip()
        ]))
    except Exception as e:
        print(f"[actions] Could not get process list: {e}")
        return []


# ══════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS  (all auto-detected by get_all_tools)
# ══════════════════════════════════════════════════════════════════

def _find_running_app_windows(app_name: str) -> list:
    """
    Return the visible top-level window handles (hwnd) belonging to a
    process whose name matches app_name, or [] if psutil/pywin32 aren't
    available or nothing matches. Matching is loose (substring, either
    direction) since the app name the AI gives us rarely matches the
    exact process name (e.g. "chrome" vs "chrome.exe", "spotify" vs
    "Spotify.exe").
    """
    if psutil is None or win32gui is None:
        return []

    needle = app_name.strip().lower()
    if not needle:
        return []

    matching_pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        stem = name[:-4] if name.endswith(".exe") else name
        if needle in stem or stem in needle:
            matching_pids.add(proc.info["pid"])

    if not matching_pids:
        return []

    hwnds = []

    def _enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in matching_pids:
            hwnds.append(hwnd)

    win32gui.EnumWindows(_enum_handler, None)
    return hwnds


def _focus_window(hwnd) -> bool:
    """
    Bring a window to the foreground AND maximize it to fullscreen -
    whether it was just launched or was already running. Plain
    SetForegroundWindow() is unreliable on Windows - it silently no-ops
    unless the calling process already owns the foreground, a well-known
    OS restriction - so this temporarily attaches this thread's input to
    both the current foreground window's thread and the target window's
    thread first, which is the standard, reliable workaround.
    """
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd) if fg_hwnd else (0, 0)

        attached = []
        for thread_id in {fg_thread, target_thread}:
            if thread_id and thread_id != current_thread:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached.append(thread_id)
                except Exception as e:
                    log_debug(f"[actions] _focus_window ignored: {e}")
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            for thread_id in attached:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except Exception as e:
                    log_debug(f"[actions] _focus_window ignored: {e}")
        # What the AI types next goes here (ai/direct_input.py).
        direct_input.set_target(hwnd)
        return True
    except Exception as e:
        print(f"[tool_open_application] Could not focus existing window: {e}")
        return False


def _wait_for_window_and_maximize(app_name: str, timeout: float = 6.0, interval: float = 0.3) -> bool:
    """
    Poll for a just-launched app's window to appear (window creation isn't
    instant - AppOpener's open() returns before the app has finished
    starting up) and maximize it as soon as it does. Gives up silently
    after `timeout` seconds if psutil/pywin32 aren't available or the
    window never shows up (e.g. the app failed to launch).
    """
    if psutil is None or win32gui is None:
        return False

    elapsed = 0.0
    while elapsed < timeout:
        windows = _find_running_app_windows(app_name)
        if windows:
            return _focus_window(windows[0])
        time.sleep(interval)
        elapsed += interval
    return False


# Some very common apps have a colloquial name that doesn't match their
# real executable/process name at all - most notably Edge, whose process
# is msedge.exe, not edge.exe. AppOpener's own name matching (and our
# process-name matching in _find_running_app_windows) can fail on these,
# so normalize a few well-known ones before either is used. Add to this
# as more mismatches turn up; anything not listed here is passed through
# unchanged (AppOpener's match_closest=True already fuzzy-matches most
# app names fine on its own).
_APP_NAME_ALIASES = {
    "edge": "msedge",
    "microsoft edge": "msedge",
    "ms edge": "msedge",
    "word": "winword",
    "microsoft word": "winword",
    "powerpoint": "powerpnt",
    "microsoft powerpoint": "powerpnt",
    "file explorer": "explorer",
    "explorateur de fichiers": "explorer",
    "explorateur": "explorer",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
}


def _normalize_app_name(app_name: str) -> str:
    return _APP_NAME_ALIASES.get(app_name.strip().lower(), app_name)


# ── Native Windows apps: launched directly, bypassing AppOpener ────────
# AppOpener resolves an app name against the Start Menu's list of
# installed apps, whose DISPLAY NAMES are localized ("Calculatrice" on a
# French install, "Bloc-notes" for Notepad, "Panneau de configuration" for
# Control Panel...). Asking Gemini for "calculator" then handing that
# English string to AppOpener on a non-English Windows install fails to
# match anything - this was the actual cause of native apps opening
# inconsistently depending on the system's display language.
#
# The fix: every built-in Windows utility has an executable name or a
# `ms-*:` URI scheme that is completely language-independent (it's a
# filename or a protocol, never a translated label) - launch those
# directly with subprocess/os.startfile instead of going through
# AppOpener's name search at all. Keys are the canonical (English) names
# the AI is told to use in ai/instructions.txt; add more built-in
# utilities here as they come up, they don't need a French alias since
# this dict is looked up by canonical key, never by the raw display name.
_NATIVE_WINDOWS_APPS = {
    "notepad":            ("exe", "notepad.exe"),
    "calculator":         ("exe", "calc.exe"),
    "paint":              ("exe", "mspaint.exe"),
    "wordpad":            ("exe", "write.exe"),
    "task manager":       ("exe", "taskmgr.exe"),
    "control panel":      ("exe", "control.exe"),
    "registry editor":    ("exe", "regedit.exe"),
    "magnifier":          ("exe", "magnify.exe"),
    "character map":      ("exe", "charmap.exe"),
    "on-screen keyboard":  ("exe", "osk.exe"),
    "cmd":                ("exe", "cmd.exe"),
    "command prompt":     ("exe", "cmd.exe"),
    "powershell":         ("exe", "powershell.exe"),
    "file explorer":      ("exe", "explorer.exe"),
    "explorer":           ("exe", "explorer.exe"),
    # ms-*: URIs - resolved by the OS itself, not by any app name search,
    # so they're immune to display-language mismatches even for apps
    # (Settings, the Store) that don't have a plain .exe to call directly.
    "settings":           ("uri", "ms-settings:"),
    "windows settings":   ("uri", "ms-settings:"),
    "microsoft store":    ("uri", "ms-windows-store:"),
    "store":              ("uri", "ms-windows-store:"),
    "snipping tool":      ("uri", "ms-screenclip:"),
    "camera":             ("uri", "microsoft.windows.camera:"),
    "mail":               ("uri", "outlookmail:"),
    "calendar":           ("uri", "outlookcal:"),
    "photos":             ("uri", "ms-photos:"),
}


def _launch_native_app(app_name: str):
    """
    Returns (process_name_for_focus_tracking, callable) if app_name (after
    alias normalization) matches a native Windows utility, else None. The
    caller launches by invoking the callable, then focuses/maximizes using
    the returned process name - same as it already does for AppOpener
    launches, since process names (unlike window titles) don't localize
    either.
    """
    entry = _NATIVE_WINDOWS_APPS.get(app_name.strip().lower())
    if entry is None:
        return None
    kind, target = entry
    if kind == "exe":
        process_name = target[:-4] if target.lower().endswith(".exe") else target

        def _launch():
            # A console of its own for cmd.exe and PowerShell: Kestrel
            # hides every console window its child processes would get
            # (ai/lazy_import.py), and this is the one case where the
            # user asked to see it. GUI programs ignore the flag.
            subprocess.Popen([target], shell=False, creationflags=_NEW_CONSOLE)
        return process_name, _launch
    else:  # "uri"
        def _launch():
            os.startfile(target)
        # URI-launched apps (Settings, Store...) aren't matched by a
        # simple process-name stem, so there's nothing reliable to focus
        # afterward - the caller just launches and moves on.
        return None, _launch


# Recognized browsers (post-alias-resolution names) - when one of these is
# already running, tool_open_application opens a fresh Ctrl+T tab
# instead of just reusing whatever tab the user already had open,
# so the AI's manipulation never mixes with the user's existing browsing.
_BROWSER_NAMES = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "browser"}


def _is_browser(resolved_name: str) -> bool:
    return resolved_name.strip().lower() in _BROWSER_NAMES


def _open_fresh_browser_tab() -> bool:
    """
    Send Ctrl+T to the now-focused browser to open a brand new tab,
    leaving the user's existing tabs untouched. The window itself was
    already brought to the foreground and maximized just before this is
    called, and Ctrl+T doesn't change that - it only adds a tab - so
    there's nothing further to re-focus or re-maximize here.
    """
    try:
        time.sleep(0.6)
        if not _new_tab():
            return False
        time.sleep(0.5)
        return True
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        if handled:
            return False
        print(f"[tool_open_application] Could not open a fresh browser tab: {e}")
        return False


def tool_open_application(app_name: str) -> str:
    """
    Open an installed application by name - or, if it's already running,
    bring it to the foreground instead of launching a duplicate. Either
    way, the app ends up maximized to fullscreen.
    For browsers specifically (chrome, edge, firefox...), if one is already
    running this opens a brand new tab (Ctrl+T) instead of reusing the
    current one, so automation never touches the user's existing tabs.
    Use this for any installed software: edge, chrome, spotify, discord, notepad, vscode, word, excel...
    NEVER use this for websites (youtube, google, gmail - those need browser navigation).
    NEVER open the Windows search bar to find the app - this tool handles it directly.
    """
    global single_fcn
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    # Typing goes to the app this opens (set when its window is focused),
    # or the window in front, not to one clicked before.
    direct_input.forget_target()

    # Opening an app is the lowest-risk tier - but it still has to go
    # through the SAME per-session limiter as tool_run_command/
    # tool_close_application, or the "low risk actions / session" cap in
    # Settings only ever throttles shell commands and silently lets an
    # unlimited number of app launches through (this was the actual
    # bug: a limit of 3 didn't stop a 4th app from opening, because
    # this function never checked the limiter at all).
    limiter_refusal = safety.limiter.check_and_record(RiskLevel.LOW)
    if limiter_refusal:
        print(f"[tool_open_application] {limiter_refusal}")
        return limiter_refusal

    resolved = _normalize_app_name(app_name)
    if resolved != app_name:
        print(f"Opening App: {app_name!r} (resolved to {resolved!r})...")
    else:
        print(f"Opening App: {app_name}...")
    actions_list.append((app_name, "open"))
    single_fcn = True

    # Native Windows utilities (Notepad, Calculator, Settings, Explorer...)
    # bypass AppOpener entirely - see _NATIVE_WINDOWS_APPS for why. Falls
    # through to the AppOpener path below for anything not on that list
    # (third-party apps like Chrome, Spotify, Discord - their AppOpener
    # display names aren't localized, so the existing path works for them).
    native = _launch_native_app(resolved)
    if native is not None:
        process_name, launch = native
        try:
            focus_target = process_name or resolved
            running_windows = _find_running_app_windows(focus_target)
            if running_windows and _focus_window(running_windows[0]):
                if _is_browser(focus_target) and _open_fresh_browser_tab():
                    return f"{app_name} was already running - opened a new tab (existing tabs left untouched)."
                print(f"[tool_open_application] {focus_target} already running - focused and maximized instead of relaunching.")
                return f"{app_name} was already running - brought it to the foreground and maximized it."

            launch()

            if process_name and _wait_for_window_and_maximize(process_name):
                print(f"[tool_open_application] {resolved} (native) launched and maximized.")
            else:
                print(f"[tool_open_application] {resolved} (native) launched.")
            return f"Opened {app_name}."
        except Exception as e:
            handled = _handle_pyautogui_exception(e)
            if handled:
                return handled
            print(f"[tool_open_application] Error launching native app: {e}")
            return f"Could not open {app_name}: {e}"

    try:
        running_windows = _find_running_app_windows(resolved)
        if running_windows and _focus_window(running_windows[0]):
            if _is_browser(resolved) and _open_fresh_browser_tab():
                print(f"[tool_open_application] {resolved} already running - opened a fresh tab instead of reusing the current one.")
                return f"{app_name} was already running - opened a new tab (existing tabs left untouched)."
            print(f"[tool_open_application] {resolved} already running - focused and maximized instead of relaunching.")
            return f"{app_name} was already running - brought it to the foreground and maximized it."

        result = appopen(f"{resolved}", match_closest=True)

        if _wait_for_window_and_maximize(resolved):
            print(f"[tool_open_application] {resolved} launched and maximized.")
        else:
            print(f"[tool_open_application] {resolved} launched, but couldn't confirm/maximize its window.")

        return str(result)
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        if handled:
            return handled
        print(f"[tool_open_application] Error: {e}")
        return f"Could not open {app_name}: {e}"


def tool_close_application(app_name: str) -> str:
    """
    Close a running application by name (approximate match supported).
    Example: tool_close_application("chrome") will close Google Chrome.
    """
    global single_fcn
    print(f"Closing App: {app_name}...")

    if not risk_gate.require_approval(f"Close the application \"{app_name}\"?"):
        print(f"[tool_close_application] Human denied (or no UI to ask) - refusing.")
        return f"Refused: closing {app_name} was not approved by the human."

    limiter_refusal = safety.limiter.check_and_record(RiskLevel.HIGH)
    if limiter_refusal:
        print(f"[tool_close_application] {limiter_refusal}")
        return limiter_refusal

    running_processes = _get_running_processes()
    matches = difflib.get_close_matches(app_name, running_processes, n=1, cutoff=0.3)

    if matches:
        real_name = matches[0]
        # Argument list, no shell - real_name is matched against the
        # actual running-process list above (not typed straight through
        # from the AI), but building a shell string out of it was still
        # an unnecessary injection surface for no benefit over argv form.
        result = subprocess.run(
            ["taskkill", "/IM", real_name, "/F"],
            shell=False, capture_output=True, creationflags=_NO_CONSOLE_WINDOW,
        )
        if result.returncode == 0:
            single_fcn = True
            print(f"[tool_close_application] Closed: {real_name}")
            return f"Closed {real_name}"
        else:
            return f"Failed to close {real_name}"
    else:
        single_fcn = True
        msg = f"{app_name.upper()} NOT FOUND in running processes."
        print(f"[tool_close_application] {msg}")
        return msg


def tool_write_and_enter(text: str) -> str:
    """
    Type text at the current cursor position, then press Enter.
    Use this for: address bars, search bars, form fields that require confirmation.
    Always click the target field first before calling this.

    Accented and non-Latin characters are typed correctly - text is sent
    by code point rather than by keyboard layout (see ai/unicode_input.py),
    so there is no need to avoid them or spell them out.

    Returns what was typed, so the result is visible in the transcript
    rather than the model having to assume it worked.
    """
    global single_fcn
    if _check_emergency_stop():
        return "Refused: automation is stopped."
    # Delegates rather than repeating the loop. This used to carry its
    # own copy, which had drifted: it never got the trailing Delete that
    # clears an address bar's autocomplete selection, and when _type_text
    # learned to type accented characters this copy would not have.
    typed = _type_text(text)
    if not typed:
        # No Enter after text that didn't all go in: a command cut short
        # would run as it is.
        if typed.started:
            return (f"Stopped typing {_preview(text)} partway: {typed.note}. Part of it is already "
                    f"in the field, and Enter was not pressed. Look at the screen before typing again.")
        return f"Nothing was typed: {typed.note}."
    actions_list.append((text, "write"))
    pressed = _press_key("enter")
    if not pressed:
        return f"Typed {_preview(text)} but didn't press Enter: {pressed.note}."
    actions_list.append(("enter", "enter"))
    time.sleep(0.6)
    single_fcn = True
    return f"Typed {_preview(text)} and pressed Enter."


def tool_enter() -> str:
    """
    Press the Enter key at the current focus position.
    Use when you need to confirm an action without typing anything.
    """
    global single_fcn
    print("Pressing Enter...")
    pressed = _press_key("enter")
    if not pressed:
        return f"Enter was not pressed: {pressed.note}."
    actions_list.append(("enter", "enter"))
    single_fcn = True
    return "Pressed Enter."


# Combinations that close a window or delete for good: asked about first,
# like closing an application.
_KEYS_TO_CONFIRM = frozenset("+".join(sorted(combo.split("+"))) for combo in (
    "alt+f4", "ctrl+w", "ctrl+q", "ctrl+shift+w", "ctrl+f4", "shift+delete",
))

# Other ways of writing those keys, so "shift+del" or "left ctrl+w" is
# asked about too.
_KEY_SPELLINGS = {
    "del": "delete", "control": "ctrl", "ctl": "ctrl", "strg": "ctrl", "suppr": "delete",
    "left ctrl": "ctrl", "right ctrl": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
    "left control": "ctrl", "right control": "ctrl",
    "left alt": "alt", "right alt": "alt", "lalt": "alt", "ralt": "alt", "alt gr": "alt", "altgr": "alt",
    "left shift": "shift", "right shift": "shift", "lshift": "shift", "rshift": "shift",
}


def _needs_confirming(combo: str) -> bool:
    """Whether any step of `combo` closes a window or deletes for good.
    keyboard takes "a, b" as two presses in a row, so each step is
    checked; and it accepts key names of the keyboard layout in use
    ("maj+suppr" on a French one), so the keys themselves are compared
    too, by scan code."""
    for step in combo.split(","):
        parts = [_KEY_SPELLINGS.get(" ".join(part.split()), " ".join(part.split()))
                 for part in step.split("+") if part.strip()]
        if parts and "+".join(sorted(parts)) in _KEYS_TO_CONFIRM:
            return True
    return _confirm_by_scan_code(combo)


_confirm_scan_codes = None


def _scan_code_steps(combo: str):
    """keyboard's reading of a combination: one entry per step, each a
    list of keys, each key the set of scan codes it can be. None when it
    can't be read (keyboard isn't the real one, or the name is unknown)."""
    try:
        steps = keyboard.parse_hotkey(combo)
    except Exception:
        return None
    if not isinstance(steps, tuple):
        return None
    try:
        return [[set(key) for key in step] for step in steps]
    except TypeError:
        return None


def _confirm_by_scan_code(combo: str) -> bool:
    import itertools

    global _confirm_scan_codes
    wanted_keys = _confirm_scan_codes
    if wanted_keys is None:
        readings = [_scan_code_steps(c) for c in sorted(_KEYS_TO_CONFIRM)]
        wanted_keys = [r[0] for r in readings if r and len(r) == 1]
        if wanted_keys:                      # kept once keyboard could read them
            _confirm_scan_codes = wanted_keys
    steps = _scan_code_steps(combo)
    if not steps:
        return False
    for step in steps:
        for wanted in wanted_keys:
            if len(step) != len(wanted):
                continue
            # The same keys in any order, each matching one of the confirm
            # combination's keys (left Ctrl is one of Ctrl's scan codes).
            if any(all(key & want for key, want in zip(order, wanted))
                   for order in itertools.permutations(step)):
                return True
    return False


def tool_press_keys(keys: str) -> str:
    """
    Press one key or a key combination in the window you are working in:
    "enter", "escape", "tab", "delete", "ctrl+a", "ctrl+c", "ctrl+v",
    "ctrl+l", "alt+tab", "win+r", "f5". Parts joined with "+".
    Use it when the user asks for a shortcut or a key, to close a popup
    with Escape, or to move to the next field with Tab. To open an app,
    tool_open_application is still the way.
    """
    global single_fcn
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    combo = "+".join(part.strip() for part in (keys or "").lower().split("+") if part.strip())
    if not combo:
        return "No key was given."
    if _needs_confirming(combo):
        if not risk_gate.require_approval(f"Press {combo}? It closes a window or deletes for good."):
            return f"Refused: pressing {combo} was not approved."
    print(f"Pressing {combo}...")
    if "+" in combo or combo in _WINDOWS_KEY_NAMES:
        pressed = _press_hotkey(combo)
    else:
        pressed = _press_key(combo)
    if not pressed:
        return f"{combo} was not pressed: {pressed.note}."
    # Recorded as a block, the way the macro editor stores a key
    # combination (ai/macro_blocks.py).
    actions_list.append({"type": "hotkey", "keys": combo})
    single_fcn = True
    return f"Pressed {combo}."


def tool_new_page() -> str:
    """
    Open a new browser tab (Ctrl+T).
    Use this when the browser is already open and you need a second destination.
    """
    global single_fcn
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    print("Opening New Tab...")
    opened = _new_tab()
    if not opened:
        return f"No new tab was opened: {opened.note}."
    actions_list.append(("new_page", "ctrl+t"))
    time.sleep(0.5)
    single_fcn = True
    return "Opened a new tab."


def tool_wait() -> None:
    """
    Wait and do nothing for this step.
    USE THIS WHEN:
    - The page is still loading (spinner visible, partial content)
    - An animation or transition is in progress
    - The screenshot is not yet useful (blank, loading state)
    - You need to give an app time to fully open
    Do NOT call more than 3 times in a row for the same state.
    """
    global single_fcn
    print("Waiting...")
    time.sleep(1.5)
    single_fcn = True


def tool_run_command(command: str) -> str:
    """
    Runs ONE plain command (with plain arguments) via cmd.exe and returns
    its combined stdout/stderr plus its exit status. Only base commands on
    a fixed allow-list can run at all, each capped at a risk tier (low /
    medium / high) with its own per-session limit - see ai/safety.py.
    Chaining (&&, &, |), redirection (>, <), and %variable% expansion are
    all refused outright; this runs exactly the one command given, nothing
    it could expand into.

    Use it for things like: checking a tool's version (`python --version`,
    `node -v`), installing/updating packages (`pip install ...`, `npm
    install ...`, `winget install ...`), git operations, listing/reading
    files (`dir`, `type file.txt`), checking system info (`ipconfig`,
    `tasklist`), etc. If the base command isn't on the allow-list, this
    refuses and names what IS allowed - don't try to route around that
    with a different command that does the same thing.

    Don't use this to open or close applications - tool_open_application /
    tool_close_application already handle window focusing and app-name
    resolution that a raw `start`/`taskkill` call here wouldn't.

    Runs synchronously with a 60-second timeout; a command still running
    after that is reported as timed out (it may still be running in the
    background - check for it with a follow-up command, e.g. `tasklist`,
    rather than re-running the original if it might not be idempotent).
    Output is capped at 4000 characters so a runaway command can't blow
    out the chat.

    Like any action that changes something on the user's PC, always
    describe what you're about to run and get a spoken/typed confirmation
    first if it deletes, overwrites, or uninstalls anything, or touches
    files/settings outside the current task - a command run here is not
    sandboxed and its effects aren't undoable by Kestrel.
    """
    if _check_emergency_stop():
        return "Command not run: emergency stop is active."

    stripped = command.strip()
    if not stripped:
        return "Command not run: empty command."

    for ch in safety.DISALLOWED_METACHARACTERS:
        if ch in stripped:
            return (
                f"Command refused: contains '{ch}', which chains, redirects, "
                f"or expands commands. Run exactly one plain command with "
                f"plain arguments per call - no &&, &, |, >, <, or %vars%."
            )

    try:
        # posix=False: Windows paths use backslashes, which shlex's
        # default (POSIX) mode would treat as escape characters and
        # mangle (e.g. "C:\Users\x" losing its backslashes).
        tokens = shlex.split(stripped, posix=False)
    except ValueError as e:
        return f"Command refused: could not parse it ({e})."
    if not tokens:
        return "Command not run: empty command."

    base = tokens[0]
    if safety.is_blocked_command(base):
        import ai.main as ai_main
        return (
            f"Command refused: '{base}' is never allowed through {ai_main.get_assistant_name()} - "
            f"too high a blast radius (can reformat/repartition a drive, "
            f"rewrite boot config, or run an unrestricted script shell). "
            f"Run it yourself in a real terminal if you actually need it."
        )

    risk = safety.classify_command(base)
    if risk is None:
        allowed = ", ".join(sorted(safety.ALLOWED_COMMANDS))
        return f"Command refused: '{base}' isn't on the allowed command list. Allowed: {allowed}."

    if risk == RiskLevel.HIGH:
        if not risk_gate.require_approval(f"Run this command:\n\n{stripped}"):
            print(f"[tool_run_command] Human denied (or no UI to ask) - refusing: {stripped}")
            return "Command refused: this high-risk command was not approved by the human."

    limiter_refusal = safety.limiter.check_and_record(risk)
    if limiter_refusal:
        return limiter_refusal

    print(f"[tool_run_command] Running ({risk.value} risk): {stripped}")
    try:
        # No shell=True: cmd.exe is invoked explicitly and only ever
        # receives the exact tokens shlex parsed out of the command
        # nothing here is ever interpolated into a shell string.
        result = subprocess.run(
            ["cmd.exe", "/c", *tokens],
            shell=False,
            capture_output=True,
            text=True,
            timeout=60,
            # The output is captured and shown in the chat; a console
            # window flashing up on top of everything adds nothing.
            creationflags=_NO_CONSOLE_WINDOW,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if not output:
            output = "(command finished with no output)"
        if len(output) > 4000:
            output = output[:4000] + "\n... (output truncated)"
        status = "succeeded" if result.returncode == 0 else f"exited with code {result.returncode}"
        # Queued for the UI too, not just returned to the model - see
        # ai.main.queue_terminal_output's docstring. Deferred import, same
        # reason the blocked-command branch above does it locally rather
        # than at module level: ai.main imports ai.actions itself (for its
        # tool list), so importing ai.main back at THIS module's top level
        # would be circular.
        import ai.main as ai_main
        ai_main.queue_terminal_output(stripped, output, success=(result.returncode == 0))
        return f"Command {status}.\n{output}"
    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds - it may still be running in the background."
    except Exception as e:
        return f"Failed to run command: {e}"


def tool_screenshot() -> str:
    """
    Capture the current screen right now.
    Use this when the user explicitly asks for a screenshot, or when you
    need to see the current screen state and don't already have a recent
    one to look at.
    The image is attached to your NEXT message automatically (not this
    one, since a tool call can't hand you an image back directly) - so
    after calling this, tell the user the capture is done; you'll be
    able to describe or act on what's in it as soon as you receive it.

    What this returns now also lists the controls of the window in front,
    by name, type and screen position (Windows UI Automation), and what
    changed since your previous screenshot of the same window - including
    when a DIFFERENT control now sits at coordinates where another one
    was. Use the names to be sure which control is which; don't assume a
    button is the same one because it is in the same place.
    """
    # Deferred import: ai.main imports ai.actions at module load time, so
    # importing it back at module level here would be circular.
    from ai.main import trigger_screenshot
    print("Taking Screenshot (tool call)...")
    trigger_screenshot()
    # NOTE: this intentionally does NOT set single_fcn - that flag means
    # "the goal is done" to run_autonomous_mode's step loop (see
    # ai/main.py). A screenshot is the AI looking at the screen, not
    # completing anything; setting it here used to make autonomous mode
    # stop immediately the moment the model glanced at the screen
    # mid-task, treating a look as if it were the finished goal.
    result = "Screenshot taken successfully. It will be attached to your next message."
    try:
        from ai.discovery import screen_map
        controls = screen_map.describe()
    except Exception as e:
        log_debug(f"[actions] screen map skipped: {e}")
        controls = ""
    if controls:
        result += "\n\n" + controls
    return result


def tool_check_control_at(x: int, y: int) -> str:
    """
    Says which control is at screen position (x, y) right now - its name,
    type and box, read from Windows UI Automation - and whether it is the
    same control that was there in your last screenshot or a different
    one.

    Use it before clicking coordinates you got from an earlier screenshot
    when the screen may have changed since (a dialog opened, a list
    scrolled, a page loaded): the same coordinates can hold a different
    button. x and y are screen pixels, as listed with the screenshot.
    """
    try:
        from ai.discovery import screen_map
        return screen_map.check_point(int(x), int(y))
    except Exception as e:
        return f"Couldn't read the control at ({x}, {y}): {e}"


@dataclass
class MappingResult:
    """How a mapping run went: the Mapping page shows it, and
    tool_discover_application_ui words it for the AI."""
    app: str
    ok: bool = False
    reused: bool = False         # a complete earlier map was used; nothing was clicked
    kept_earlier: bool = False   # stopped short, so the earlier complete map was kept
    ending: str = ""             # "complete", "stopped", "screen_limit", "action_limit"
    screens: int = 0             # what this run found (or the reused map holds)
    controls: int = 0
    window: str = ""
    program: str = ""
    path: str = ""
    error: str = ""
    outline: str = ""            # the map the AI can use now


def run_mapping(app_name: str, force_remap: bool = False, ask_limiter: bool = True) -> MappingResult:
    """
    Map an open app, or use its saved map. What tool_discover_application_ui
    and the Mapping page's Start button both run.

    A saved map is used instead of clicking through the app again only
    when the run that made it finished; one that was stopped or hit a
    limit is mapped again. A run that stops short doesn't replace a
    finished map: the finished one is kept.

    ask_limiter: count this against the AI's action budget for the run
    (ai/safety.py). The Mapping page passes False: that budget is for
    what the AI does, not for someone pressing Start.

    One automation at a time (ai/automation_lock.py): while an AI run or
    a macro is going, this refuses and says which.
    """
    result = MappingResult(app=app_name)
    refusal = _check_emergency_stop()
    if refusal:
        result.error = refusal
        return result
    if ask_limiter:
        limiter_refusal = safety.limiter.check_and_record(RiskLevel.MEDIUM)
        if limiter_refusal:
            result.error = limiter_refusal
            return result

    from ai import automation_lock
    try:
        with automation_lock.hold("a mapping run"):
            return _run_mapping(app_name, force_remap, result)
    except automation_lock.Busy as busy:
        result.error = str(busy)
        return result


def _run_mapping(app_name: str, force_remap: bool, result: MappingResult) -> MappingResult:
    from ai.discovery import DiscoveryError, map_application_ui
    from ai.discovery import events as discovery_events
    from ai.discovery.tree import ActionTree, map_path

    path = map_path(app_name)
    earlier = None
    if os.path.exists(path):
        try:
            earlier = ActionTree.load(path)
        except Exception as e:
            print(f"[mapping] The saved map at {path} couldn't be read ({e}); mapping {app_name!r} again.")

    if earlier is not None and earlier.complete and not force_remap:
        print(f"[mapping] {app_name!r} was mapped before ({path}); using that map instead of clicking through the app again.")
        _describe_map(result, earlier, path)
        result.ok = result.reused = True
        return result

    print(f"[mapping] Mapping {app_name!r}...")
    discovery_events.notify_started(app_name)
    tree = None
    try:
        try:
            def window_found(title: str, program: str) -> None:
                # Known before the run starts, so a run that fails partway
                # still says which window it was (the Mapping page brings
                # Kestrel back over that app).
                result.window, result.program = title, program

            tree = map_application_ui(
                app_name,
                on_progress=discovery_events.notify_progress,
                on_node=discovery_events.notify_node,
                on_edge=discovery_events.notify_edge,
                on_window=window_found,
            )
        except DiscoveryError as e:
            print(f"[mapping] {e}")
            result.error = str(e)
            return result
        except Exception as e:
            print(f"[mapping] Mapping {app_name!r} failed: {e}")
            result.error = _handle_pyautogui_exception(e) or str(e)
            return result

        if not tree.complete and earlier is not None and earlier.complete:
            print(f"[mapping] The run ended early ({tree.ending}); keeping the earlier full map at {path}.")
            _describe_map(result, earlier, path)
            result.kept_earlier = True
        else:
            _describe_map(result, tree, tree.save(path))
        result.screens, result.controls = tree.node_count(), tree.action_count()
        result.ending = tree.ending
        result.ok = True
        return result
    finally:
        # Whatever happened, so the overlay and the Mapping page never
        # stay on "mapping" after a failed run.
        discovery_events.notify_finished(
            app_name,
            tree.node_count() if tree is not None else 0,
            tree.action_count() if tree is not None else 0,
        )


def _describe_map(result: MappingResult, tree, path: str) -> None:
    result.screens, result.controls = tree.node_count(), tree.action_count()
    result.ending = tree.ending
    result.window, result.program = tree.window_title, tree.program
    result.path = path
    result.outline = tree.outline()


_ENDINGS = {
    "stopped": "it was stopped",
    "screen_limit": "it reached the most screens one run maps",
    "action_limit": "it reached the most clicks one run makes",
}

_USING_A_MAP = ("To press one of its controls or type into one of its fields, call "
                "tool_use_mapped_control with the app name and the control's name as the "
                "map shows it, and the screen id when two screens have a control of that name.")


def _mapping_report(result: MappingResult) -> str:
    """A mapping run, for the AI."""
    app = result.app
    if result.error:
        return f"Could not map {app}: {result.error}"
    where = f" (window {result.window!r})" if result.window else ""
    if result.reused:
        return (f"{app} was mapped before{where}: {result.screens} screen(s), {result.controls} "
                f"control(s). {_USING_A_MAP} If the app has changed since, call this again with "
                f"force_remap=True to map it again.\n\nThe saved map (positions are from when it "
                f"was made):\n{result.outline}")
    if result.kept_earlier:
        return (f"Mapping {app} ended before it finished ({_ENDINGS.get(result.ending, result.ending)}), "
                f"after {result.screens} screen(s). The earlier full map of {app} is kept, and it is "
                f"the one used. {_USING_A_MAP}\n\nThe map:\n{result.outline}")
    if result.ending == "complete":
        head = f"Mapped {app}{where}"
    else:
        head = (f"Mapping {app}{where} ended before it finished "
                f"({_ENDINGS.get(result.ending, result.ending)}); what it found is saved")
    return (f"{head}: {result.screens} screen(s), {result.controls} control(s). {_USING_A_MAP}\n\n"
            f"The map (screen pixels, each screen under the control that opens it):\n{result.outline}")


def tool_discover_application_ui(app_name: str, force_remap: bool = False) -> str:
    """
    Map an open application's screens and controls through Windows UI
    Automation, so its controls can then be pressed by name with
    tool_use_mapped_control instead of one screenshot per click.

    Use it once per app you will work with repeatedly. It clicks through
    every screen it can reach from the current one and types a test value
    into empty one-line fields, so it takes a while and works in the app's
    window. Buttons that delete, send, pay, post or log out are recorded
    but never pressed while mapping.

    The app must already be open (tool_open_application first). The
    window is found by its program name or its title.

    A finished earlier map is reused without clicking anything. Pass
    force_remap=True when the app has changed since (an update, a new
    layout).
    """
    global single_fcn
    result = run_mapping(app_name, force_remap=force_remap)
    if result.ok and not result.reused:
        single_fcn = True
    return _mapping_report(result)


def _same_words(text: str) -> str:
    return " ".join((text or "").lower().split())


def _mapped_controls(tree, control: str, screen: str = "") -> list:
    """(screen, action) pairs in a map whose control is named `control`
    (or has that action id), each control once however many screens list
    it. The whole name, never a part: "Save" is not "AutoSave"."""
    wanted, screen_key = _same_words(control), _same_words(screen)
    found, seen = [], set()
    if not wanted:
        return found
    for node_id, node in tree.nodes.items():
        if screen_key and screen_key not in (_same_words(node_id), _same_words(node.title)):
            continue
        for action in node.discovered_actions:
            if action.accessibility_path in seen:
                continue
            if wanted in (_same_words(action.name), _same_words(action.action_id)):
                found.append((node, action))
                seen.add(action.accessibility_path)
    return found


def _similar_control_names(tree, control: str, limit: int = 5) -> list:
    names = sorted({a.name for node in tree.nodes.values() for a in node.discovered_actions if a.name})
    by_lower = {name.lower(): name for name in names}
    close = difflib.get_close_matches(control.lower(), list(by_lower), n=limit, cutoff=0.6)
    partial = [name for name in names if control.lower() in name.lower() and name.lower() not in close]
    return [by_lower[name] for name in close] + partial[:max(0, limit - len(close))]


def _where_on_the_map(tree, node) -> str:
    """A screen as the map outline names it, and the control that opens it."""
    label = node.current_node_id + (f' "{node.title}"' if node.title else "")
    parent = tree.nodes.get(node.parent_node_id) if node.parent_node_id else None
    if parent is not None:
        opener = next((a for a in parent.discovered_actions if a.action_id == node.via_action_id), None)
        if opener is not None:
            label += f", opened by {opener.name or opener.element_type!r} on {parent.current_node_id}"
    return label


def tool_use_mapped_control(app_name: str, control: str, screen: str = "", text: str = "") -> str:
    """
    Press a control in an app mapped with tool_discover_application_ui: a
    button, menu item, tab, check box or list item, by its name as the
    map shows it. For a text field, pass `text` and it is typed in.
    `screen` is the screen id from the map (screen_notepad_002), needed
    only when two screens have a control with that name.

    The control is looked up in the app's window as it is now. If it
    isn't there (another screen is open), nothing is pressed and the
    reply says which screen has it: open that one first. Controls the
    map marks destructive ask the user first.
    """
    global single_fcn
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    from ai.discovery.errors import DiscoveryError
    from ai.discovery.tree import ActionTree, map_path

    path = map_path(app_name)
    if not os.path.exists(path):
        return f"{app_name} has no saved map. Map it first with tool_discover_application_ui."
    try:
        tree = ActionTree.load(path)
    except Exception as e:
        return f"The saved map of {app_name} couldn't be read: {e}"
    matches = _mapped_controls(tree, control, screen)
    if not matches:
        on = f" on {screen}" if screen else ""
        similar = _similar_control_names(tree, control)
        hint = f" Close names in the map: {', '.join(repr(n) for n in similar)}." if similar else ""
        return (f"The map of {app_name} has no control named {control!r}{on}. Use the whole name "
                f"as the map shows it.{hint}")

    try:
        from ai.discovery.accessibility import get_backend
        backend = get_backend(app_name, take=False)
    except DiscoveryError as e:
        return f"Couldn't reach {app_name}'s window: {e}"
    # A minimized window's controls are all off screen.
    backend.show_if_minimized()

    found = None
    for node, action in matches:
        element = backend.find_by_path(action.accessibility_path)
        if element is None or element.is_offscreen or element.control_type != action.element_type:
            continue
        # A path made of positions ("Pane[0]/Button[3]") points at
        # whatever is in that place now, which after a change in the app
        # is another control: its name has to match too.
        if "[" in action.accessibility_path and action.name and \
                _same_words(element.name) != _same_words(action.name):
            continue
        found = (node, action, element)
        break
    if found is None:
        places = list(dict.fromkeys(_where_on_the_map(tree, node) for node, _ in matches))
        return (f"{control!r} isn't in {app_name}'s window now, so nothing was pressed. The map has "
                f"it on {'; '.join(places)}. Open that screen first.")

    node, action, element = found
    label = element.name or action.name or control
    if not element.is_enabled:
        return f"{label!r} is greyed out in {app_name} right now; nothing was pressed."
    if action.is_destructive or safety.is_destructive_label(label):
        if not risk_gate.require_approval(f"Press \"{label}\" in {app_name}?"):
            return f"Refused: pressing {label!r} in {app_name} was not approved."

    if action.requires_input:
        if not text:
            return f"{label!r} is a field. Pass the text to type into it."
        if not backend.input_value(element, text):
            return _check_emergency_stop() or f"{_preview(text)} couldn't be typed into {label!r}."
        single_fcn = True
        return f"Typed {_preview(text)} into {label!r} in {app_name}."
    if text:
        return f"{label!r} is a {action.element_type.lower()}, not a field: it takes no text. Nothing was pressed."
    if not backend.activate(element):
        return _check_emergency_stop() or f"{label!r} in {app_name} couldn't be pressed."
    single_fcn = True
    return f"Pressed {label!r} in {app_name}."


def _sanitize_macro_name(name: str) -> str:
    """
    Turn an arbitrary AI-provided name into a safe Python identifier suffix,
    so a malformed name (accents, spaces, punctuation, a Python keyword,
    or an empty string) can never produce invalid Python and corrupt
    actions.py on the next importlib.reload().
    """
    # Strip accents (e.g. é → e)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Keep only word characters, collapse everything else to underscores
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", ascii_only).strip("_").lower()

    if not safe or not safe[0].isalpha():
        safe = f"macro_{safe}" if safe else "macro"

    if keyword.iskeyword(safe):
        safe = f"{safe}_macro"

    return safe[:60]  # keep it reasonable


last_saved_name = None       # name of the macro tool_name_action most recently wrote
last_saved_actions = None    # its raw recorded actions_list, before it got cleared
last_appended_block = None   # kept for backward compatibility with older call sites; unused now that macros are JSON records (see ai.macro_store)


def tool_name_action(name: str, description: str | None = None) -> None:
    """
    Save the current recorded action sequence as a reusable macro function.
    CALL THIS ONLY when the autonomous goal is 100% complete and confirmed on screen.
    The name should describe what the macro DOES in snake_case (3-5 words).
    Examples: 'open_youtube_lofi', 'send_email_gmail', 'spotify_play_chill'
    NEVER call this as a way to give up or exit early.

    If a macro with this exact name already exists, this REPLACES it -
    useful when a previously learned macro stopped working (e.g. the UI
    it
    was recorded against changed) and you're re-recording the same task
    from scratch. If you know an old macro is broken but aren't
    re-recording it right now, call tool_delete_macro instead so it stops
    being offered.
    """
    global fcn_name, actions_list, last_saved_name, last_saved_actions, last_appended_block
    from ai import macro_store

    recorded_data = list(actions_list)
    name = _sanitize_macro_name(name)
    if not recorded_data:
        # Nothing was done on screen: the goal was met by reading the web
        # (tool_read_url and friends are not recorded), by a command, or
        # was already true. There is nothing to replay, and an empty macro
        # would only sit in the tool list looking like a skill.
        print(f"[tool_name_action] Goal complete. Nothing was done on screen, so no macro was saved ({name}).")
        last_saved_name = None
        last_saved_actions = None
        last_appended_block = None
        fcn_name = True
        return
    print(f"Naming Action: {name}")

    last_saved_name = name
    last_saved_actions = recorded_data
    last_appended_block = None

    replacing = macro_store.macro_exists(name)
    macro_store.save_literal_macro(name, recorded_data, description=description)

    import ai.actions as actions_module
    macro_store.load_and_register_all(actions_module)

    print(f"[tool_name_action] {'Replaced' if replacing else 'Saved'} macro: tool_{name}")
    actions_list = []
    fcn_name = True


def tool_delete_macro(macro_name: str) -> str:
    """
    Deletes a previously learned macro (tool_<macro_name>) so it stops
    being offered/callable. Use this when a saved macro's recorded clicks
    no longer work because the UI it was learned against has changed (a
    redesigned website, a moved button, an app update, ...) - rather than
    silently continuing to call, or ignoring, a macro you know is broken.

    Fuzzy-matches macro_name the same way tool_close_application matches a
    running process name, so an approximate name (or one with/without the
    'tool_' prefix) still resolves correctly.

    After deleting, the same task can be re-learned from scratch by
    completing it again in autonomous mode - the name is immediately free
    for tool_name_action to reuse (recording under the exact same name
    also just replaces it directly, without needing this first).
    """
    from ai import macro_store

    resolved = macro_store.resolve_macro_name(macro_name)
    if resolved is None:
        existing = ", ".join(macro_store.list_macro_names()) or "(none saved yet)"
        return f"No saved macro matching {macro_name!r} was found. Saved macros: {existing}."

    macro_store.delete_macro(resolved)

    # Incremental, not a full rebuild. This used to call
    # ai_main.refresh_actions(), which reloads this whole module,
    # re-reads and re-execs every surviving macro from disk, re-renders
    # the system prompt and builds a fresh provider chat session - all to
    # make one function stop existing. drop_macro_tool removes just that
    # one and defers the session rebuild to the next message.
    import ai.main as ai_main
    ai_main.drop_macro_tool(resolved)

    print(f"[tool_delete_macro] Deleted macro: tool_{resolved}")
    return f"Deleted macro tool_{resolved}. It can be re-recorded from scratch next time it's needed."

