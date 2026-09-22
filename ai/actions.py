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
import pyautogui
# NOTE: deliberately no `from typing import Literal` here any more - a
# Literal[...] annotation on a tool_* function breaks Gemini's automatic
# function calling at call time. See the comment under
# tool_generate_file for the full story.

# Fail-safe kill switch: PyAutoGUI already defaults to this, but it's set
# explicitly here since the whole point of it is safety-critical - never
# want it silently off because some code elsewhere touched the default.
# Slamming the mouse into ANY screen corner (top-right included) raises
# pyautogui.FailSafeException from inside whatever pyautogui call is
# running at that instant. run_autonomous_mode() in ai/main.py is what
# actually turns that into a full stop instead of just a caught error.
pyautogui.FAILSAFE = True

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
from urllib.parse import urlparse
from AppOpener import open as appopen

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

    sections = [f"# {page.title}" if page.title else "", f"Source: {page.url}", "", page.text]
    if include_images and page.images:
        sections.append(_attach_page_images(page, url))
    return "\n".join(part for part in sections if part).strip()


def tool_crawl_site(url: str, max_pages: int = 10, include_images: bool = False) -> str:
    """
    Reads several pages of one site, so you can answer from the site as
    a whole rather than from whichever page happened to be linked.

    Finds the pages the way the site itself says to: robots.txt, then
    its sitemap, then the links on the starting page if it has neither.
    A site whose robots.txt asks not to be crawled is not crawled.

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
    failed = [p for p in pages if p.error]

    if not readable:
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
            if step_type == "click":
                x, y = int(step.get("x", 0)), int(step.get("y", 0))
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.click()
                print(f"[play] click ({x}, {y})")
            elif step_type == "right_click":
                x, y = int(step.get("x", 0)), int(step.get("y", 0))
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.click(button="right")
                print(f"[play] right click ({x}, {y})")
            elif step_type == "write":
                value = step.get("value", "")
                _type_text(value)
                print(f"[play] write: {value!r}")
            elif step_type == "enter":
                keyboard.press_and_release("enter")
                print("[play] enter")
            elif step_type == "hotkey":
                combo = str(step.get("keys", "")).strip()
                if combo:
                    keyboard.press_and_release(combo)
                    print(f"[play] hotkey {combo}")
            elif step_type == "new_tab":
                pyautogui.hotkey("ctrl", "t")
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


def _type_text(text: str) -> None:
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
    """
    from ai import unicode_input

    for ch in text:
        if not unicode_input.send_char(ch):
            _type_char_fallback(ch)
    time.sleep(0.05)  # let an async autocomplete suggestion actually land first
    pyautogui.press("delete")


def click_at_target_or_position(target: str, x: int, y: int, button: str = "left") -> str:
    """
    Click a described UI element using vision (get_position_object_and_click)
    if possible, falling back to the fixed (x, y) coordinates recorded when
    the macro was made - in case the layout changed since then, or the
    vision call fails for any reason. button="right" opens a context menu.
    Used by macros generalized via ai/macro_generalizer.py.
    """
    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    try:
        import ai.main as ai_main  # local import: ai.main already imports this module
        result = ai_main.get_position_object_and_click(target, button=button)
        if isinstance(result, str) and result.lower().startswith("could not locate"):
            raise RuntimeError(result)
        return result
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        if handled:
            return handled
        print(f"[macro] Vision click for {target!r} failed ({e}) - falling back to ({x}, {y})")
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click(button=button)
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

    print(f"Playing Actions... (delay between steps: {action_delay}s)")

    # Anything with control blocks in it goes through the interpreter;
    # a plain recording (the overwhelmingly common case, and every
    # macro saved before this existed) takes the original flat loop
    # below completely unchanged.
    if any(isinstance(a, dict) for a in recorded_actions):
        program = blocks.parse_program(
            [a if isinstance(a, dict) else _legacy_action_to_step(a) for a in recorded_actions]
        )
        try:
            _run_block_nodes(program, action_delay)
        except MacroStopped as e:
            print(f"[play] {e}")
        import ai.main as ai_main
        ai_main.play_notification_sound("macro_finished")
        return

    for action in recorded_actions:
        if emergency_stop:
            print("[play] EMERGENCY STOP ACTIVE - aborting macro replay.")
            break
        try:
            if len(action) == 3 and action[0] == "click":
                _, x, y = action
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.click()
                print(f"[play] click ({x}, {y})")

            elif len(action) == 3 and action[0] == "right_click":
                _, x, y = action
                pyautogui.moveTo(x, y, duration=0.2)
                pyautogui.click(button="right")
                print(f"[play] right click ({x}, {y})")

            elif len(action) == 2 and action[1] == "write":
                text, _ = action
                _type_text(text)
                print(f"[play] write: {text!r}")

            elif len(action) == 2 and action[0] == "enter":
                keyboard.press_and_release("enter")
                print("[play] enter")

            elif len(action) == 2 and action[0] == "new_page":
                pyautogui.hotkey('ctrl', 't')
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

    # Local import: ai.main imports this module at module load time, so
    # importing it back at module level here would be circular (same
    # reason click_at_target_or_position/tool_screenshot import it locally
    # instead of at the top of the file).
    import ai.main as ai_main
    ai_main.play_notification_sound("macro_finished")


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
            shell=False, text=True
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
            subprocess.Popen([target], shell=False)
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
        pyautogui.hotkey("ctrl", "t")
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
            shell=False, capture_output=True
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
    _type_text(text)
    actions_list.append((text, "write"))
    keyboard.press("enter")
    actions_list.append(("enter", "enter"))
    time.sleep(0.6)
    single_fcn = True
    preview = text if len(text) <= 60 else text[:57] + "..."
    return f"Typed {preview!r} and pressed Enter."


def tool_enter() -> None:
    """
    Press the Enter key at the current focus position.
    Use when you need to confirm an action without typing anything.
    """
    global single_fcn
    print("Pressing Enter...")
    keyboard.press("enter")
    actions_list.append(("enter", "enter"))
    single_fcn = True


def tool_new_page() -> None:
    """
    Open a new browser tab (Ctrl+T).
    Use this when the browser is already open and you need a second destination.
    """
    global single_fcn
    if _check_emergency_stop():
        return
    print("Opening New Tab...")
    pyautogui.hotkey('ctrl', 't')
    actions_list.append(("new_page", "ctrl+t"))
    time.sleep(0.5)
    single_fcn = True


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
    return "Screenshot taken successfully. It will be attached to your next message."


def tool_discover_application_ui(app_name: str, force_remap: bool = False) -> str:
    """
    Map an already-open application's screens and controls via structural
    accessibility parsing (Windows UI Automation) instead of vision -
    Phase 2's Structural UI Mapping engine. Use this ONCE per app, the
    first time Kestrel needs to work with it repeatedly, rather than
    relying on get_position_object_and_click for every single click going
    forward: it explores every reachable screen from the current one,
    typing mock values into inputs to see what they lead to, and produces
    a reusable library of atomic tool_click_*/tool_input_* functions that
    call ai.discovery.runtime immediately after this returns (no restart
    needed) - plus a JSON state graph saved under ai/discovery/trees/.

    Requires the app to already be open and focused/findable by window
    title (open it first with tool_open_application if it isn't running).
    Only works on Windows today - refuses with an explanation otherwise.

    This can take a while (it's actively clicking through the app) and
    will visibly move focus/mouse - treat it like any other action that
    takes over the screen. Destructive-looking controls (delete, purge,
    submit order, ...) are recorded but never clicked during the mapping
    run itself.

    force_remap: bug fix - this used to click through the ENTIRE app
    from scratch every single time it was called, even for an app
    that already had a saved state graph on disk from a previous run
    (map_application_ui() always started from an empty seen_signatures
    dict, with nothing here ever checking ai/discovery/trees/ first).
    The visible symptom was exactly "it keeps going back into the same
    buttons even though it already mapped them" - every call re-clicked
    every already-known screen, permanently, no matter how many times
    it had already been run. Now, unless force_remap=True, an existing
    saved tree for this app is loaded and its actions re-registered
    (cheap - no clicking, no window focus stolen) instead of re-running
    the click-through. Pass force_remap=True when the app's UI is known
    to have changed (an update, a redesign) and the saved map is stale.
    """
    refusal = _check_emergency_stop()
    if refusal:
        return refusal

    limiter_refusal = safety.limiter.check_and_record(RiskLevel.MEDIUM)
    if limiter_refusal:
        return limiter_refusal

    from ai.discovery import map_application_ui, compile_action_tree, DiscoveryError
    from ai.discovery import events as discovery_events
    from ai.discovery.tree import ActionTree

    if not force_remap:
        existing_path = ActionTree(app_name=app_name)._default_path()
        if os.path.exists(existing_path):
            try:
                existing_tree = ActionTree.load(existing_path)
                actions_path = compile_action_tree(existing_tree)
                from ai.discovery.compiler import register_compiled_actions
                import ai.actions as actions_module
                registered = register_compiled_actions(actions_module, existing_tree)
                print(f"[tool_discover_application_ui] {app_name!r} already mapped ({existing_path}) - reusing it instead of re-clicking through the app.")
                return (
                    f"{app_name} was already mapped previously: {existing_tree.node_count()} screen(s), "
                    f"{existing_tree.action_count()} action(s), loaded from {existing_path}. Re-registered "
                    f"{registered} atomic action(s) (tool_click_*/tool_input_* for {app_name}) - they're "
                    f"callable now, no re-clicking needed. If the app's UI has changed since this map was "
                    f"made, call this again with force_remap=True to redo it from scratch."
                )
            except Exception as e:
                print(f"[tool_discover_application_ui] Existing map at {existing_path} couldn't be loaded ({e}) - mapping fresh instead.")

    print(f"[tool_discover_application_ui] Starting mapping run of {app_name!r}...")
    discovery_events.notify_started(app_name)
    tree = None
    try:
        try:
            tree = map_application_ui(app_name, on_progress=discovery_events.notify_progress)
        except DiscoveryError as e:
            print(f"[tool_discover_application_ui] {e}")
            return f"Could not discover {app_name}: {e}"
        except Exception as e:
            handled = _handle_pyautogui_exception(e)
            if handled:
                return handled
            print(f"[tool_discover_application_ui] Unexpected error: {e}")
            return f"UI mapping of {app_name} failed: {e}"

        tree_path = tree.save()
        actions_path = compile_action_tree(tree)

        from ai.discovery.compiler import register_compiled_actions
        import ai.actions as actions_module
        registered = register_compiled_actions(actions_module, tree)

        global single_fcn
        single_fcn = True

        return (
            f"Mapped {app_name}: {tree.node_count()} screen(s), {tree.action_count()} action(s) "
            f"discovered. State graph saved to {tree_path}. Compiled {registered} atomic action(s) "
            f"to {actions_path} - they're already callable now (tool_click_*/tool_input_* for {app_name})."
        )
    finally:
        # Guaranteed regardless of how the run ended (success, a
        # DiscoveryError, an unexpected exception) - otherwise the
        # Mapping Monitor window (app/mapping_monitor.py) would stay
        # shown/spinning forever after a failed run, exactly the kind
        # of "cleanup skipped on the error path" bug the screenshot-leak
        # fix earlier in this file's history already taught this app not
        # to repeat.
        discovery_events.notify_finished(
            app_name,
            tree.node_count() if tree is not None else 0,
            tree.action_count() if tree is not None else 0,
        )


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

