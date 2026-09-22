# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy, QSpacerItem, QPushButton, QApplication, QTextEdit,
    QPlainTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QEvent, QSize, QRectF
from PyQt6.QtGui import (
    QKeyEvent, QIcon, QPixmap, QDragEnterEvent, QDropEvent, QFont, QPainter, QFontMetrics,
    QPainterPath,
)
import os
import re
import time
import base64
import json
import app.chat_database as db
import ai.file_context as file_context
import ai.main as ai_main
import ai.profiles as profiles

import app.theme as theme
import app.icons as icons
from app.file_reveal import reveal_in_explorer
from app.styled_dropdown import StyledDropdown
from app.smooth_scroll import install_smooth_scroll, SmoothScroller
from ai.log import log_input

from paths import BASE_PATH
from app.workers.title_worker import TitleWorker
from app.i18n import tr
root_path = BASE_PATH


# ── Lightweight markdown rendering for chat bubbles ─────────────────────────
# Bubbles used to be plain QLabel text - **bold**, `code`, bullet lists,
# etc. all showed up completely literally, asterisks and all, since
# nothing ever parsed them. QLabel supports a small HTML subset natively
# (setTextFormat(RichText)), so this converts the handful of markdown
# constructs models actually use in replies into that subset, without
# pulling in a full markdown dependency for what's a fairly small,
# well-known set of patterns.
_MD_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<![_\w])_(?!_)(.+?)(?<!_)_(?!_)")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
_MD_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.+)$")
_MD_NUMBERED_RE = re.compile(r"^(\s*)\d+\.\s+(.+)$")
_MD_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,})\s*$")
_MD_CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


def _md_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(text: str) -> str:
    """Applies inline-only markdown (bold/italic/strike/code/links) to an
    already-HTML-escaped line. Code spans are pulled out and restored
    last so bold/italic markers accidentally appearing inside `code`
    never get touched."""
    placeholders = []

    def _stash_code(m):
        placeholders.append(f"<code style='background:rgba(127,127,127,0.18);"
                             f"border-radius:4px;padding:1px 5px;'>{m.group(1)}</code>")
        return f"\x00{len(placeholders) - 1}\x00"

    text = _MD_INLINE_CODE_RE.sub(_stash_code, text)
    text = _MD_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _MD_BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _MD_ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _MD_STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    for i, chunk in enumerate(placeholders):
        text = text.replace(f"\x00{i}\x00", chunk)
    return text


def markdown_to_html(raw: str) -> str:
    """
    Converts the common markdown a model's reply might contain - **bold**,
    *italic*/_italic_, `inline code`, ```fenced code blocks```, ~~strike~~,
    [links](url), #/##/### headers, and -/*/1. lists - into the small HTML
    subset QLabel's RichText mode understands. Anything not recognized as
    one of those constructs is left exactly as typed (HTML-escaped so a
    literal '<' or '&' in a reply never gets mistaken for markup).
    """
    if raw is None:
        return ""

    # Fenced code blocks first, and stashed whole - nothing inside one
    # (asterisks, underscores, '#' at line start, ...) should ever be
    # treated as markdown.
    #
    # NOTE on this specific block's own styling: this is the FALLBACK
    # renderer only (user messages, the "thinking" panel, and the
    # in-progress streaming bubble before it finishes) - the real,
    # finished assistant reply instead gets real per-code-block QWidget
    # widgets (see _make_code_block_widget below), because Qt's rich-text
    # engine (what setTextFormat(RichText) on a QLabel actually uses)
    # only understands a small, inconsistent slice of CSS: border-radius
    # on a <pre>/<div> specifically is NOT part of what it supports, so
    # no amount of rewriting that property here was ever going to make
    # rounded corners show up - hence only background-color below, which
    # Qt's rich text DOES honor.
    blocks = []

    def _stash_block(m):
        lang = m.group(1).strip()
        code = m.group(2).rstrip("\n")
        blocks.append(
            "<table width='100%' cellspacing='0' cellpadding='10' "
            f"style='background-color:#1e1e1e;margin:6px 0px;'><tr><td>"
            f"<span style='color:#7a7a7a;font-family:\"Segoe UI\";font-size:11px;'>{_md_escape(lang) or 'code'}</span><br>"
            f"<span style='font-family:Consolas,\"Courier New\",monospace;font-size:13px;'>"
            f"{_highlight_code_html(code, lang)}</span>"
            "</td></tr></table>"
        )
        return f"\x01{len(blocks) - 1}\x01"

    raw = _MD_CODE_FENCE_RE.sub(_stash_block, raw)

    out_lines = []
    for line in raw.split("\n"):
        if "\x01" in line:
            out_lines.append(line)  # a stashed code block placeholder - passes through untouched
            continue

        escaped = _md_escape(line)

        header_match = _MD_HEADER_RE.match(escaped)
        if header_match:
            level = len(header_match.group(1))
            size = {1: "18px", 2: "16px", 3: "14.5px"}.get(level, "13.5px")
            out_lines.append(f"<div style='font-size:{size};font-weight:700;margin:4px 0px;'>"
                              f"{_md_inline(header_match.group(2))}</div>")
            continue

        if _MD_HR_RE.match(escaped):
            out_lines.append("<hr>")
            continue

        bullet_match = _MD_BULLET_RE.match(escaped)
        numbered_match = _MD_NUMBERED_RE.match(escaped)
        if bullet_match:
            out_lines.append(f"&nbsp;&nbsp;•&nbsp; {_md_inline(bullet_match.group(2))}")
            continue
        if numbered_match:
            out_lines.append(f"&nbsp;&nbsp;{_md_inline(numbered_match.group(2))}")
            continue

        out_lines.append(_md_inline(escaped))

    html = "<br>".join(out_lines)
    for i, block in enumerate(blocks):
        html = html.replace(f"\x01{i}\x01", block)
    return _insert_soft_breaks(html)


# A zero-width space: invisible, copies as nothing meaningful, and Qt's
# text layout treats it as a legal place to break a line.
_SOFT_BREAK = "​"
# How many characters a run may reach before one is inserted. Long
# enough that ordinary words and most identifiers are never touched,
# short enough that a URL or a Windows path can't outrun the column.
_SOFT_BREAK_EVERY = 40


def _insert_soft_breaks(html: str, limit: int = _SOFT_BREAK_EVERY) -> str:
    """
    Gives Qt somewhere to wrap inside an unbroken run of characters - a
    URL, a file path, a base64 blob, a hash.

    Word wrap can only break at an existing break opportunity, so a
    single 400-character "word" has none: the label's minimum width
    becomes that word's full width, the row demands more space than the
    viewport has, and the whole conversation ends up scrolling
    sideways. MESSAGE_MAX_WIDTH caps the column, but a cap can't
    conjure a break point - only this can.

    Walks the rendered HTML rather than the source text so it can leave
    markup alone: characters inside a <tag> and inside an &entity; are
    skipped entirely, which is why this runs last instead of being
    folded into the escaping (a zero-width space inside "&amp;" would
    corrupt it, and one inside a link's href would break the link).
    """
    out = []
    run = 0
    i = 0
    length = len(html)
    while i < length:
        ch = html[i]
        if ch == "<":  # markup - copied verbatim, never counted
            close = html.find(">", i)
            if close == -1:
                out.append(html[i:])
                break
            out.append(html[i:close + 1])
            i = close + 1
            run = 0
            continue
        if ch == "&":  # an entity is one character to the reader
            end = html.find(";", i)
            if end != -1 and end - i <= 10:
                out.append(html[i:end + 1])
                i = end + 1
                run += 1
                if run >= limit:
                    out.append(_SOFT_BREAK)
                    run = 0
                continue
        if ch.isspace() or ch == _SOFT_BREAK:
            run = 0
        else:
            run += 1
        out.append(ch)
        i += 1
        if run >= limit:
            out.append(_SOFT_BREAK)
            run = 0
    return "".join(out)


# ── Lightweight, dependency-free "syntax highlighting" for code blocks ──────
# Not a real per-language lexer/grammar (that would mean a separate
# implementation per language) - one shared regex recognizes the constructs
# common across most C-like/Python-like languages (strings, #/// line
# comments, /* */ block comments, numbers, likely-function-names, and a
# cross-language keyword list) well enough to read like real syntax
# highlighting at a glance, the same way chatgpt.com's own code blocks do.
_CODE_KEYWORDS = {
    "def", "class", "return", "if", "elif", "else", "for", "while", "import", "from", "as",
    "try", "except", "finally", "with", "in", "is", "not", "and", "or", "True", "False", "None",
    "lambda", "yield", "break", "continue", "pass", "raise", "global", "nonlocal", "assert", "del",
    "async", "await", "function", "const", "let", "var", "this", "new", "extends", "implements",
    "interface", "type", "export", "default", "public", "private", "protected", "static", "void",
    "null", "undefined", "true", "false", "switch", "case", "do", "typeof", "instanceof", "struct",
    "enum", "namespace", "using", "include", "package", "throw", "throws", "self", "super", "match",
}
_CODE_TOKEN_RE = re.compile(
    r'(?P<string>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
    r'|(?P<comment>#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)'
    r'|(?P<number>\b\d+\.?\d*\b)'
    r'|(?P<funcname>\b[A-Za-z_][A-Za-z0-9_]*\b(?=\s*\())'
    r'|(?P<keyword>\b(?:' + "|".join(_CODE_KEYWORDS) + r')\b)'
)
_CODE_TOKEN_COLORS = {
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
    "funcname": "#dcdcaa",
    "keyword": "#569cd6",
}


def _highlight_code_html(code: str, lang: str = "") -> str:
    """
    Tokenizes `code` in a SINGLE pass over the ORIGINAL text (rather than
    several sequential find/replace passes, each blind to what an earlier
    pass already inserted) and wraps each recognized token in a colored
    <span>. A single pass is what keeps this safe: a later pass re-scanning
    HTML/CSS an earlier pass had already produced could easily re-match,
    say, the digits inside a hex color code an earlier pass inserted
    (#569cd6) and wrongly re-highlight THOSE as a "number" token.
    color/font-weight ARE part of the small CSS subset Qt's rich-text
    engine actually supports (unlike border-radius - see markdown_to_html's
    own comment on that), so this reliably renders regardless of which
    renderer (the real per-block QWidget, or the plain-QLabel
    fallback) ends up displaying it.
    """
    out = []
    pos = 0
    for m in _CODE_TOKEN_RE.finditer(code):
        if m.start() > pos:
            out.append(_md_escape(code[pos:m.start()]))
        color = _CODE_TOKEN_COLORS[m.lastgroup]
        out.append(f"<span style='color:{color};'>{_md_escape(m.group(0))}</span>")
        pos = m.end()
    if pos < len(code):
        out.append(_md_escape(code[pos:]))
    html = "".join(out)
    # Leading indentation would otherwise collapse to a single space, the
    # way HTML treats any run of whitespace - done before the \n → <br>
    # swap below, while '^' in MULTILINE mode can still anchor on real
    # newline characters.
    html = re.sub(r"(?m)^( +)", lambda mm: "&nbsp;" * len(mm.group(1)), html)
    html = html.replace("\n", "<br>")
    return f"<span style='color:#d4d4d4;'>{html}</span>"


def _bubble_plain_text(widget) -> str:
    """
    The message's ORIGINAL text (pre-markdown-rendering) - needed anywhere
    the bubble's content is read back out for something other than
    display (Copy to clipboard, Read aloud, starting an edit). For a user
    bubble (still a single QLabel), label.text() on a RichText QLabel
    would otherwise return the rendered HTML, asterisks-turned-tags and
    all. For an assistant bubble (now a QWidget container holding a mix
    of text QLabels and real code-block widgets - see populate_ai_message)
    there's no single .text() to read at all; raw_text is the only place
    the original text still exists as one piece, for either kind.
    """
    raw = widget.property("raw_text")
    if raw is not None:
        return raw
    return widget.text() if hasattr(widget, "text") else ""


def set_bubble_text(label: QLabel, text: str) -> None:
    """Single place that updates a bubble QLabel's text - renders markdown
    for display while keeping the original plain text available via
    _bubble_plain_text (see its own docstring) for Copy/Read aloud/Edit."""
    label.setProperty("raw_text", text)
    label.setText(markdown_to_html(text))


# ── Code blocks while a reply is still streaming ──────────────────────
# A fenced code block only becomes a real, properly rendered block once
# its CLOSING fence arrives (markdown_to_html only matches closed
# fences, and the final render builds actual code widgets - see
# populate_ai_message). Until then the code was streaming in as raw
# escaped text: unstyled, re-wrapping on every chunk, and jerking the
# scroll position around as it grew.
#
# Nothing is gained by watching code arrive character by character
# it's unreadable while it moves - so an open fence is shown as a
# compact "writing code" placeholder that counts the lines as they
# land, and the real block appears, all at once and properly
# highlighted, the moment the fence closes.

def _split_open_code_fence(text: str) -> "tuple[str, str, str] | None":
    """
    (text_before_the_fence, language, body_so_far) when `text` ends
    inside an unclosed ``` fence, else None.

    Detected by counting fences rather than regex-matching an unclosed
    one: an odd number of ``` markers means the last one opened a block
    nothing has closed yet, which stays true no matter what the code
    inside happens to contain.
    """
    if text.count("```") % 2 == 0:
        return None
    index = text.rfind("```")
    before = text[:index]
    rest = text[index + 3:]
    newline = rest.find("\n")
    if newline == -1:
        # Still on the opening line - the language tag may not even be
        # complete yet.
        return before, rest.strip(), ""
    return before, rest[:newline].strip(), rest[newline + 1:]


def _coding_placeholder_html(lang: str, body: str) -> str:
    """The stand-in shown in place of a code block that's still being
    written. Reports the language and how much has arrived, so it reads
    as something in progress rather than a stalled reply."""
    lines = body.count("\n") + (1 if body.strip() else 0)
    language = (lang or "").strip()
    label = f"Writing {language} code" if language else "Writing code"
    detail = f" · {lines} line{'s' if lines != 1 else ''}" if lines else ""
    return (
        "<table width='100%' cellspacing='0' cellpadding='10' "
        "style='background-color:#1e1e1e;margin:6px 0px;'><tr><td>"
        "<span style='color:#9a9a9a;font-family:\"Segoe UI\";font-size:12px;'>"
        f"&lt;/&gt;&nbsp; {_md_escape(label)}…{_md_escape(detail)}</span>"
        "</td></tr></table>"
    )


def set_streaming_bubble_text(label: QLabel, text: str) -> None:
    """
    set_bubble_text's counterpart for a reply that's still arriving:
    identical except that an unfinished code block renders as the
    placeholder above rather than as raw text.

    raw_text still holds the COMPLETE text either way, so Copy and Read
    aloud mid-stream get what was actually said, not the placeholder.
    """
    label.setProperty("raw_text", text)
    split = _split_open_code_fence(text)
    if split is None:
        label.setText(markdown_to_html(text))
        return
    before, lang, body = split
    label.setText(markdown_to_html(before) + _coding_placeholder_html(lang, body))


# ── Collapsing very long messages ────────────────────────────────────
# A pasted wall of text (a log, a whole file, a long spec) otherwise
# takes over the entire scroll area, burying every message around it
# and making the conversation unusable to scroll through. Past these
# thresholds a message renders truncated with a "Show more" toggle
# under it instead.
#
# Only the DISPLAYED html is truncated - set_bubble_text above always
# stores the complete text in the label's "raw_text" property, which is
# what Copy / Read aloud / Edit / regenerate all read (see
# _bubble_plain_text), so collapsing never risks silently sending or
# copying a shortened version of what was actually said.
MESSAGE_COLLAPSE_LINES = 14
MESSAGE_COLLAPSE_CHARS = 1200


def _message_needs_collapse(text: str) -> bool:
    return len(text) > MESSAGE_COLLAPSE_CHARS or text.count("\n") + 1 > MESSAGE_COLLAPSE_LINES


def _collapsed_preview(text: str) -> str:
    """The first MESSAGE_COLLAPSE_LINES lines (and at most
    MESSAGE_COLLAPSE_CHARS characters) of `text`, with an ellipsis line
    marking that there's more behind the toggle."""
    preview = "\n".join(text.split("\n")[:MESSAGE_COLLAPSE_LINES])
    if len(preview) > MESSAGE_COLLAPSE_CHARS:
        preview = preview[:MESSAGE_COLLAPSE_CHARS].rstrip()
    return preview + "\n…"


# The readable column every message is laid out inside.
#
# Removing the old fixed 520px half-width column was right - it left a
# big empty gutter on one side - but replacing it with "use the whole
# row" went too far the other way: on a wide window a reply runs the
# full width of the screen, which is both hard to read (far past the
# ~60-90 characters a line should carry) and lets anything unbreakable
# in it - a long URL, a Windows path, a base64 blob - push the row
# wider than the viewport and force the whole conversation to scroll
# sideways. A maximum width caps both: the column stops growing at a
# comfortable measure, and nothing inside it can widen the page,
# because the layout can't allocate a capped widget more than its
# maximum however wide its content wants to be.
MESSAGE_MAX_WIDTH = 760

# How tall a finished assistant reply may be before it's collapsed. A
# height cap rather than the text truncation used for user bubbles
# above: an assistant bubble is a CONTAINER of mixed widgets (rich-text
# labels and real code-block widgets - see populate_ai_message), so
# shortening its source text would mean re-rendering it with a code
# fence potentially cut in half. Capping the container leaves the
# widgets exactly as they are and just shows less of them.
ASSISTANT_COLLAPSED_HEIGHT = 380
_QWIDGETSIZE_MAX = 16777215  # Qt's own "no maximum" sentinel


def _row_content_layout(row: QWidget):
    """The QVBoxLayout inside a message row that holds the bubble and
    everything stacked under it (the actions row, an inline editor, a
    Show more toggle). Found by scanning rather than by index so it
    survives anything else being added to the row's own layout."""
    layout = row.layout()
    if layout is None:
        return None
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is not None and item.layout() is not None:
            return item.layout()
    return None


def _install_assistant_collapse(row: QWidget, container: QWidget, text: str, c: dict) -> None:
    """
    Caps a long finished assistant reply and puts a Show more/Show less
    toggle under it, so one very long answer doesn't bury the rest of
    the conversation.

    Only ever called once a reply is COMPLETE - on history load, and
    when a streamed or typed-out reply finishes (see
    finish_streaming_response). Never mid-stream: watching the reply
    arrive is the point of streaming, and capping it while it grows
    would make it look stuck.

    Safe to call repeatedly on the same row - a re-render of the same
    message reuses the existing toggle instead of stacking up another
    one, and a reply that's no longer long enough to need collapsing
    (an edit, a regenerate) has its cap and its toggle removed.
    """
    content = _row_content_layout(row)
    if content is None:
        return

    existing = row.findChild(QPushButton, "msgExpandBtn")

    if not _message_needs_collapse(text):
        if existing is not None:
            holder = existing.parentWidget()
            content.removeWidget(holder)
            holder.deleteLater()
        container.setMaximumHeight(_QWIDGETSIZE_MAX)
        return

    if existing is not None:
        return  # already collapsed - leave whatever state the reader put it in

    container.setMaximumHeight(ASSISTANT_COLLAPSED_HEIGHT)

    btn = QPushButton(tr("Show more"))
    btn.setObjectName("msgExpandBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(_msg_action_btn_style(c))
    btn.setProperty("expanded", False)

    def _toggle():
        expanded = not bool(btn.property("expanded"))
        btn.setProperty("expanded", expanded)
        container.setMaximumHeight(_QWIDGETSIZE_MAX if expanded else ASSISTANT_COLLAPSED_HEIGHT)
        btn.setText("Show less" if expanded else "Show more")

    btn.clicked.connect(_toggle)

    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    holder_row = QHBoxLayout(holder)
    holder_row.setContentsMargins(2, 0, 2, 0)
    holder_row.addWidget(btn)
    holder_row.addStretch()  # left-aligned, flush under the assistant bubble

    index = content.indexOf(container)
    content.insertWidget(index + 1 if index >= 0 else content.count(), holder)


def _make_show_more_button(label: QLabel, full_text: str, c: dict) -> QPushButton:
    """
    The "Show more"/"Show less" toggle that sits under a collapsed
    message. Re-renders the label between the truncated preview and the
    full text; `full_text` is captured here rather than re-read from
    the label so an edit that rewrites the bubble can't leave the
    toggle showing a stale version.
    """
    btn = QPushButton(tr("Show more"))
    btn.setObjectName("msgExpandBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(_msg_action_btn_style(c))
    btn.setProperty("expanded", False)

    def _toggle():
        expanded = not bool(btn.property("expanded"))
        btn.setProperty("expanded", expanded)
        label.setText(markdown_to_html(full_text if expanded else _collapsed_preview(full_text)))
        btn.setText("Show less" if expanded else "Show more")

    btn.clicked.connect(_toggle)
    return btn


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


def _make_ai_text_label(segment_text: str, c: dict) -> QLabel:
    """One plain-text segment of an assistant reply (the parts NOT inside
    a fenced code block) - see populate_ai_message, which is what splits
    a reply into these and the code-block widgets around them."""
    label = QLabel()
    label.setObjectName("bubbleAiText")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    label.setStyleSheet(_bubble_ai_text_style(c))
    # Same reasoning as _make_row's bubble: a wrapped label has to be
    # allowed to report the height it needs at the width it is given,
    # or the column allocates one and lays out the other and the text
    # is clipped.
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setText(markdown_to_html(segment_text.strip("\n")))
    return label


def _make_code_block_widget(code: str, lang: str, c: dict) -> QWidget:
    """
    A ChatGPT-style code block: a language label + a real, working Copy
    button in a header bar, and syntax-tinted code below, all built as
    actual QWidgets styled with a real Qt Style Sheet - NOT more embedded
    rich-text HTML.

    That distinction is the actual fix for "the [style]sheet css isn't
    working": Qt's rich-text engine (what a QLabel's setTextFormat(RichText)
    uses, and what the old all-in-one-QLabel code block relied on for its
    background/border-radius/padding) only supports a small, inconsistent
    slice of CSS - border-radius on a <pre>/<div> specifically was simply
    never going to render there, no matter how that inline style was
    worded. A real QWidget.setStyleSheet() (a proper Qt Style Sheet, the
    same mechanism every button/input/panel in this whole app already
    uses) DOES support border-radius correctly - and, just as importantly,
    lets this have an actual clickable QPushButton for Copy, which a
    QLabel can never contain (only a plain <a> link, styled to only sort
    of look like a button).
    """
    frame = QWidget()
    frame.setObjectName("codeBlock")
    frame.setStyleSheet(
        f"QWidget#codeBlock{{background-color:{c['code_bg']};"
        f"border:1px solid {c['border']};border-radius:8px;}}"
    )
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    header = QWidget()
    header.setObjectName("codeBlockHeader")
    header.setStyleSheet(
        f"QWidget#codeBlockHeader{{background-color:{c['code_header_bg']};"
        "border-top-left-radius:8px;border-top-right-radius:8px;"
        f"border-bottom:1px solid {c['border']};}}"
    )
    header_row = QHBoxLayout(header)
    header_row.setContentsMargins(14, 6, 8, 6)
    header_row.setSpacing(8)

    lang_label = QLabel(lang.strip() or "code")
    lang_label.setStyleSheet(
        f"color:{c['text_muted']};font-family:\"Segoe UI\";font-size:11.5px;"
        "background:transparent;border:none;"
    )
    header_row.addWidget(lang_label)
    header_row.addStretch(1)

    copy_btn = QPushButton(tr(" Copy"))
    copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_btn.setIcon(icons.icon("copy", c, "text_muted"))
    copy_btn.setIconSize(QSize(11, 11))
    copy_btn.setStyleSheet(
        "QPushButton{background:transparent;border:none;"
        f"color:{c['text_muted']};font-family:\"Segoe UI\";font-size:11.5px;"
        "padding:4px 8px;border-radius:5px;}"
        f"QPushButton:hover{{background-color:{c['bg_hover_strong']};color:{c['text_primary']};}}"
    )

    def _do_copy():
        QApplication.clipboard().setText(code)
        copy_btn.setText(tr(" Copied"))
        copy_btn.setIcon(icons.icon("check", c, "success"))

        def _revert():
            copy_btn.setText(tr(" Copy"))
            copy_btn.setIcon(icons.icon("copy", c, "text_muted"))

        QTimer.singleShot(1400, _revert)

    copy_btn.clicked.connect(_do_copy)
    header_row.addWidget(copy_btn)
    outer.addWidget(header)

    body = QLabel()
    body.setObjectName("codeBlockBody")
    body.setWordWrap(True)
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    body.setStyleSheet(
        f"QLabel#codeBlockBody{{background-color:{c['code_bg']};border:none;"
        "border-bottom-left-radius:8px;border-bottom-right-radius:8px;"
        "font-family:Consolas,\"Courier New\",monospace;font-size:13px;"
        "padding:12px 14px;}"
    )
    body.setText(_highlight_code_html(code, lang))
    outer.addWidget(body)

    return frame


def populate_ai_message(container: QWidget, text: str, c: dict) -> None:
    """
    (Re)builds an assistant bubble's content from scratch: plain-text
    segments become rich-text QLabels (_make_ai_text_label, same markdown
    rendering as before) and each fenced code block becomes its own real
    widget (_make_code_block_widget - see its own docstring for why code
    blocks specifically need to be real widgets, not more embedded
    rich-text HTML the way the rest of the bubble still is). Called both
    for a fully-loaded historical message and, once a streamed reply
    finishes, to upgrade it from the plain interim QLabel it streamed
    into (see ChatWidget.finish_streaming_response) to this.
    """
    container.setProperty("raw_text", text)
    layout = container.layout()
    _clear_layout(layout)

    if not text:
        layout.addWidget(_make_ai_text_label("", c))
        return

    pos = 0
    for m in _MD_CODE_FENCE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip("\n"):
            layout.addWidget(_make_ai_text_label(before, c))
        lang, code = m.group(1), m.group(2).rstrip("\n")
        layout.addWidget(_make_code_block_widget(code, lang, c))
        pos = m.end()

    tail = text[pos:]
    if tail.strip("\n") or pos == 0:
        layout.addWidget(_make_ai_text_label(tail, c))

# Bubble styles - matches exactly what's in the .ui's root styleSheet
# property (see _build_chrome_style below, which regenerates that whole
# blob from theme tokens and replaces it after loadUi so switching themes
# doesn't need editing the .ui file itself).
def _bubble_user_style(c: dict) -> str:
    # Claude.ai-style: a subtle filled pill around the user's OWN message
    # only - no border (a flat fill reads calmer than an outlined box at
    # this larger font size) - while still filling the row's full width
    # (see _make_row's own comment on why there's no more 520px cap
    # squeezing this into a narrow column).
    return (
        f"background-color:{c['bg_elevated']};"
        f"color:{c['text_primary']};"
        "font-family:\"Segoe UI\";"
        "font-size:16px;"
        "line-height:1.5;"
        "border:none;"
        "border-radius:16px;"
        "padding:14px 18px;"
    )


def _bubble_ai_style(c: dict) -> str:
    # Claude.ai-style: no bubble at all for the assistant's own replies
    # plain text spanning the full row width, exactly what claude.ai
    # itself does (only the user's side gets a visible box). Kept at the
    # same left inset the old bordered box's padding gave it, so replies
    # don't suddenly hug the row's left edge now that the border/fill are
    # gone.
    #
    # This is now the CONTAINER's own style only (background/padding)
    # an assistant bubble is a QWidget holding a mix of text QLabels and
    # real code-block widgets (see populate_ai_message), not a single
    # QLabel anymore, so the actual text color/font/line-height now live
    # on _bubble_ai_text_style below instead, applied to each text
    # segment individually.
    return (
        "background-color:transparent;"
        "border:none;"
        "padding:6px 4px;"
    )


def _bubble_ai_text_style(c: dict) -> str:
    """Style for one plain-text segment QLabel inside an assistant
    bubble's container (see populate_ai_message) - everything
    _bubble_ai_style used to carry itself back when the whole bubble was
    one QLabel, minus the padding (the container already provides that)."""
    return (
        "background-color:transparent;"
        f"color:{c['text_primary']};"
        "font-family:\"Segoe UI\";"
        "font-size:16px;"
        "line-height:1.5;"
        "border:none;"
    )


def _typing_bubble_style(c: dict) -> str:
    return (
        "background-color:transparent;"
        f"color:{c['text_muted']};"
        "font-family:\"Segoe UI\";"
        "font-size:15px;"
        f"border:1px solid {c['bg_input']};"
        "border-radius:4px 12px 12px 12px;"
        "padding:12px 16px;"
    )


def _thinking_header_style(c: dict) -> str:
    return (
        f"QPushButton{{background:{c['bg_elevated']};color:{c['text_secondary']};border:1px solid {c['bg_input']};"
        "font-family:\"Segoe UI\";font-size:12.5px;font-weight:600;text-align:left;"
        "padding:6px 12px;border-radius:10px;}"
        f"QPushButton:hover{{color:{c['text_primary']};border-color:{c['border_strong']};background:{c['bg_hover']};}}"
    )


def _thinking_body_style(c: dict) -> str:
    # Bug fix / cosmetic overhaul: this used to be a bare italic-muted
    # QLabel with only a thin 2px left rule for structure - easy to miss
    # entirely against the rest of the bubble and, per feedback, just
    # "very bad" as a piece of UI. Now a proper inset card (rounded,
    # actual background fill, real padding on all sides) so the model's
    # reasoning reads as its own distinct block rather than a stray
    # sliver of gray italic text.
    return (
        f"background-color:{c['bg_elevated']};color:{c['text_secondary']};"
        "font-family:\"Segoe UI\";font-size:13px;line-height:1.5;"
        f"border:1px solid {c['bg_input']};border-radius:10px;"
        "padding:10px 14px;margin-top:2px;"
    )


def _confirm_card_style(c: dict) -> str:
    return (
        f"background-color:{c['bg_elevated']};"
        f"border:1px solid {c['border']};"
        "border-radius:4px 12px 12px 12px;"
    )


def _confirm_title_style(c: dict) -> str:
    return (
        f"color:{c['text_primary']}; font-family:\"Segoe UI\"; font-size:13px; "
        "font-weight:600; border:none; background:transparent;"
    )


def _confirm_goal_style(c: dict) -> str:
    return (
        f"color:{c['text_secondary']}; font-family:\"Segoe UI\"; font-size:13px; "
        "border:none; background:transparent;"
    )


def _confirm_hint_style(c: dict) -> str:
    return (
        f"color:{c['text_faint']}; font-family:\"Segoe UI\"; font-size:11px; "
        "border:none; background:transparent;"
    )


def _confirm_accept_style(c: dict) -> str:
    return (
        f"QPushButton{{background-color:{c['accent']};color:{c['white']};border:none;"
        "border-radius:8px;padding:6px 16px;font-family:\"Segoe UI\";font-size:12px;font-weight:600;}"
        f"QPushButton:hover{{background-color:{c['accent_hover']};}}"
        f"QPushButton:disabled{{background-color:{c['border_strong']};color:{c['text_faint']};}}"
    )


def _confirm_cancel_style(c: dict) -> str:
    return (
        f"QPushButton{{background-color:transparent;color:{c['text_secondary']};border:1px solid {c['border_strong']};"
        "border-radius:8px;padding:6px 16px;font-family:\"Segoe UI\";font-size:12px;}"
        f"QPushButton:hover{{border-color:{c['text_disabled']};color:{c['text_primary']};}}"
        f"QPushButton:disabled{{color:{c['text_disabled']};border-color:{c['border']};}}"
    )


def _build_chrome_style(c: dict) -> str:
    """
    Regenerates the ENTIRE root styleSheet the .ui file bakes in at
    design time (see app/ui/chat_widget.ui's own styleSheet property) -
    applied on top after loadUi so switching themes doesn't need
    hand-editing that XML. chatInput has its own separate local
    styleSheet property that a parent-level override like this one can't
    reach (Qt: a widget's own directly-set stylesheet wins over a
    cascaded ancestor rule for that widget) - see _chat_input_style,
    applied directly to self.chatInput instead.
    """
    return f"""
QWidget#ChatWidget {{ background-color: {c['bg_base']}; }}
QWidget#topbar {{
    background-color: {c['bg_base']};
    border-bottom: 1px solid {c['bg_input']};
    min-height: 52px;
    max-height: 52px;
}}
QLabel#chatTitle {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 14px;
    font-weight: 600;
    background: transparent;
}}
QPushButton#btn_archive {{
    background-color: transparent;
    color: {c['text_disabled']};
    border: 1px solid transparent;
    border-radius: 7px;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    font-size: 14px;
}}
QPushButton#btn_archive:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_primary']};
    border-color: {c['bg_input']};
}}
QScrollArea#messagesScroll {{
    background-color: {c['bg_base']};
    border: none;
}}
QScrollArea#messagesScroll > QWidget > QWidget {{
    background-color: {c['bg_base']};
}}
QScrollBar:vertical {{
    background: transparent;
    border: none;
    width: 8px;
    margin: 2px 0px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background: {c['bg_hover_strong']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{ height: 0px; subcontrol-position: bottom; subcontrol-origin: margin; }}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ background: transparent; }}
QWidget#msgRowUser,
QWidget#msgRowAi {{
    background: transparent;
}}
QWidget#msgRowUser:hover,
QWidget#msgRowAi:hover {{
    background-color: {c['hover_overlay']};
}}
/* Bubble styling itself now lives entirely in _bubble_user_style /
   _bubble_ai_style / _bubble_ai_text_style (app/chat_widget.py), set
   directly on each widget instance in _make_row / populate_ai_message - both because an assistant bubble is a QWidget container now, not a
   QLabel (so a "QLabel#bubbleAi" selector here would silently no-op
   for it, not actually restyle it), and so theme-token colors/sizes only
   have to be edited in one place instead of kept in sync with a second,
   easy-to-forget copy here. */
QWidget#inputArea {{
    background-color: {c['bg_base']};
    border-top: 1px solid {c['bg_input']};
    max-height: 240px;
}}
QWidget#inputWrapper {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['bg_input']};
    border-radius: 14px;
}}
QWidget#inputWrapper:focus-within {{
    border-color: {c['border_strong']};
}}
QPlainTextEdit#chatInput::placeholder {{
    color: {c['text_disabled']};
}}
QPushButton#sendBtn {{
    background-color: {c['accent']};
    color: {c['white']};
    border: none;
    border-radius: 9px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    font-size: 16px;
    font-weight: 700;
}}
QPushButton#sendBtn:hover {{
    background-color: {c['accent_hover']};
}}
QPushButton#sendBtn:pressed {{
    background-color: {c['accent_pressed']};
}}
QPushButton#micBtn {{
    background-color: transparent;
    color: {c['text_muted']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 9px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    font-size: 15px;
}}
QPushButton#micBtn:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_primary']};
    border-color: {c['border_strong']};
}}
QPushButton#micBtn:checked {{
    background-color: rgba({c['danger_rgb']},0.15);
    color: {c['danger']};
    border-color: rgba({c['danger_rgb']},0.4);
}}
QPushButton#tool_join,
QPushButton#tool_no_code,
QPushButton#tool_flow {{
    background-color: transparent;
    color: {c['text_disabled']};
    font-family: "Segoe UI";
    font-size: 11px;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
}}
QPushButton#tool_join:hover,
QPushButton#tool_no_code:hover,
QPushButton#tool_flow:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_muted']};
    border-color: {c['bg_input']};
}}
QLabel#inputHint {{
    color: {c['text_disabled']};
    font-family: "Segoe UI";
    font-size: 10px;
    background: transparent;
}}
"""


def _msg_action_btn_style(c: dict) -> str:
    return f"""
QPushButton {{
    background-color: transparent;
    color: {c['text_disabled']};
    border: none;
    border-radius: 5px;
    font-size: 11px;
    padding: 3px 6px;
}}
QPushButton:hover {{
    background-color: {c['bg_hover']};
    color: {c['text_primary']};
}}
QPushButton:pressed {{
    background-color: {c['bg_hover_strong']};
    color: {c['text_primary']};
}}
"""


def _edit_textedit_style(c: dict) -> str:
    return (
        f"background-color:{c['bg_input']}; color:{c['text_primary']}; "
        f"border:1px solid {c['accent']}; border-radius:8px; padding:8px; "
        "font-family:\"Segoe UI\"; font-size:13px;"
    )


def _edit_cancel_style(c: dict) -> str:
    return (
        f"QPushButton{{background-color:transparent;color:{c['text_secondary']};border:1px solid {c['border_strong']};"
        "border-radius:7px;padding:5px 14px;font-family:\"Segoe UI\";font-size:12px;}"
        f"QPushButton:hover{{border-color:{c['text_disabled']};color:{c['text_primary']};}}"
    )


def _edit_save_style(c: dict) -> str:
    return (
        f"QPushButton{{background-color:{c['accent']};color:{c['white']};border:none;"
        "border-radius:7px;padding:5px 14px;font-family:\"Segoe UI\";font-size:12px;font-weight:600;}"
        f"QPushButton:hover{{background-color:{c['accent_hover']};}}"
    )


def _chat_input_style(c: dict) -> str:
    return (
        f"background:transparent;color:{c['text_primary']};font-family:\"Segoe UI\";"
        f"font-size:15px;border:none;padding:0px;selection-background-color:rgba({c['accent_rgb']},0.35);"
    )


def _screenshot_caption_style(c: dict) -> str:
    return (
        f"color:{c['text_faint']}; font-family:\"Segoe UI\"; font-size:11px; "
        "border:none; background:transparent;"
    )


def _attachment_chip_style(c: dict) -> str:
    return (
        f"QWidget#attachmentChip{{background-color:{c['bg_input']};border:1px solid {c['text_disabled']};"
        "border-radius:8px;}"
    )


def _attachment_chip_label_style(c: dict) -> str:
    return (
        f"color:{c['text_secondary']}; font-family:\"Segoe UI\"; font-size:11px; "
        "border:none; background:transparent;"
    )


def _attachment_chip_remove_style(c: dict) -> str:
    return (
        f"QPushButton{{color:{c['text_muted']};border:none;background:transparent;font-size:13px;font-weight:bold;}}"
        f"QPushButton:hover{{color:{c['text_primary']};}}"
    )


def _attachment_error_style(c: dict) -> str:
    return (
        f"color:{c['danger_soft']}; font-family:\"Segoe UI\"; font-size:11px; "
        "border:none; background:transparent;"
    )


def _document_pixel_height(edit) -> float:
    """
    The laid-out height of a text widget's content, IN PIXELS, for both
    QTextEdit and QPlainTextEdit.

    Not `document().size().height()`, which is the obvious call and is
    wrong for one of them: a QPlainTextEdit uses QPlainTextDocumentLayout,
    whose documentSize() reports its height as a NUMBER OF LINES, while
    QTextEdit's QTextDocumentLayout reports pixels. Measuring the
    composer (a QPlainTextEdit) that way produced "3" for a three-line
    message - which, once margins were added, still landed under the
    24px floor, so the box never grew past one line no matter what was
    typed into it, while the wrapper around it sized itself off
    something else entirely. That mismatch is exactly the "outer box is
    huge, inner box stays one line" bug.

    Two measurements are taken and the LARGER is used, because each one
    alone has a case where it reads low:

      - line count × line spacing. For a plain-text layout the line
        count is exactly what documentSize().height() reports, wrapped
        lines included, and it's computed eagerly - so this is the
        measurement that's reliable at the moment this is called, which
        is from textChanged, i.e. BEFORE the widget has re-laid-out or
        repainted for the text that just arrived.
      - the sum of each block's bounding rect, which is real pixels for
        either layout and exact for mixed line heights - but on a
        plain-text layout it reads back CACHED geometry, so on that
        same pre-layout call it can come back as zeros.

    Taking the max means a stale or not-yet-computed value can only
    ever be ignored, never undersize the box. That matters because
    undersizing here isn't cosmetic: clamped against the caller's
    minimum, it's what pinned the composer at one line forever.
    """
    doc = edit.document()
    layout = doc.documentLayout()
    metrics = QFontMetrics(doc.defaultFont())

    # Which semantic documentSize() carries is decided from the WIDGET
    # type, not the layout class: QPlainTextDocumentLayout is declared
    # in Qt's qplaintextedit.h and therefore lives in QtWidgets, not
    # QtGui - importing it from the wrong one is an ImportError at
    # startup that takes the whole app down, which is exactly what it
    # did. QPlainTextEdit is the widget this module already deals in,
    # and a QPlainTextEdit always has that layout.
    if isinstance(edit, QPlainTextEdit):
        # NOT pixels - see this function's docstring. This is the line
        # count, which is why it has to be multiplied out rather than
        # used as a height the way QTextEdit's own layout allows.
        lines = max(1.0, float(layout.documentSize().height()))
        pixels = lines * metrics.lineSpacing()
    else:
        pixels = float(layout.documentSize().height())

    block_total = 0.0
    block = doc.begin()
    while block.isValid():
        block_total += layout.blockBoundingRect(block).height()
        block = block.next()

    pixels = max(pixels, block_total)
    if pixels <= 0:
        pixels = float(metrics.lineSpacing())  # empty document
    return pixels + doc.documentMargin() * 2


def _autosize_text_edit(edit, min_height: int = 40, max_height: int = 200) -> None:
    """
    Generic grow-with-content resize for any QTextEdit/QPlainTextEdit,
    shared by the main composer (ChatWidget._autosize_chat_input, which
    additionally re-measures on width changes since it's a persistent
    widget that gets resized) and the inline "edit this message" box
    that opens indented under a message when you click Edit (see
    _make_row below) - the latter used to sit at a hard
    setFixedHeight(72) no matter how much was typed into it, the exact
    same "box doesn't grow" bug the composer had, just on a second input
    box that was missed the first time around.
    """
    doc = edit.document()
    viewport_width = edit.viewport().width()
    if viewport_width > 0:
        doc.setTextWidth(viewport_width)
    margins = edit.contentsMargins()
    frame = edit.frameWidth() * 2
    content_height = _document_pixel_height(edit) + margins.top() + margins.bottom() + frame + 8
    new_height = max(min_height, min(int(content_height), max_height))
    if edit.height() != new_height:
        edit.setFixedHeight(new_height)
    at_cap = content_height > max_height
    edit.setVerticalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded if at_cap else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def _make_row(text: str, role: str, theme_name: str = "dark", message_id: int | None = None) -> QWidget:
    """
    Dynamically recreates the .ui's row structure:

    User  →  [spacer | QVBoxLayout > QLabel#bubbleUser + actions ]
    AI    →  [ QVBoxLayout > QLabel#bubbleAi + actions | spacer  ]

    message_id is the DB row id for a user message (None for a not-yet-
    persisted or assistant message) - stashed as a Qt property so the
    Edit button knows what to delete-from without needing a wider
    refactor to thread ids through every assistant call site too (Copy
    and Read aloud only need the bubble's live text, never an id).

    A user row also carries a hidden inline editor (editContainer,
    editTextEdit + Save/Cancel) that ChatWidget._start_edit/_confirm_edit/
    _cancel_edit toggle in place of the label - see those methods for the
    ChatGPT-style edit flow (edit the bubble itself, not the compose box;
    hide what follows until confirmed; Cancel reverts with nothing
    changed).
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowUser" if role == "user" else "msgRowAi")
    row.setStyleSheet("background: transparent;")
    if message_id is not None:
        row.setProperty("message_id", message_id)

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    if role == "user":
        label = QLabel()
        label.setWordWrap(True)
        label.setObjectName("bubbleUser")
        label.setStyleSheet(_bubble_user_style(c))
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        set_bubble_text(label, text)
        # raw_text (the full message) is already stored by the call
        # above - only what's DISPLAYED gets shortened here, so Copy,
        # Edit and everything else downstream still see all of it.
        if _message_needs_collapse(text):
            label.setText(markdown_to_html(_collapsed_preview(text)))
    else:
        # An assistant bubble is a QWidget container, not a single QLabel
        # - see populate_ai_message's own docstring for why (real,
        # working code-block widgets need to sit alongside plain-text
        # QLabels, not be squeezed into one).
        label = QWidget()
        label.setObjectName("bubbleAi")
        label.setStyleSheet(_bubble_ai_style(c))
        ai_vbox = QVBoxLayout(label)
        ai_vbox.setContentsMargins(0, 0, 0, 0)
        ai_vbox.setSpacing(10)
        populate_ai_message(label, text, c)
    # Expanding, but bounded - see MESSAGE_MAX_WIDTH. Expanding alone
    # (what this was) let a reply run the full width of any window and
    # let one unbreakable token drag the row past the viewport into a
    # sideways scroll.
    # heightForWidth matters as much as the width itself: without it the
    # column hands the bubble a height measured at one width and a
    # geometry at another, and a word-wrapped QLabel given too little
    # height clips rather than scrolls. That mismatch is what cut
    # messages off halfway through.
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setMaximumWidth(MESSAGE_MAX_WIDTH)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(4)
    # The user's own message hugs the right edge of the column, the
    # assistant's the left: the usual chat reading order.
    #
    # Done with a stretch rather than setAlignment, which looks like the
    # obvious way and is a trap. ANY horizontal alignment flag makes Qt
    # clamp the item's width to its sizeHint, and a word-wrapped
    # QLabel's sizeHint is a roughly square golden-ratio box, not the
    # column width. So the bubble silently ignored MESSAGE_MAX_WIDTH,
    # got narrower the longer the message was, and was then laid out
    # with the height it would have needed at the full width. A stretch
    # positions it without touching its width.
    align_row = QHBoxLayout()
    align_row.setContentsMargins(0, 0, 0, 0)
    align_row.setSpacing(0)
    if role == "user":
        align_row.addStretch(1)
        align_row.addWidget(label)
    else:
        align_row.addWidget(label)
        align_row.addStretch(1)
    content_vbox.addLayout(align_row)

    if role == "assistant" and text and _message_needs_collapse(text):
        # A reply loaded from history is already complete, so it can be
        # capped right away. A reply that's about to stream in arrives
        # here as "" and is collapsed later, once it finishes (see
        # finish_streaming_response) - never while it's still growing.
        container_ref = label
        collapse_holder = QWidget()
        collapse_holder.setStyleSheet("background: transparent;")
        collapse_row = QHBoxLayout(collapse_holder)
        collapse_row.setContentsMargins(2, 0, 2, 0)
        collapse_btn = QPushButton(tr("Show more"))
        collapse_btn.setObjectName("msgExpandBtn")
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.setStyleSheet(_msg_action_btn_style(c))
        collapse_btn.setProperty("expanded", False)

        def _toggle_assistant(btn=collapse_btn, bubble=container_ref):
            expanded = not bool(btn.property("expanded"))
            btn.setProperty("expanded", expanded)
            bubble.setMaximumHeight(_QWIDGETSIZE_MAX if expanded else ASSISTANT_COLLAPSED_HEIGHT)
            btn.setText("Show less" if expanded else "Show more")

        collapse_btn.clicked.connect(_toggle_assistant)
        collapse_row.addWidget(collapse_btn)
        collapse_row.addStretch()
        label.setMaximumHeight(ASSISTANT_COLLAPSED_HEIGHT)
        content_vbox.addWidget(collapse_holder)

    if role == "user" and _message_needs_collapse(text):
        show_more_row = QHBoxLayout()
        show_more_row.setContentsMargins(2, 0, 2, 0)
        show_more_row.addStretch()  # right-aligned, flush under the user bubble
        show_more_row.addWidget(_make_show_more_button(label, text, c))
        content_vbox.addLayout(show_more_row)

    if role == "user":
        edit_container = QWidget()
        edit_container.setObjectName("editContainer")
        edit_container.setVisible(False)
        edit_vbox = QVBoxLayout(edit_container)
        edit_vbox.setContentsMargins(0, 0, 0, 0)
        edit_vbox.setSpacing(6)

        edit_input = QTextEdit()
        edit_input.setObjectName("editTextEdit")
        edit_input.setStyleSheet(_edit_textedit_style(c))
        edit_input.setAcceptRichText(False)
        # Bug fix: used to be setFixedHeight(72) - a hard cap regardless
        # of how much text was in it, so anything past ~3 lines just
        # scrolled invisibly inside the same tiny box instead of growing,
        # same bug (and same fix) as the main composer at the bottom of
        # the window - see _autosize_text_edit's own docstring.
        edit_input.setMinimumHeight(40)
        edit_input.textChanged.connect(lambda: _autosize_text_edit(edit_input))
        edit_vbox.addWidget(edit_input)

        edit_btn_row = QHBoxLayout()
        edit_btn_row.setSpacing(6)
        edit_btn_row.addStretch()
        btn_cancel_edit = QPushButton(tr("Cancel"))
        btn_cancel_edit.setObjectName("editCancelBtn")
        btn_cancel_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel_edit.setStyleSheet(_edit_cancel_style(c))
        btn_save_edit = QPushButton(tr("Update"))
        btn_save_edit.setObjectName("editSaveBtn")
        btn_save_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_edit.setStyleSheet(_edit_save_style(c))
        edit_btn_row.addWidget(btn_cancel_edit)
        edit_btn_row.addWidget(btn_save_edit)
        edit_vbox.addLayout(edit_btn_row)

        content_vbox.addWidget(edit_container)

    actions_container = QWidget()
    actions_container.setObjectName("msgActionsRow")
    actions_row = QHBoxLayout(actions_container)
    actions_row.setSpacing(2)
    actions_row.setContentsMargins(2, 0, 2, 0)
    btn_style = _msg_action_btn_style(c)

    btn_copy = QPushButton(tr(" Copy"))
    btn_copy.setIcon(icons.icon("copy", c, "text_disabled"))
    btn_copy.setObjectName("msgCopyBtn")
    btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_copy.setStyleSheet(btn_style)

    if role == "assistant":
        btn_speak = QPushButton(tr(" Read aloud"))
        btn_speak.setIcon(icons.icon("speaker", c, "text_disabled"))
        btn_speak.setObjectName("msgSpeakBtn")
        btn_speak.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_speak.setStyleSheet(btn_style)

        btn_regenerate = QPushButton(tr(" Regenerate"))
        btn_regenerate.setIcon(icons.icon("refresh", c, "text_disabled"))
        btn_regenerate.setObjectName("msgRegenerateBtn")
        btn_regenerate.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_regenerate.setStyleSheet(btn_style)
        # Enabled/wired by _wire_message_actions (is_leaf) or, for a
        # reply that just finished streaming in THIS session, by
        # attach_message_id - see both docstrings. Starts disabled:
        # correct for every row except the one that turns out to be
        # the chat's actual last message, which gets flipped on right
        # after.
        btn_regenerate.setEnabled(False)
    else:
        btn_edit = QPushButton(tr(" Edit"))
        btn_edit.setIcon(icons.icon("edit", c, "text_disabled"))
        btn_edit.setObjectName("msgEditBtn")
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.setStyleSheet(btn_style)

    if role == "user":
        # Right-aligned, flush under the bubble above (which is also
        # right-aligned) - stretch first, buttons last.
        actions_row.addStretch()
        actions_row.addWidget(btn_copy)
        actions_row.addWidget(btn_edit)
    else:
        # Left-aligned, flush under the AI bubble.
        actions_row.addWidget(btn_copy)
        actions_row.addWidget(btn_speak)
        actions_row.addWidget(btn_regenerate)
        actions_row.addStretch()
    content_vbox.addWidget(actions_container)

    # Bug fix: both roles used to be squeezed into a fixed ~520px column
    # against one edge of the row via a stretchy QSpacerItem on the other
    # side (see this row's own history/_bubble_*_style docstrings) - on
    # anything wider than a fairly narrow window that left a large empty
    # gutter on one side and forced either a cramped window or tiny text
    # to actually use the space. content_vbox (and the label inside it,
    # set to Expanding above) now takes the row's FULL width - no spacer
    # squeezing it to one side - same as claude.ai's own message column,
    # which spans the full chat width for both the user and the model.
    h.addLayout(content_vbox, 1)

    return row


# Message content for a screenshot captured via the global shortcut is
# stored as this marker instead of plain text - [[SCREENSHOT:<path>]] - so
# _append_bubble can tell it apart from a normal message on both a live
# capture and a later chat reload, and render the actual image either way.
SCREENSHOT_MARKER_RE = re.compile(r"^\[\[SCREENSHOT:(.+)\]\]$")

# Same idea, for a command the AI actually ran via ai.actions.tool_run_command
# sharing its real output in the chat - [[TERMINAL:<base64 JSON>]]. The
# payload (command/output/success) is JSON, base64-encoded rather than
# embedded as raw text the way SCREENSHOT_MARKER_RE embeds a bare path:
# command output can itself contain newlines, "]]", or anything else a
# plain single-line/no-brackets marker can't safely round-trip, where a
# filesystem path never does. See encode_terminal_marker/decode_terminal_marker.
TERMINAL_MARKER_RE = re.compile(r"^\[\[TERMINAL:([A-Za-z0-9+/=]+)\]\]$")


def encode_terminal_marker(command: str, output: str, success: bool) -> str:
    """
    Builds the [[TERMINAL:...]] marker main_window.py's
    _on_terminal_output_captured stores as the message content (both live
    and on reload) for a command the AI ran via tool_run_command. See
    TERMINAL_MARKER_RE's comment for why the payload is base64 JSON
    rather than embedded as plain text.
    """
    payload = json.dumps({"command": command, "output": output, "success": success})
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return f"[[TERMINAL:{encoded}]]"


def decode_terminal_marker(encoded: str) -> dict:
    """
    Inverse of encode_terminal_marker - returns {"command", "output",
    "success"}. Callers should expect this to raise on a corrupted/
    truncated marker (shouldn't happen from any real call site, but a
    hand-edited or partially-migrated DB row is always possible) and
    fall back to a plain-text rendering rather than crash the chat.
    """
    payload = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    return json.loads(payload)

# Same idea again, for a file the AI wrote to disk (tool_generate_file /
# tool_merge_files_to_pdf) - [[FILE:<base64 JSON>]], rendered as a card
# with a "Show in folder" button. Base64 JSON for the same reason as the
# terminal marker: a Windows path can contain "]" and other characters a
# bare "[[FILE:<path>]]" regex couldn't safely round-trip.
FILE_MARKER_RE = re.compile(r"^\[\[FILE:([A-Za-z0-9+/=]+)\]\]$")


def encode_file_marker(path: str, action: str = "created") -> str:
    """Builds the [[FILE:...]] marker MainWindow._on_file_output_captured
    stores as the message content (live and on reload) for a file the AI wrote."""
    payload = json.dumps({"path": path, "action": action})
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return f"[[FILE:{encoded}]]"


def decode_file_marker(encoded: str) -> dict:
    """Inverse of encode_file_marker - returns {"path", "action"}. Raises
    on a corrupted marker; callers fall back to plain text (see _append_bubble)."""
    payload = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    return json.loads(payload)


# Same idea, for lightweight system/status notices (e.g. "automation
# stopped via the global shortcut") - [[SYSTEM:<icon>|<text>]]. These are
# NOT a normal chat message from either side: no avatar-side alignment, no
# Copy/Read-aloud/Edit actions (which would be nonsensical on a status
# line), just a small centered pill, closer to how Discord/Slack render a
# system event than a conversational message. `icon` is one of
# app.icons._ICON_MAP's keys.
SYSTEM_MARKER_RE = re.compile(r"^\[\[SYSTEM:([a-z0-9_-]+)\|(.+)\]\]$", re.DOTALL)

# Appended to the PROMPT (never to the displayed/saved bubble) when a
# message came from voice mode - see ChatWidget._send_message's
# voice_reply param and MainWindow._on_voice_transcript_ready. Keeps
# spoken replies short and speech-friendly without needing a second,
# separate chat session with its own system prompt just for voice.
VOICE_REPLY_HINT = (
    "\n\n[This message was spoken aloud and your reply will be read aloud "
    "too - reply in plain spoken language: no markdown, asterisks, or "
    "emoji, numbers spelled out, and keep it concise (2-4 sentences) "
    "unless more detail is clearly needed.]"
)


def _make_system_row(icon_name: str, text: str, theme_name: str = "dark") -> QWidget:
    """
    A small, centered, muted status pill - for events like "automation
    stopped via the global shortcut" that aren't really a message from
    either side of the conversation and shouldn't look like one (no
    bubble background/alignment, no Copy/Read-aloud/Edit actions).
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowSystem")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 4, 28, 4)

    pill = QWidget()
    pill.setStyleSheet(
        f"background-color: {c['bg_elevated']}; border: 1px solid {c['border']}; border-radius: 10px;"
    )
    pill_layout = QHBoxLayout(pill)
    pill_layout.setContentsMargins(10, 4, 12, 4)
    pill_layout.setSpacing(6)

    icon_lbl = QLabel()
    icon_lbl.setPixmap(icons.pixmap(icon_name, c, 11, "text_faint"))
    icon_lbl.setStyleSheet("background: transparent; border: none;")
    pill_layout.addWidget(icon_lbl)

    text_lbl = QLabel(text)
    text_lbl.setStyleSheet(
        f"color: {c['text_faint']}; font-family: \"Segoe UI\"; font-size: 11px; background: transparent; border: none;"
    )
    pill_layout.addWidget(text_lbl)

    h.addStretch()
    h.addWidget(pill)
    h.addStretch()

    return row


def _make_screenshot_row(path: str, theme_name: str = "dark") -> QWidget:
    """
    Chat bubble for a screenshot captured via the global shortcut - same
    row shape as a normal assistant bubble, but showing a scaled
    thumbnail instead of text. Falls back to a plain notice if the file
    is missing (moved/deleted since capture).
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(6)

    pixmap = QPixmap(path)
    if pixmap.isNull():
        row_box = QHBoxLayout()
        row_box.setSpacing(6)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(icons.pixmap("camera", c, 13, "text_faint"))
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        row_box.addWidget(icon_lbl)
        label = QLabel(tr("Screenshot (file no longer available)"))
        label.setWordWrap(True)
        label.setObjectName("bubbleAi")
        label.setStyleSheet(_bubble_ai_style(c))
        row_box.addWidget(label)
        content_vbox.addLayout(row_box)
    else:
        thumb = pixmap.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        img_label = QLabel()
        img_label.setObjectName("screenshotThumb")
        img_label.setPixmap(thumb)
        img_label.setStyleSheet(
            f"border:1px solid {c['bg_input']}; border-radius:8px; background:transparent;"
        )
        content_vbox.addWidget(img_label)

        caption_box = QHBoxLayout()
        caption_box.setSpacing(6)
        caption_icon = QLabel()
        caption_icon.setPixmap(icons.pixmap("camera", c, 11, "text_faint"))
        caption_icon.setStyleSheet("background: transparent; border: none;")
        caption_box.addWidget(caption_icon)
        caption = QLabel(tr("Screenshot captured - attached to your next message."))
        caption.setObjectName("screenshotCaption")
        caption.setWordWrap(True)
        caption.setStyleSheet(_screenshot_caption_style(c))
        caption_box.addWidget(caption, stretch=1)
        content_vbox.addLayout(caption_box)

    spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    h.addLayout(content_vbox)
    h.addSpacerItem(spacer)

    return row


_TERMINAL_BODY_MIN_HEIGHT = 34  # one line of output at rest, not a much taller empty-looking box
_TERMINAL_BODY_MAX_HEIGHT = 260  # past this it scrolls internally - see tool_run_command's own 4000-char cap


def _make_terminal_row(command: str, output: str, success: bool, theme_name: str = "dark") -> QWidget:
    """
    Chat bubble for a command the AI ran via ai.actions.tool_run_command,
    showing the actual command + its real combined stdout/stderr - a
    "kinda terminal env" right inside the chat, rather than the human
    only ever seeing whatever the model chose to paraphrase about it
    afterward. Deliberately its own fixed dark/monospace look - same
    JetBrains Mono + near-black palette as app/console_widget.py's
    standalone developer console - regardless of the app's current
    light/dark theme, for the same reason that console doesn't re-theme
    itself: a terminal keeping a consistent scheme is the familiar,
    expected look, not something that should shift with app chrome.

    No output body at all when the command produced none (e.g. a
    silent `mkdir` or `cd`) - a QTextEdit reading "(no output)" was
    just noise for the common no-output case; the header (command +
    succeeded/failed) already says everything there is to say. When
    there IS output, the body is sized to the ACTUAL output
    (_autosize_text_edit, same content-driven grow-to-fit every other
    QTextEdit in this file uses) instead of always claiming a large,
    mostly-empty box - QTextEdit's own default sizeHint has nothing to
    do with its text length, so without this a one-line "ok" printed
    the same oversized box as 200 lines of real output.
    """
    c = theme.colors(theme_name)
    has_output = bool(output.strip())

    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(0)

    frame = QWidget()
    frame.setObjectName("terminalFrame")
    frame.setStyleSheet(
        "QWidget#terminalFrame {"
        " background-color: #111318;"
        " border: 1px solid #262b33;"
        " border-radius: 12px;"
        "}"
    )
    frame_layout = QVBoxLayout(frame)
    frame_layout.setContentsMargins(0, 0, 0, 0)
    frame_layout.setSpacing(0)

    # Header: a terminal icon, the command itself (as typed/run - a
    # human should be able to see exactly what ran, not a summary of
    # it), and a succeeded/failed badge on the right. Only the top
    # corners are rounded when a body follows; all four are when this
    # is the whole card (no output).
    header = QWidget()
    header.setObjectName("terminalHeader")
    header_radius = (
        "border-top-left-radius: 12px; border-top-right-radius: 12px;"
        if has_output else
        "border-radius: 12px;"
    )
    header.setStyleSheet(
        "QWidget#terminalHeader {"
        " background-color: #171a20;"
        f" {'border-bottom: 1px solid #262b33;' if has_output else ''}"
        f" {header_radius}"
        "}"
    )
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(14, 10, 12, 10)
    header_layout.setSpacing(10)

    term_icon = QLabel()
    term_icon.setPixmap(icons.pixmap("terminal", c, 12, "text_faint"))
    term_icon.setStyleSheet("background: transparent; border: none;")
    header_layout.addWidget(term_icon)

    cmd_label = QLabel(command)
    cmd_label.setWordWrap(True)
    cmd_label.setStyleSheet(
        'color: #e8e8ea; font-family: "JetBrains Mono", "Consolas", "Courier New"; '
        "font-size: 12px; font-weight: 500; background: transparent; border: none;"
    )
    header_layout.addWidget(cmd_label, 1)

    # A small filled badge rather than plain colored text - reads as a
    # status chip (same idea as a CI pass/fail badge) instead of just
    # tinted words sitting loose in the header.
    status_badge = QLabel("Succeeded" if success else "Failed")
    if success:
        badge_bg, badge_fg = "rgba(95,217,122,0.14)", "#5fd97a"
    else:
        badge_bg, badge_fg = "rgba(255,107,107,0.14)", "#ff6b6b"
    status_badge.setStyleSheet(
        f"background-color: {badge_bg}; color: {badge_fg};"
        ' font-family: "Segoe UI"; font-size: 10px; font-weight: 700;'
        " border: none; border-radius: 8px; padding: 3px 9px;"
    )
    header_layout.addWidget(status_badge)

    frame_layout.addWidget(header)

    if has_output:
        # Body: the actual output. QTextEdit rather than a plain wrapped
        # QLabel - command output can be long enough (up to 4000 chars,
        # see tool_run_command's own cap) that it needs its own internal
        # scroll instead of stretching the whole chat bubble to match
        # it, and a read-only QTextEdit still keeps text
        # selectable/copyable, which a rendered image never would.
        body = QTextEdit()
        body.setObjectName("terminalBody")
        body.setReadOnly(True)
        body.setPlainText(output)
        body_font = QFont("JetBrains Mono")
        body_font.setStyleHint(QFont.StyleHint.Monospace)
        body_font.setPointSize(10)
        body.setFont(body_font)
        body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        body.setStyleSheet(
            "QTextEdit#terminalBody {"
            " background-color: #0b0d11;"
            " color: #cfd3da;"
            " border: none;"
            " border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
            " padding: 10px 14px;"
            " selection-background-color: rgba(124,106,255,0.35);"
            "}"
        )
        install_smooth_scroll(body)
        frame_layout.addWidget(body)
        # Sized to fit AFTER it has real geometry - same reason the
        # inline "edit this message" box (_autosize_text_edit's other
        # caller) defers its own initial sizing this way: viewport
        # width is 0 until this row has actually been laid out inside
        # messagesScroll, so measuring now would just clamp to the
        # floor every time.
        QTimer.singleShot(
            0, lambda: _autosize_text_edit(body, _TERMINAL_BODY_MIN_HEIGHT, _TERMINAL_BODY_MAX_HEIGHT)
        )

    # Bounded like every message bubble (see MESSAGE_MAX_WIDTH): a
    # command's output is monospace and often long, so without this it
    # is the single most likely thing in the chat to stretch a row past
    # the viewport.
    frame.setMaximumWidth(MESSAGE_MAX_WIDTH)
    content_vbox.addWidget(frame)

    spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    h.addLayout(content_vbox, 1)
    h.addSpacerItem(spacer)

    return row


_FILE_ACTION_LABELS = {
    "created": "File created",
    "merged": "Merged PDF created",
}


def _make_file_row(path: str, action: str, theme_name: str = "dark") -> QWidget:
    """
    Chat card for a file the AI just wrote to disk: file icon, name, the
    folder it's in, and a "Show in folder" button that opens File
    Explorer on it (file pre-selected - see app.file_reveal).

    The button is looked up by object name ("fileRevealBtn") so tests and
    callers can find it without holding a reference. If neither the file
    nor its folder exists by the time it's clicked (deleted/moved since),
    the button says so instead of silently doing nothing.
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    card = QWidget()
    card.setObjectName("fileCard")
    card.setStyleSheet(
        f"QWidget#fileCard {{background-color:{c['bg_elevated']};"
        f"border:1px solid {c['border']}; border-radius:12px;}}"
    )
    card_layout = QHBoxLayout(card)
    card_layout.setContentsMargins(14, 12, 14, 12)
    card_layout.setSpacing(12)

    icon_lbl = QLabel()
    icon_lbl.setPixmap(icons.pixmap("file", c, 20, "accent"))
    icon_lbl.setStyleSheet("background: transparent; border: none;")
    icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
    card_layout.addWidget(icon_lbl)

    text_box = QVBoxLayout()
    text_box.setSpacing(2)

    name_lbl = QLabel(os.path.basename(path) or path)
    name_lbl.setObjectName("fileCardName")
    name_lbl.setWordWrap(True)
    name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    name_lbl.setStyleSheet(_confirm_title_style(c))
    text_box.addWidget(name_lbl)

    status = _FILE_ACTION_LABELS.get(action, (action or "created").capitalize())
    folder = os.path.dirname(path)
    detail_lbl = QLabel(f"{status} in {folder}" if folder else status)
    detail_lbl.setObjectName("fileCardDetail")
    detail_lbl.setWordWrap(True)
    detail_lbl.setToolTip(path)
    detail_lbl.setStyleSheet(_confirm_hint_style(c))
    text_box.addWidget(detail_lbl)

    card_layout.addLayout(text_box, 1)

    btn = QPushButton(tr("Show in folder"))
    btn.setObjectName("fileRevealBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(icons.icon("folder-open", c, "white"))
    btn.setStyleSheet(_confirm_accept_style(c))
    btn.setToolTip(f"Open File Explorer at {path}")

    def _on_clicked():
        if not reveal_in_explorer(path):
            btn.setText(tr("Folder not found"))
            btn.setEnabled(False)

    btn.clicked.connect(_on_clicked)
    card_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

    h.addWidget(card, 1)
    h.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

    return row


TYPING_DOTS = "  ● ● ●  "  # the old text-only indicator; still the default "no status text" value


class LogoTypingIndicator(QLabel):
    """
    The "Kestrel is working" indicator: the app's own logo assembling
    itself, over and over, in place of the three static dots that used
    to sit here.

    Each cycle draws the logo faintly (the "not built yet" ghost) and
    wipes the solid version up over it, then settles with a short
    breathing pulse before starting again - so it reads as the mark
    being rebuilt rather than just a blinking image.

    Every frame is pre-rendered once at construction into a plain list
    of QPixmaps, and the timer only swaps which one is shown. That
    matters because this widget exists exactly while the app is busy
    waiting on a model: scaling and recompositing a 1000px PNG 14
    times a second on the UI thread would be competing with the very
    reply it's animating for.
    """

    FRAME_MS = 70
    BUILD_FRAMES = 12   # the wipe-up "assembling" pass
    PULSE_FRAMES = 6    # the settle/breathe pass before it loops
    GHOST_OPACITY = 0.16

    # Accent-purple mark: reads correctly against both the dark and the
    # light theme's message background, so this doesn't need a
    # per-theme asset. Ordered - first one that exists on disk wins.
    _LOGO_CANDIDATES = (
        "Purple-transparent-1000.png",
        "BlackPurple-transparent-1000.png",
        "White-transparent-1000.png",
        "icon.png",
    )

    def __init__(self, theme_name: str = "dark", size: int = 26, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent; border: none;")
        self._frames = self._build_frames()
        self._index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._advance)
        if self._frames:
            self.setPixmap(self._frames[0])
            self._timer.start()

    # ── Frame construction ───────────────────────────────────────────

    def _source_pixmap(self) -> "QPixmap | None":
        for name in self._LOGO_CANDIDATES:
            path = os.path.join(BASE_PATH, "assets", "icon", name)
            if not os.path.isfile(path):
                continue
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                return pixmap
        return None

    def _build_frames(self) -> list:
        source = self._source_pixmap()
        if source is None:
            return []  # no logo on disk - caller falls back to the dots
        base = source.scaled(
            self._size, self._size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        frames = []
        for i in range(self.BUILD_FRAMES):
            progress = (i + 1) / self.BUILD_FRAMES
            frames.append(self._compose(base, revealed=progress, scale=1.0))
        for i in range(self.PULSE_FRAMES):
            # Single smooth in-and-out breath: 0 → 1 → 0 over the pass.
            phase = i / max(1, self.PULSE_FRAMES - 1)
            breath = 1.0 - abs(phase * 2.0 - 1.0)
            frames.append(self._compose(base, revealed=1.0, scale=1.0 - 0.10 * breath))
        return frames

    def _compose(self, base: QPixmap, revealed: float, scale: float) -> QPixmap:
        """One frame: the full logo at GHOST_OPACITY, with the bottom
        `revealed` fraction of the solid logo painted over it, the whole
        thing drawn at `scale` inside a fixed-size transparent canvas
        (fixed so the row never jitters as the animation breathes)."""
        canvas = QPixmap(self._size, self._size)
        canvas.fill(Qt.GlobalColor.transparent)

        drawn = base
        if scale != 1.0:
            target = max(1, int(self._size * scale))
            drawn = base.scaled(
                target, target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        x = (self._size - drawn.width()) // 2
        y = (self._size - drawn.height()) // 2

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(self.GHOST_OPACITY)
        painter.drawPixmap(x, y, drawn)
        if revealed > 0:
            painter.setOpacity(1.0)
            reveal_h = max(1, int(drawn.height() * min(1.0, revealed)))
            # Wipes upward - the mark builds from its base rather than
            # dropping in from the top.
            src_top = drawn.height() - reveal_h
            painter.drawPixmap(
                x, y + src_top,
                drawn,
                0, src_top, drawn.width(), reveal_h,
            )
        painter.end()
        return canvas

    # ── Playback ─────────────────────────────────────────────────────

    def _advance(self):
        if not self._frames:
            return
        self._index = (self._index + 1) % len(self._frames)
        self.setPixmap(self._frames[self._index])

    def has_frames(self) -> bool:
        """False when no logo could be loaded from disk at all - the
        caller then falls back to the plain text dots rather than
        showing an empty 26px hole where the animation should be."""
        return bool(self._frames)

    def stop(self):
        self._timer.stop()

    def hideEvent(self, event):  # noqa: N802 - Qt's own casing
        # Nothing to animate while off-screen (chat switched away,
        # window minimized) - a timer ticking against a hidden widget
        # is pure waste.
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802 - Qt's own casing
        if self._frames and not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)


def _make_typing_row(text: str = TYPING_DOTS, theme_name: str = "dark") -> QWidget:
    """
    The "working on it" row: the animated logo (see
    LogoTypingIndicator), optionally followed by a status line.

    The QLabel#typingBubble is still here, and still holds whatever
    text is passed, because ChatWidget.set_typing_label() updates this
    row in place by looking that name up - autonomous mode drives a
    running commentary through it ("Taking a screenshot…", "Clicking
    …"). It's just hidden for the default placeholder: three dots next
    to an animating logo is the same information twice. If the logo
    can't be loaded from disk at all, the dots stay as the fallback so
    there's always SOMETHING showing the app is busy.
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    indicator_row = QHBoxLayout()
    indicator_row.setSpacing(8)

    logo = LogoTypingIndicator(theme_name)
    logo.setObjectName("typingLogo")
    has_logo = logo.has_frames()
    if has_logo:
        indicator_row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
    else:
        logo.deleteLater()

    label = QLabel(text)
    label.setObjectName("typingBubble")
    label.setStyleSheet(_typing_bubble_style(c))
    # This label used to carry max-width:90px from its stylesheet, which
    # Qt turns into setMaximumWidth. With no word wrap that is a hard
    # chop at roughly eleven characters, and set_typing_label puts whole
    # sentences here ("Connection lost, retrying in 4s..."). The pill
    # stays small because its text is short, not because it is clamped.
    label.setWordWrap(True)
    label.setMaximumWidth(MESSAGE_MAX_WIDTH)
    label.setVisible(bool(text) and (text != TYPING_DOTS or not has_logo))
    indicator_row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
    indicator_row.addStretch()

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(4)
    content_vbox.addLayout(indicator_row)

    spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    h.addLayout(content_vbox)
    h.addSpacerItem(spacer)

    return row


def _make_thinking_row(theme_name: str = "dark") -> QWidget:
    """
    Claude.ai-style collapsible "Thinking…" section, opened above the
    reply bubble the moment a model's own reasoning starts
    streaming in (Settings > Extended thinking; see
    ChatWidget.begin_thinking_block/append_thinking_chunk/
    finish_thinking_block). Expanded live while thinking is still in
    progress, same as claude.ai itself; finish_thinking_block collapses
    it and swaps the header to "Thought for Ns" - clicking the header
    re-expands it either way, so the reasoning is never actually gone,
    just tucked away once there's an answer to read instead.
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 4, 36, 0)
    h.setSpacing(14)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(2)

    header = QPushButton(tr("▾  Thinking…"))
    header.setObjectName("thinkingHeader")
    header.setCursor(Qt.CursorShape.PointingHandCursor)
    header.setStyleSheet(_thinking_header_style(c))
    # Bug fix: the thought-bubble emoji the header used to lead with rendered
    # inconsistently (a different, often clashing, style depending on
    # the OS/font) and read as an odd, out-of-place "sticker" next to the
    # rest of the app's actual icon set. A real vector icon from the same
    # icon set every other button in the app already uses (app/icons.py,
    # Font Awesome) is what belongs here instead.
    header.setIcon(icons.icon("brain", c, "text_secondary"))
    header.setIconSize(QSize(13, 13))
    header.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    # AlignLeft is not the default. A layout item with no horizontal
    # alignment flag is CENTRED in its cell, and Maximum has no grow
    # flag, so this pill can never fill the row and ends up in the
    # middle of it. That was hidden while thinking was live, because the
    # expanding body underneath anchored the block to the left margin;
    # finish_thinking_block hides that body and the pill is suddenly
    # alone and obviously centred.
    content_vbox.addWidget(header, 0, Qt.AlignmentFlag.AlignLeft)

    body = QLabel("")
    body.setObjectName("thinkingBody")
    body.setWordWrap(True)
    body.setStyleSheet(_thinking_body_style(c))
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    body.setTextFormat(Qt.TextFormat.RichText)
    content_vbox.addWidget(body)

    # Collapsed/expanded is tracked as explicit state on the header,
    # and the label it shows is stored beside it - NOT re-derived by
    # splitting the button's own text back apart on every click, which
    # is what this used to do. That round-trip only held together while
    # the text kept the exact "<arrow><two spaces><label>" shape it was
    # written with, and finish_thinking_block rewrites that text from
    # somewhere else entirely ("Thought for 3s"), so the label could be
    # re-parsed into something empty and the section would read as
    # having vanished instead of collapsed.
    header.setProperty("expanded", True)
    header.setProperty("base_label", "Thinking…")

    def _render_header():
        expanded = bool(header.property("expanded"))
        label = header.property("base_label") or "Thinking…"
        header.setText(f"{'▾' if expanded else '▸'}  {label}")
        # The header is the ONLY way back to collapsed reasoning, so it
        # is never allowed to be the thing that disappears.
        header.setVisible(True)
        header.setToolTip("Hide the model's reasoning" if expanded else "Show the model's reasoning")

    def _toggle():
        header.setProperty("expanded", not bool(header.property("expanded")))
        body.setVisible(bool(header.property("expanded")))
        _render_header()

    header.clicked.connect(_toggle)
    _render_header()

    # Full row width, same as the message bubbles below it - see
    # _make_row's own comment on why there's no more stretchy spacer
    # squeezing content into a narrow fixed column.
    h.addLayout(content_vbox, 1)

    return row


def _make_confirmation_row(goal: str, voice_hint: bool, theme_name: str = "dark") -> QWidget:
    """
    Inline accept/decline card for an autonomous-mode suggestion - shown
    as a normal assistant-side chat bubble so the user can see exactly
    what's about to run before agreeing to it, right in the conversation,
    instead of a separate modal popup they have to leave the chat to see.
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    card = QWidget()
    card.setObjectName("confirmCard")
    card.setStyleSheet(_confirm_card_style(c))
    card.setMaximumWidth(420)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 14, 16, 14)
    card_layout.setSpacing(8)

    title = QLabel(tr("Switch to autonomous mode?"))
    title.setStyleSheet(_confirm_title_style(c))
    card_layout.addWidget(title)

    goal_label = QLabel(goal)
    goal_label.setWordWrap(True)
    goal_label.setStyleSheet(_confirm_goal_style(c))
    card_layout.addWidget(goal_label)

    hint = QLabel(
        "Say « accept » or « refuse », or click a button."
        if voice_hint else
        "Click a button to continue."
    )
    hint.setObjectName("confirmHint")
    hint.setWordWrap(True)
    hint.setStyleSheet(_confirm_hint_style(c))
    card_layout.addWidget(hint)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_accept = QPushButton(tr("Accept"))
    btn_accept.setObjectName("confirmAccept")
    btn_accept.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_accept.setStyleSheet(_confirm_accept_style(c))
    btn_cancel = QPushButton(tr("Cancel"))
    btn_cancel.setObjectName("confirmCancel")
    btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_cancel.setStyleSheet(_confirm_cancel_style(c))
    btn_row.addWidget(btn_accept)
    btn_row.addWidget(btn_cancel)
    btn_row.addStretch()
    card_layout.addLayout(btn_row)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(4)
    content_vbox.addWidget(card)

    spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    h.addLayout(content_vbox)
    h.addSpacerItem(spacer)

    return row


def _make_human_handoff_row(reason: str, theme_name: str = "dark") -> QWidget:
    """
    Inline "I've handled it - Continue" card shown when autonomous mode
    pauses for a human to resolve a CAPTCHA/MFA/verification challenge
    (see ai.actions.tool_request_human_handoff). Reuses the confirmation
    card's visual style - same shape, different copy and a single button
    instead of accept/decline, since there's only one way for the run to
    go from here (or the stop shortcut, which isn't part of this card).
    """
    c = theme.colors(theme_name)
    row = QWidget()
    row.setObjectName("msgRowAi")
    row.setStyleSheet("background: transparent;")

    h = QHBoxLayout(row)
    h.setContentsMargins(28, 10, 36, 10)
    h.setSpacing(14)

    card = QWidget()
    card.setObjectName("handoffCard")
    card.setStyleSheet(_confirm_card_style(c))
    card.setMaximumWidth(420)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(16, 14, 16, 14)
    card_layout.setSpacing(8)

    title_box = QHBoxLayout()
    title_box.setSpacing(6)
    title_icon = QLabel()
    title_icon.setPixmap(icons.pixmap("lock", c, 13, "text_primary"))
    title_icon.setStyleSheet("background: transparent; border: none;")
    title_box.addWidget(title_icon)
    title = QLabel(f"{ai_main.get_assistant_name()} needs you")
    title.setStyleSheet(_confirm_title_style(c))
    title_box.addWidget(title, stretch=1)
    card_layout.addLayout(title_box)

    reason_label = QLabel(reason)
    reason_label.setWordWrap(True)
    reason_label.setStyleSheet(_confirm_goal_style(c))
    card_layout.addWidget(reason_label)

    hint = QLabel(f"Handle it yourself, then click Continue - {ai_main.get_assistant_name()} will pick up right where it left off.")
    hint.setObjectName("handoffHint")
    hint.setWordWrap(True)
    hint.setStyleSheet(_confirm_hint_style(c))
    card_layout.addWidget(hint)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_continue = QPushButton(tr("Continue"))
    btn_continue.setObjectName("handoffContinue")
    btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_continue.setStyleSheet(_confirm_accept_style(c))
    btn_row.addWidget(btn_continue)
    btn_row.addStretch()
    card_layout.addLayout(btn_row)

    content_vbox = QVBoxLayout()
    content_vbox.setSpacing(4)
    content_vbox.addWidget(card)

    spacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    h.addLayout(content_vbox)
    h.addSpacerItem(spacer)

    return row


# px. An image attachment's chip IS this thumbnail (no filename row
# see _make_attachment_chip), so it's sized to be recognisable on its
# own rather than to sit as a small square beside a label. Matches the
# scale claude.ai and ChatGPT stage a pasted image at.
ATTACHMENT_PREVIEW_SIZE = 64
ATTACHMENT_CORNER_RADIUS = 10
# Space kept around the tile so the remove button, which sits over the
# tile's top-right corner, has somewhere to overhang without being
# clipped by the layout.
ATTACHMENT_CHIP_PADDING = 5


def _pixmap_from_pil(image, size: int = ATTACHMENT_PREVIEW_SIZE) -> "QPixmap | None":
    """
    PIL Image (what file_context.load_image_attachment and
    ai.main.trigger_screenshot both produce) → a square, center-cropped,
    ROUND-CORNERED QPixmap for an attachment chip's thumbnail.

    The rounding is painted into the pixmap rather than left to a
    stylesheet: a QLabel's border-radius clips its own background and
    border, NOT the pixmap it displays, so a styled label around a
    square image renders as a square image with a rounded outline
    floating behind its corners - which is what made the staged
    screenshot look like a bug rather than a thumbnail.

    Returns None on anything unreadable rather than raising: a preview
    is a nicety, and a chip with no thumbnail is still usable.
    """
    if image is None:
        return None
    try:
        import io as _io
        buf = _io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        source = QPixmap()
        if not source.loadFromData(buf.getvalue(), "PNG"):
            return None

        # Scale-to-cover then center-crop, so a wide screenshot doesn't
        # become an unreadable letterboxed sliver in a square tile.
        scaled = source.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - size) // 2)
        y = max(0, (scaled.height() - size) // 2)
        cropped = scaled.copy(x, y, size, size)

        rounded = QPixmap(size, size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, size, size), ATTACHMENT_CORNER_RADIUS, ATTACHMENT_CORNER_RADIUS
        )
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        return rounded
    except Exception as e:
        print(f"[chat] Couldn't build an attachment preview: {e}")
        return None


def _make_attachment_chip(
    filename: str,
    theme_name: str = "dark",
    is_image: bool = False,
    preview: "QPixmap | None" = None,
) -> QWidget:
    """
    One file (or image) staged in the chat input, with an X to remove it
    before sending. Purely a pre-send UI element: it's never persisted,
    and disappears the moment the message goes out (or is removed by
    hand).

    Laid out ChatGPT-style rather than as a single-line pill: the name
    and the remove button sit on the chip's TOP row, with an image's
    thumbnail below them - so the name reads as a title for the thing
    attached rather than floating vertically centered beside it, and
    you can see WHICH screenshot/image you staged without sending it
    first. A non-image file has no thumbnail, so its chip is just that
    top row (the old pill shape, effectively).
    """
    c = theme.colors(theme_name)
    chip = QWidget()
    chip.setObjectName("attachmentChip")

    # An image is its own label - the thumbnail IS the identification,
    # the same way claude.ai and ChatGPT stage a pasted image. A
    # filename row above it would be saying "this is
    # screenshot_1758….png" about a picture you can already see, and
    # stacking that on every staged image pushes the composer down for
    # no information. Files keep their name, because a .docx has
    # nothing to look at.
    if preview is not None:
        pad = ATTACHMENT_CHIP_PADDING
        size = preview.width()
        chip.setStyleSheet("background: transparent; border: none;")
        # Fixed to tile + padding: the remove button overhangs the
        # tile's corner, and a chip sized to the tile alone would clip
        # it. Nothing is laid out here - both children are positioned
        # directly, because a layout would fight the overlap.
        chip.setFixedSize(size + pad, size + pad)

        thumb = QLabel(chip)
        thumb.setObjectName("attachmentPreview")
        thumb.setPixmap(preview)
        thumb.setFixedSize(size, size)
        thumb.move(0, pad)
        thumb.setToolTip(filename)
        # No border-radius here: the corners are already painted into
        # the pixmap (see _pixmap_from_pil). A background tint shows
        # through them, which is what gives the tile a soft edge
        # against the composer instead of a hard crop.
        thumb.setStyleSheet(
            f"background-color:{c['bg_input']};border:none;"
            f"border-radius:{ATTACHMENT_CORNER_RADIUS}px;"
        )

        btn_remove = QPushButton(chip)
        btn_remove.setIcon(icons.icon("close", c, "white"))
        btn_remove.setIconSize(QSize(8, 8))
        btn_remove.setObjectName("attachmentRemove")
        btn_remove.setFixedSize(18, 18)
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove.setToolTip(f"Remove {filename}")
        # A dark circular chip over the image's corner - legible over
        # whatever the screenshot happens to contain, which a plain
        # icon on the image itself is not.
        btn_remove.setStyleSheet(
            "QPushButton{background-color:rgba(20,20,22,0.82);"
            "border:1px solid rgba(255,255,255,0.28);border-radius:9px;}"
            f"QPushButton:hover{{background-color:{c['danger']};"
            "border-color:rgba(255,255,255,0.5);}"
        )
        btn_remove.move(size - 13, 0)
        btn_remove.raise_()
        return chip

    chip.setStyleSheet(_attachment_chip_style(c))
    layout = QVBoxLayout(chip)
    layout.setContentsMargins(10, 6, 6, 6)
    layout.setSpacing(6)

    # ── Title row (icon + name + remove), pinned to the top ──────────
    title_row = QHBoxLayout()
    title_row.setContentsMargins(0, 0, 0, 0)
    title_row.setSpacing(6)

    icon_lbl = QLabel()
    icon_lbl.setPixmap(icons.pixmap("image" if is_image else "file", c, 12, "text_secondary"))
    icon_lbl.setStyleSheet("background: transparent; border: none;")
    title_row.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

    label = QLabel(filename)
    label.setObjectName("attachmentName")
    label.setStyleSheet(_attachment_chip_label_style(c))
    # Elided by width rather than wrapped: a long filename shouldn't
    # turn one chip into a three-line block and push the composer down.
    label.setMaximumWidth(150)
    metrics = label.fontMetrics()
    label.setText(metrics.elidedText(filename, Qt.TextElideMode.ElideMiddle, 150))
    label.setToolTip(filename)
    title_row.addWidget(label, 1, Qt.AlignmentFlag.AlignVCenter)

    btn_remove = QPushButton()
    btn_remove.setIcon(icons.icon("close", c, "text_muted"))
    btn_remove.setIconSize(QSize(9, 9))
    btn_remove.setObjectName("attachmentRemove")
    btn_remove.setFixedSize(16, 16)
    btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_remove.setToolTip(tr("Remove this attachment"))
    btn_remove.setStyleSheet(_attachment_chip_remove_style(c))
    title_row.addWidget(btn_remove, 0, Qt.AlignmentFlag.AlignTop)

    layout.addLayout(title_row)

    # No thumbnail branch here: an image chip is handled entirely above
    # and returns early (it's the thumbnail and nothing else). Anything
    # reaching this point is a file with no picture to show.
    return chip


class ChatWidget(QWidget):

    request_sent = pyqtSignal(int, str, bool)     # (chat_id, prompt, from_voice)
    new_message_sent = pyqtSignal(int)          # (chat_id)
    chat_title_changed = pyqtSignal(int, str)   # (chat_id, new_title)
    mic_requested = pyqtSignal(int)             # (chat_id)
    mic_stop_speaking_requested = pyqtSignal(int)  # (chat_id) - click while Kestrel is talking
    mic_cancel_requested = pyqtSignal(int)      # (chat_id) - click while listening (see VoiceWorker.request_cancel)
    confirmation_response = pyqtSignal(int, bool)  # (chat_id, accepted) - autonomous-mode card answered
    human_handoff_response = pyqtSignal(int)       # (chat_id) - "Continue" clicked on a human-handoff card
    speak_requested = pyqtSignal(int, str)         # (chat_id, text) - "Read aloud" clicked on an AI message
    stop_read_aloud_requested = pyqtSignal(int)    # (chat_id) - "Read aloud" clicked again on the message already being read
    regenerate_requested = pyqtSignal(int, int)    # (chat_id, message_id) - "Regenerate" clicked on the chat's current last reply
    profile_switched = pyqtSignal()                # emitted after a successful profile switch from this widget's own picker - lets MainWindow refresh Settings' profile list so it doesn't show a stale active marker

    def __init__(self):
        super().__init__()
        uic.loadUi(fr"{root_path}/app/ui/chat_widget.ui", self)

        self._theme: str = ai_main.get_theme()
        # loadUi bakes in the dark-theme QSS from the .ui file's own
        # styleSheet properties (see _build_chrome_style's docstring)
        # reapply immediately so a non-default saved theme is correct
        # from the very first frame, not just after a later set_theme().
        self.setStyleSheet(_build_chrome_style(theme.colors(self._theme)))
        self.chatInput.setStyleSheet(_chat_input_style(theme.colors(self._theme)))

        self._current_chat_id: int | None = None
        self._typing_row: QWidget | None = None
        self._reveal_timers: set = set()
        # State for a currently in-progress STREAMED reply (real
        # network streaming - see begin_streaming_response below); None
        # values mean "no stream in progress right now".
        self._streaming_chat_id: int | None = None
        self._streaming_row: QWidget | None = None
        self._streaming_container: "QWidget | None" = None
        self._streaming_label: "QLabel | None" = None
        self._streaming_text: str = ""
        # State for a currently in-progress THINKING block (Settings >
        # Extended thinking; see begin_thinking_block below) - same
        # None-means-"nothing in progress" convention as the streaming
        # block above, tracked separately since a thinking block can be
        # open before the matching answer bubble even exists yet (and,
        # on a turn with no visible thinking, the answer bubble opens
        # with no thinking block at all).
        self._thinking_chat_id: int | None = None
        self._thinking_row: QWidget | None = None
        self._thinking_header: "QPushButton | None" = None
        self._thinking_body: "QLabel | None" = None
        self._thinking_text: str = ""
        self._thinking_started_at: float = 0.0
        self._mic_state: str = "idle"  # "idle" | "preparing" | "listening" | "speaking"
        # Read-aloud state, kept apart from _mic_state on purpose - see
        # the "Read aloud" section below. _pending_speak_bubble is the
        # one a click just asked for; _speaking_bubble is the one audio
        # is actually playing for, which is only known once the worker
        # says it started.
        self._pending_speak_bubble = None
        self._speaking_bubble = None
        self._pending_confirmation_row: QWidget | None = None
        self._answered_confirmation_row: QWidget | None = None
        self._pending_handoff_row: QWidget | None = None
        # Which chat (if any) has an unresolved human handoff, and why
        # unlike _pending_handoff_row (a live widget, wiped by
        # _clear_messages() on every load_chat()), these two survive a
        # reload so load_chat() can re-attach a WORKING Continue card
        # instead of the inert text bubble db.add_message persisted.
        # Without this, switching chats and back (or any reload) while
        # paused for a CAPTCHA silently destroyed the only way to resume
        # the run - the card would still be *visible* as plain history
        # text, but its button would be gone. See show_human_handoff /
        # load_chat / MainWindow.clear_pending_handoff.
        self._pending_handoff_chat_id: int | None = None
        self._pending_handoff_reason: str | None = None
        self._pending_attachments: list[dict] = []  # [{"name": str, "text": str}]
        # ChatGPT-style inline message editing (_start_edit/_confirm_edit/
        # _cancel_edit) - which row (if any) is mid-edit, and which rows
        # got hidden (not deleted) while it is, so Cancel can restore
        # them exactly.
        self._active_edit_row: QWidget | None = None
        self._hidden_rows_during_edit: list[QWidget] = []
        # Composer "expand" state - see _build_expand_button /
        # _current_input_cap. False = the normal grow-with-content
        # composer; True = a tall drafting area for writing something
        # long without it scrolling inside a few visible lines.
        self._composer_expanded = False
        # Optimistic profile switching - see _on_profile_picker_changed.
        # _switch_in_flight is True while the (threaded) session rebuild
        # is still running; a message sent during that window is parked
        # in _deferred_send and handed off by _flush_deferred_send once
        # the new session is live.
        self._switch_in_flight = False
        self._optimistic_profile_name: "str | None" = None
        # Set while a voice transcript is sitting in the composer
        # awaiting review (see stage_voice_transcript) - consumed by
        # _send_message so the reply is still spoken back even though
        # the send happened by hand, minutes later.
        self._pending_voice_reply = False
        self._deferred_send: "tuple[int, str, bool] | None" = None
        self._profile_switch_thread = None
        self._profile_switch_worker = None

        # ── Grab the message layout from the .ui ───────────────────────────
        # messagesContents is messagesScroll's child QWidget (widgetResizable)
        # Its layout (messagesLayout) holds the message rows.
        self._messages_layout: QVBoxLayout = self.messagesContents.layout()

        # Do not manufacture scrollable height with a fake bottom spacer.
        # The newest-message positioning code moves the scrollbar directly
        # to the target row instead. Changing the content height just to
        # center a row was what made the whole conversation jump upward.
        self._messages_layout.addStretch()

        # Both _scroll_to_bottom() and _recenter_on() move the scrollbar
        # via this instead of setValue()'ing it directly. During a
        # streaming/typed-out reply they're called every ~15ms as the
        # reply grows a few characters at a time - jumping the scrollbar
        # straight to each new position on every one of those calls reads
        # as the whole conversation visibly hopping in little steps in
        # time with the text. Routing every programmatic scroll through
        # one shared, continuously-retargetable animation (see
        # SmoothScroller's own docstring) turns that into a single
        # smooth glide instead - the scrollbar drifts to keep up with the
        # reply rather than snapping there and back on every chunk.
        self._scroll_follower = SmoothScroller(self.messagesScroll.verticalScrollBar())
        # Eased WHEEL scrolling - a separate concern from the above (user
        # spinning the wheel vs. code moving the view), see
        # app/smooth_scroll.py for why this needs its own mechanism.
        install_smooth_scroll(self.messagesScroll)

        # ── Wire up widgets from the .ui ────────────────────────────────────
        # chatInput = QPlainTextEdit  |  sendBtn = QPushButton
        self.chatInput.installEventFilter(self)
        # DragEnter/DragMove/Drop are delivered to chatInput's internal
        # viewport() widget, not to chatInput itself - QPlainTextEdit (like
        # any QAbstractScrollArea subclass) routes those through its
        # viewport. Filtering only `self.chatInput` meant every drag/drop
        # silently missed this filter and fell through to QPlainTextEdit's
        # own default drop handling, which just inserts the dropped
        # file:// URL as plain text - exactly the "only displays the file
        # path" bug. Both need the filter installed for the chip logic
        # below to ever see these events.
        self.chatInput.viewport().installEventFilter(self)
        self.chatInput.textChanged.connect(self._on_text_changed)
        self.chatInput.setAcceptDrops(True)
        # Bug fix: chatInput used to sit at a permanently fixed height
        # inputArea's own maximumSize (see app/ui/chat_widget.ui) capped
        # the whole input bar to ~90px regardless of content, and nothing
        # here ever resized chatInput itself, so anything past the first
        # visible line just scrolled invisibly INSIDE that one fixed-size
        # box instead of the box growing to show it - unlike every other
        # chat UI (including claude.ai's own composer), where the input
        # grows with what's typed, up to a cap, before it starts
        # scrolling. _autosize_chat_input (called on every keystroke via
        # _on_text_changed, and once here for the initial empty state)
        # is what actually grows/shrinks it now; inputArea's own cap was
        # raised from ~90px to 240px to give it room to do that.
        #
        # The expand toggle is built BEFORE that first autosize call,
        # not further down with the other composer pieces:
        # _autosize_chat_input shows/hides self._expandBtn, so it has
        # to exist by the time anything calls it.
        # Wrapping, stated explicitly rather than relying on the default,
        # and - the part that actually matters - a horizontal size policy
        # of Ignored: "use whatever width the layout gives me, never ask
        # for more".
        #
        # Without that, a long line's own sizeHint asks for the width it
        # would need to fit on ONE line, and a QPlainTextEdit wraps at
        # its WIDGET width - so a widget allowed to keep widening never
        # reaches a width its text has to wrap at. The text runs off
        # sideways instead of moving to a second line, and since the box
        # only grows taller when the document gains lines, it also never
        # grows. Same root for both halves of "the text expands
        # horizontally instead of vertically".
        self.chatInput.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.chatInput.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chatInput.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        self._build_expand_button()
        self._build_attach_button()
        self._build_thinking_button()
        self._autosize_chat_input()
        self.sendBtn.setEnabled(False)
        # Wrapped in a lambda: QPushButton.clicked emits a `checked: bool`
        # arg that would otherwise land in _send_message's own (unrelated)
        # voice_reply bool parameter.
        self.sendBtn.clicked.connect(lambda: self._send_message())
        self.micBtn.clicked.connect(self._on_mic_clicked)
        self.set_mic_state("idle")  # themed icon from the first frame, not whatever the .ui's own static icon property shows

        # Bug fix: the chat input's placeholder ("Ask Kestrel…") was set
        # once, statically, in the .ui file and never touched again in
        # code - unlike the mic tooltip just above (set_mic_state) or
        # every other assistant-name reference in this file, it never
        # picked up a custom assistant name. Anyone who renamed their
        # assistant away from the "Kestrel" product name (which is the
        # whole point of ai.main.get_assistant_name() being separate
        # from the product name in the first place) kept seeing the
        # product's own name staring back at them in the one place
        # they type. Refreshed here and in refresh_profile_picker()
        # below (chat page becoming visible again) and from
        # MainWindow._on_assistant_name_changed (a rename from Settings,
        # which otherwise has no reason to touch this page at all).
        self._refresh_assistant_name_copy()

        self._build_profile_picker()
        self._build_model_picker()

        # ── Attachment chip row, inserted above the input row itself.
        # Hidden until something is actually dropped - see _add_attachment.
        # setMaximumHeight(0) on top of setVisible(False) (via
        # _set_attachments_row_visible below) is belt-and-suspenders: a
        # hidden widget SHOULD already take zero space in its parent
        # layout, but this row's own bottom margin (contentsMargins'
        # 4px below) plus a stale sizeHint left over from when it last
        # held chips could otherwise leave a lingering gap above the
        # composer instead of the input box hugging the text the moment
        # the last attachment is removed - pinning the max height to 0
        # makes "empty" mean zero pixels, not "whatever it used to be."
        self._attachmentsRow = QWidget()
        self._attachmentsRow.setStyleSheet("background: transparent;")
        self._attachmentsLayout = QHBoxLayout(self._attachmentsRow)
        self._attachmentsLayout.setContentsMargins(0, 0, 0, 4)
        self._attachmentsLayout.setSpacing(6)
        self._attachmentsLayout.addStretch()
        self._set_attachments_row_visible(False)
        self.inputWrapperLayout.insertWidget(0, self._attachmentsRow)

        # Transient status line for a drop that failed (unsupported type,
        # unreadable PDF, ...) - cleared on the next successful drop/send.
        # Same zero-height-when-hidden treatment as the chip row above.
        self._attachStatus = QLabel()
        self._attachStatus.setStyleSheet(_attachment_error_style(theme.colors(self._theme)))
        self._attachStatus.setWordWrap(True)
        self._set_attach_status_visible(False)
        self.inputWrapperLayout.insertWidget(1, self._attachStatus)

        # Initial title
        self.chatTitle.setText(tr("New chat"))


    # ── Internal helpers ────────────────────────────────────────────────────

    def _build_expand_button(self):
        """
        A ChatGPT-style expand toggle in the composer, left of the mic.
        Collapsed (the default) the input grows with what's typed up to
        _CHAT_INPUT_MAX_HEIGHT and then scrolls; expanded it becomes a
        tall drafting area (_CHAT_INPUT_EXPANDED_RATIO of the window)
        so writing something long isn't done through an 8-line
        peephole. Hidden until there's actually enough text to warrant
        it - an empty composer has nothing to expand.
        """
        c = theme.colors(self._theme)
        self._expandBtn = QPushButton()
        self._expandBtn.setObjectName("micBtn")  # same 34x34 chrome as the mic/send buttons
        self._expandBtn.setIcon(icons.icon("chevron-up", c, "text_muted"))
        self._expandBtn.setIconSize(QSize(13, 13))
        self._expandBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expandBtn.setToolTip(tr("Expand the composer"))
        self._expandBtn.setVisible(False)
        self._expandBtn.clicked.connect(self._toggle_composer_expanded)
        # Index 1 = straight after chatInput, before micBtn/sendBtn.
        self.inputTopLayout.insertWidget(1, self._expandBtn)

    def _build_attach_button(self):
        """
        Attach a file from the composer.

        Attaching was drag-and-drop only, which is fine when you have a
        file manager open next to the window and useless otherwise -
        there was no way to attach anything using the keyboard, or from
        a folder you would have to go and find first. The drop path
        stays exactly as it was; this reaches the same
        _handle_dropped_file, so both routes share one set of
        size/type/extraction rules rather than growing a second one.
        """
        c = theme.colors(self._theme)
        self._attachBtn = QPushButton()
        self._attachBtn.setObjectName("micBtn")   # same 34x34 chrome as mic/send
        self._attachBtn.setIcon(icons.icon("plus", c, "text_muted"))
        self._attachBtn.setIconSize(QSize(14, 14))
        self._attachBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attachBtn.setToolTip(tr("Attach a file"))
        self._attachBtn.clicked.connect(self._on_attach_clicked)
        self.inputTopLayout.insertWidget(1, self._attachBtn)

    def _on_attach_clicked(self):
        from PyQt6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Attach files"), "",
            tr("Supported files") + " (" + file_context.file_dialog_filter() + ");;"
            + tr("All files") + " (*)",
        )
        for path in paths:
            self._handle_dropped_file(path)

    def _build_thinking_button(self):
        """
        Extended thinking, toggled from the composer.

        It lives in Settings, which is the wrong place for it: whether a
        particular question is worth the extra tokens and latency is a
        per-message decision, not a preference you set once. Buried
        three pages away, it was effectively off permanently.

        The button reflects and writes the same setting Settings does,
        so the two stay in step rather than becoming two switches for
        one thing.
        """
        c = theme.colors(self._theme)
        self._thinkBtn = QPushButton()
        self._thinkBtn.setObjectName("micBtn")
        self._thinkBtn.setCheckable(True)
        self._thinkBtn.setChecked(ai_main.get_extended_thinking_enabled())
        self._thinkBtn.setIconSize(QSize(14, 14))
        self._thinkBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thinkBtn.clicked.connect(self._on_thinking_toggled)
        self._sync_thinking_button()
        self.inputTopLayout.insertWidget(2, self._thinkBtn)

    def _on_thinking_toggled(self, checked: bool):
        ai_main.set_extended_thinking_enabled(bool(checked))
        self._sync_thinking_button()

    def _sync_thinking_button(self):
        """Icon and tooltip for the current state. The checked look
        comes from micBtn's own :checked rule, which is already an
        accent fill - the same affordance the mic uses when live."""
        if not hasattr(self, "_thinkBtn"):
            return
        c = theme.colors(self._theme)
        on = self._thinkBtn.isChecked()
        # A brain, not a wand. "sparkles" (fa5s.magic) is the generic
        # AI-does-something icon and says nothing about thinking - next
        # to a send button and a microphone it reads as "enhance" or
        # "generate", which is not what the toggle does.
        self._thinkBtn.setIcon(icons.icon("brain", c, "white" if on else "text_muted"))
        self._thinkBtn.setToolTip(
            tr("Extended thinking is on - slower, more thorough") if on
            else tr("Turn on extended thinking")
        )

    def _toggle_composer_expanded(self):
        self._composer_expanded = not self._composer_expanded
        c = theme.colors(self._theme)
        self._expandBtn.setIcon(
            icons.icon("chevron-down" if self._composer_expanded else "chevron-up", c, "text_muted")
        )
        self._expandBtn.setToolTip(
            "Shrink the composer" if self._composer_expanded else "Expand the composer"
        )
        self._autosize_chat_input()

    def _current_input_cap(self) -> int:
        """How tall chatInput is allowed to get right now - the normal
        cap, or a window-relative one while expanded (never smaller
        than the normal cap, so expanding is never a downgrade on a
        short window)."""
        if not self._composer_expanded:
            return self._CHAT_INPUT_MAX_HEIGHT
        return max(self._CHAT_INPUT_MAX_HEIGHT, int(self.height() * self._CHAT_INPUT_EXPANDED_RATIO))

    def _build_profile_picker(self):
        """
        A profile switcher in the chat's topbar - lets the user change
        which named AI profile (ai.profiles.Profile: its own name,
        provider, and model) is active without leaving the chat page,
        the same switch the wake word already does by voice
        (ai.profiles.match_profile_for_phrase) or Settings' own profile
        list (app/settings_widget.py's _build_profiles_section) does by
        clicking "Switch". Placed to the LEFT of the model picker built
        right after this - switching profile changes which model is
        active too (each profile owns its own), so the model picker's
        own selection is refreshed to match in _on_profile_picker_changed
        rather than being left showing the PREVIOUS profile's model.

        Options are rebuilt from ai.profiles.list_profiles() whenever
        this page becomes visible again (see MainWindow._navigate) - not
        just once here at construction - so a profile added,
        renamed, or removed from Settings shows up here without
        needing its own dedicated signal for every one of those.
        """
        active_id = profiles.get_active_profile_id()
        options = [(p.id, p.name) for p in profiles.list_profiles()]

        # expanding=False: this sits right after the topbar's stretch
        # spacer, so without this it would fight that spacer for
        # leftover space and grow wider than its content needs on any
        # window wider than the cramped case (see StyledDropdown's own
        # docstring on the `expanding` param).
        self._profilePicker = StyledDropdown(options, active_id, self._theme, min_width=110, expanding=False)
        self._profilePicker.setObjectName("chatProfilePicker")
        self.topbarLayout.addWidget(self._profilePicker)
        self._profilePicker.currentValueChanged.connect(self._on_profile_picker_changed)

    def refresh_profile_picker(self):
        """Re-reads ai.profiles.list_profiles()/get_active_profile_id() - call this whenever the chat page becomes visible again, since profiles
        can be added/renamed/removed from Settings while this page isn't showing."""
        self._profilePicker.set_options([(p.id, p.name) for p in profiles.list_profiles()])
        self._profilePicker.set_value(profiles.get_active_profile_id())
        self._refresh_assistant_name_copy()

    def _refresh_assistant_name_copy(self) -> None:
        """Re-applies the current assistant name (ai_main.get_assistant_name())
        to every static bit of chat-page copy that names the assistant but
        isn't already recomputed on every use - see this method's call
        sites for when that's needed (construction, a profile switch, a
        rename from Settings, or just returning to this page)."""
        # Per request: this placeholder should read as a plain, generic
        # input hint - not name the assistant at all (an earlier fix
        # made it dynamic, "Ask <assistant name>…", but showing the
        # name here reads oddly for a renamed assistant just as
        # "Kestrel" hardcoded did - the fix wasn't wrong to remove the
        # hardcoded product name, just wrong to replace it with any
        # name instead of none). The mic tooltip still uses the
        # assistant's name - that one reads naturally ("Talk to
        # Jasper"); a static input field asking the same way doesn't.
        self.chatInput.setPlaceholderText(tr("Ask anything…"))
        self.set_mic_state(self._mic_state)  # re-derives the mic tooltip's assistant name too

    def _display_assistant_name(self) -> str:
        """The assistant name the UI should be SHOWING right now.

        While a profile switch is still being applied in the background
        (see _on_profile_picker_changed), that's the name of the
        profile being switched TO - ai_main.get_assistant_name() is
        still the outgoing one until activate_profile() lands, and
        showing the old name in the mic tooltip for the second the
        rebuild takes is exactly the lag the optimistic switch exists
        to hide."""
        return self._optimistic_profile_name or ai_main.get_assistant_name()

    def _on_profile_picker_changed(self, profile_id: str):
        """
        Switches profile OPTIMISTICALLY: the picker, the model picker
        and the assistant-name copy all update on this very tick, and
        the actual work - ai.profiles.activate_profile(), which rebuilds
        the whole chat session (two .env writes, an instructions-file
        read, a fresh provider client, reseeding history) - runs on a
        worker thread behind it.

        That rebuild is the reason switching used to visibly hang the
        window for something like a second: it was called straight from
        this slot, i.e. on the UI thread, so nothing repainted and no
        keystroke was accepted until it finished. It can't be made
        instant (a provider client genuinely has to be constructed), so
        it stops being something the user waits on instead.

        Typing during the switch is fine - the composer stays live. A
        message SENT during it is held until the new session is ready
        (see _flush_deferred_send); it still appears in the transcript
        immediately, it just isn't handed to a session that's being
        replaced underneath it. On failure - almost always "this
        profile's provider has no API key yet" - everything rolls back
        to whichever profile is really active.
        """
        if profile_id == profiles.get_active_profile_id():
            return  # shouldn't normally fire (StyledDropdown only emits on an actual change), kept as a cheap safety no-op

        # Optimistic UI, applied before any of the slow work starts.
        # list_profiles() is a cheap read of the already-loaded
        # profiles.json - the expensive part is the session rebuild, not
        # this.
        target = next((p for p in profiles.list_profiles() if p.id == profile_id), None)
        if target is not None:
            self._optimistic_profile_name = target.name
            self._modelPicker.set_value(self._model_picker_key(target.provider, target.model_id))
            self._refresh_assistant_name_copy()

        self._switch_in_flight = True
        self._start_profile_switch(profile_id)

    def _start_profile_switch(self, profile_id: str) -> None:
        from PyQt6.QtCore import QThread
        from app.workers.profile_switch_worker import ProfileSwitchWorker

        thread = QThread(self)
        worker = ProfileSwitchWorker(profile_id)
        worker.moveToThread(thread)
        # Kept on self so neither is garbage-collected mid-switch
        # same ownership pattern as this app's other workers.
        self._profile_switch_thread = thread
        self._profile_switch_worker = worker

        worker.succeeded.connect(self._on_profile_switch_succeeded)
        worker.failed.connect(self._on_profile_switch_failed)
        worker.succeeded.connect(lambda *_: thread.quit())
        worker.failed.connect(lambda *_: thread.quit())
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.start.emit)
        thread.start()

    def _on_profile_switch_succeeded(self, profile_id: str, profile_name: str) -> None:
        self._switch_in_flight = False
        self._optimistic_profile_name = None
        # Re-read rather than trusting the optimistic values: the
        # profile's model is only truly active once activate_profile has
        # actually run, and a rename/edit could have landed meanwhile.
        active_key = self._model_picker_key(ai_main.get_active_provider(), ai_main.get_active_model())
        self._modelPicker.set_value(active_key)
        self._refresh_assistant_name_copy()
        self.profile_switched.emit()
        self._flush_deferred_send()

    def _on_profile_switch_failed(self, profile_id: str, message: str) -> None:
        self._switch_in_flight = False
        self._optimistic_profile_name = None
        # Roll the optimistic UI back to whatever is genuinely live.
        self._profilePicker.set_value(profiles.get_active_profile_id())
        self._modelPicker.set_value(
            self._model_picker_key(ai_main.get_active_provider(), ai_main.get_active_model())
        )
        self._refresh_assistant_name_copy()
        self._deferred_send = None  # the session it was waiting for never arrived
        QMessageBox.warning(self, "Couldn't switch profile", message)

    def _flush_deferred_send(self) -> None:
        """Hands off a message that was sent mid-switch, now that the
        new session is live - see _on_profile_picker_changed."""
        pending, self._deferred_send = self._deferred_send, None
        if pending is None:
            return
        chat_id, prompt, from_voice = pending
        self.request_sent.emit(chat_id, prompt, from_voice)

    def _build_model_picker(self):
        """
        A ChatGPT-style model dropdown in the chat's topbar (next to the
        chat title), listing ai.providers.base.MODEL_CHOICES - every
        provider Kestrel supports, not just whichever one this chat
        happens to be on right now. Selecting a different entry calls
        ai_main.set_active_provider_and_model(), which rebuilds the
        live chat session with the conversation's history carried over
        (see that function's own docstring, and
        ai.providers.switch_provider() for how the carry-over itself
        works) - switching mid-conversation, not just for the next new
        chat.

        Uses StyledDropdown (app/styled_dropdown.py) rather than a plain
        QComboBox - the same custom-built popup widget
        settings_widget.py's Dark/Light theme picker and per-AI-profile
        model picker both already use, so every dropdown in this app
        shares one look instead of this one being styled QSS-on-a-
        QComboBox (fighting the native popup's platform chrome, per
        StyledDropdown's own docstring) while the others use the real
        custom-drawn popup.
        """
        from ai.providers.base import MODEL_CHOICES

        options = [(self._model_picker_key(c.provider, c.model_id), c.label) for c in MODEL_CHOICES]
        active_key = self._model_picker_key(ai_main.get_active_provider(), ai_main.get_active_model())

        # min_width trimmed from 140 (StyledDropdown's own default) and,
        # more importantly, expanding=False so this doesn't stretch to
        # fill the topbar's leftover width the way an Expanding button
        # placed after a stretch spacer otherwise would - see
        # StyledDropdown's own docstring on the `expanding` param. That
        # stretch, not the min-width number, was what made this dropdown
        # read as "too wide" on anything but a narrow window.
        self._modelPicker = StyledDropdown(options, active_key, self._theme, min_width=104, expanding=False)
        self._modelPicker.setObjectName("modelPicker")

        # Appended AFTER the topbar's horizontal spacer (topbar is
        # [chatTitle, spacer] - item count 2 once loadUi has run), so the
        # picker actually lands flush right, same side ChatGPT's own model
        # dropdown sits on. It used to be insertWidget(1, ...), i.e. BEFORE
        # the spacer - that put it immediately next to chatTitle instead,
        # with the spacer's stretch pushing empty space out to the far
        # right edge instead of around the picker, which is what actually
        # produced the "squeezed next to the title" look this was meant to
        # avoid in the first place.
        self.topbarLayout.addWidget(self._modelPicker)

        self._modelPicker.currentValueChanged.connect(self._on_model_picker_changed)

    @staticmethod
    def _model_picker_key(provider: str, model: str) -> str:
        return f"{provider}|{model}"

    def _on_model_picker_changed(self, key: str):
        provider, model = key.split("|", 1)
        if (provider, model) == (ai_main.get_active_provider(), ai_main.get_active_model()):
            return  # shouldn't normally fire (StyledDropdown only emits on an actual change), kept as a cheap safety no-op

        try:
            ai_main.set_active_provider_and_model(provider, model)
        except Exception as e:
            # Most commonly ai.providers.ProviderClientError - no key
            # saved yet for a BYO-key provider. Revert the dropdown to
            # whatever's still actually active (set_active_provider_and_model
            # leaves the OLD session live on failure - see its own
            # docstring) rather than leaving the UI showing a model
            # that isn't really the one in use.
            QMessageBox.warning(self, "Couldn't switch model", str(e))
            active_key = self._model_picker_key(ai_main.get_active_provider(), ai_main.get_active_model())
            self._modelPicker.set_value(active_key)

    def _clear_static_messages(self):
        layout = self._messages_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _insert_before_stretch(self, widget: QWidget):
        """Insert a message row immediately before the final layout stretch."""
        insert_at = max(0, self._messages_layout.count() - 1)
        self._messages_layout.insertWidget(insert_at, widget)

    def _scroll_to_bottom(self):
        """Glide to the actual bottom of the conversation.

        This helper is intentionally separate from _recenter_on(): ordinary
        chat-load/typing/card operations may want the true bottom, while the
        newest sent/replied message can be centered without changing content
        height. Both end up going through self._scroll_follower rather than
        setting the scrollbar's value directly - see SmoothScroller's own
        docstring for why that matters specifically during streaming.
        """
        try:
            sb = self.messagesScroll.verticalScrollBar()
            self._scroll_follower.scroll_to(sb.maximum())
        except RuntimeError:
            return

    def _recenter_on(self, row: QWidget | None) -> None:
        """
        Scroll directly to ``row`` instead of changing the conversation's
        content height with a fake bottom spacer. The row is centered when
        there is enough scrollable content; otherwise Qt clamps naturally to
        the available scroll range. Goes through self._scroll_follower (see
        its own docstring) instead of setValue()'ing the scrollbar straight
        to the target - this is called on every tick of a typed-out or
        streamed reply, and jumping there instantly each time is what made
        the conversation visibly hop in little steps as the reply grew.
        """
        if row is None:
            return
        try:
            row.updateGeometry()
            self._messages_layout.activate()

            viewport = self.messagesScroll.viewport()
            scrollbar = self.messagesScroll.verticalScrollBar()
            row_center_y = row.geometry().center().y()
            viewport_center_y = viewport.height() // 2
            target_value = row_center_y - viewport_center_y

            # Never scroll UPWARD from where the conversation already
            # sits. Every caller of this method recenters on the NEWEST
            # row - the one just appended, or the reply currently
            # growing - all of which live at the bottom, so a target
            # above the current position can only mean `row` hasn't
            # been given its real geometry yet.
            #
            # That's the "sending a message throws you to the top of
            # the chat" bug, and why an attached image made it far more
            # likely: _send_message calls _clear_attachments() (which
            # collapses the chip row, changing the viewport's height)
            # immediately before recentering, so the layout is still
            # mid-resize when geometry() is read here. A stale row at
            # y≈0 computes a negative target, Qt clamps that to 0, and
            # the view lands at the very first message of the
            # conversation. A taller chip row (image chips are taller
            # than file ones) makes the discrepancy bigger, which is
            # why it showed up with an image attached.
            target_value = max(target_value, scrollbar.value())

            self._scroll_follower.scroll_to(target_value)
        except RuntimeError:
            # The row or layout was removed while a delayed UI update was
            # executing (for example after switching chats).
            return

    # ── Keyboard filtering (Enter to send) ─────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.chatInput and event.type() == QEvent.Type.Resize:
            # Re-measure on every width change (window resize, sidebar
            # toggling, etc.) - _autosize_chat_input's wrap-aware height
            # calculation depends on the viewport's CURRENT width, so a
            # width change alone (no text change) can still change how
            # many lines the same text wraps to.
            QTimer.singleShot(0, self._autosize_chat_input)

        if obj is self.chatInput and isinstance(event, QKeyEvent):
            if (event.type() == QKeyEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_Return
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                if self.sendBtn.isEnabled():
                    self._send_message()
                return True
            if (event.type() == QKeyEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_Escape
                    and self._pending_voice_reply):
                # One key to throw away a transcript that came out
                # wrong - the alternative being select-all-then-delete
                # on something you never typed in the first place.
                # Only while a voice draft is actually staged, so
                # Escape doesn't become a way to lose something typed
                # by hand.
                self._pending_voice_reply = False
                self.chatInput.clear()
                self._autosize_chat_input()
                self._update_send_enabled()
                return True

        if obj in (self.chatInput, self.chatInput.viewport()) and event.type() in (
            QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop
        ):
            # Handled entirely here instead of falling through to
            # QPlainTextEdit's own drop handling, which would just insert
            # the raw file:// URL as text - not what dropping a file onto
            # the chat is for.
            mime = event.mimeData()
            local_files = [
                url.toLocalFile() for url in mime.urls() if url.isLocalFile()
            ] if mime.hasUrls() else []
            if not local_files:
                return super().eventFilter(obj, event)

            if isinstance(event, (QDragEnterEvent,)):
                event.acceptProposedAction()
                return True
            if event.type() == QEvent.Type.DragMove:
                event.accept()
                return True
            if isinstance(event, QDropEvent):
                event.acceptProposedAction()
                for path in local_files:
                    self._handle_dropped_file(path)
                return True

        return super().eventFilter(obj, event)

    # ── File attachments (drag-and-drop) ────────────────────────────────

    def _set_attachments_row_visible(self, visible: bool) -> None:
        """
        The ONLY way _attachmentsRow's visibility should ever be
        touched - see this row's construction comment for why a hidden
        widget also gets pinned to setMaximumHeight(0): without it, an
        empty attachment row could still leave a gap above the composer
        instead of the input box's outline hugging the text exactly,
        the moment the last chip is removed. clearMaximumHeight-style
        reset (16777215 is QWIDGETSIZE_MAX, Qt's own "no cap" sentinel)
        on show, so a visible row is free to size to its actual chips
        again rather than staying capped at whatever height it happened
        to have last time.
        """
        self._attachmentsRow.setMaximumHeight(16777215 if visible else 0)
        self._attachmentsRow.setVisible(visible)
        self.inputWrapperLayout.activate()

    def _set_attach_status_visible(self, visible: bool) -> None:
        """Same zero-height-when-hidden guarantee as
        _set_attachments_row_visible, for the drop-failed status line."""
        self._attachStatus.setMaximumHeight(16777215 if visible else 0)
        self._attachStatus.setVisible(visible)
        self.inputWrapperLayout.activate()

    def _handle_dropped_file(self, path: str) -> None:
        filename = os.path.basename(path)
        if file_context.is_image_file(path):
            # Real vision path, not a text-extraction attempt - see
            # ai/file_context.py's module docstring and
            # ai.main.queue_chat_images for where this actually reaches
            # the model.
            image, err = file_context.load_image_attachment(path)
            if err:
                self._attachStatus.setText(f"Couldn't attach {filename}: {err}")
                self._set_attach_status_visible(True)
                return
            self._set_attach_status_visible(False)
            self._add_attachment(filename, image=image)
            return

        text, err = file_context.extract_text_from_file(path)
        if err:
            self._attachStatus.setText(f"Couldn't attach {filename}: {err}")
            self._set_attach_status_visible(True)
            return
        self._set_attach_status_visible(False)
        self._add_attachment(filename, text=text)

    def stage_voice_transcript(self, transcript: str) -> None:
        """
        Puts a finished voice transcript in the composer for review
        instead of sending it immediately - you can see what was
        actually heard, fix the word it got wrong, or clear it and say
        nothing at all. Escape clears it outright (see eventFilter).

        The reply still comes back spoken: _pending_voice_reply carries
        the "this came from voice" flag through to whenever the message
        is actually sent, so the prompt still gets VOICE_REPLY_HINT and
        MainWindow still speaks the answer, exactly as it would have on
        the send-immediately path.
        """
        transcript = (transcript or "").strip()
        if not transcript:
            return
        self._pending_voice_reply = True
        self.chatInput.setPlainText(transcript)
        cursor = self.chatInput.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chatInput.setTextCursor(cursor)
        self.chatInput.setFocus()
        self._autosize_chat_input()
        self._update_send_enabled()

    def attach_screenshot(self, path: str) -> bool:
        """
        Stages a screenshot the user just took with the global shortcut
        as an ordinary attachment chip in the composer - thumbnail,
        name, X to remove - exactly like an image dragged onto the
        input, instead of the read-only preview bubble it used to drop
        straight into the transcript. That bubble couldn't be removed,
        couldn't be previewed before committing to it, and sat in the
        conversation as if it were a message someone had sent.

        Returns True if it was staged (the caller must then discard
        ai.main's own pending capture - see MainWindow's shortcut
        handler - or the same screenshot would be attached twice: once
        as this chip's queued ImagePart, once as that global), False if
        the file couldn't be read, leaving the caller to fall back to
        the old transcript-bubble behavior.
        """
        image, err = file_context.load_image_attachment(path)
        if err or image is None:
            print(f"[chat] Couldn't stage screenshot {path}: {err}")
            return False
        self._add_attachment(os.path.basename(path), image=image)
        return True

    def _add_attachment(self, filename: str, text: "str | None" = None, image=None) -> None:
        # Exactly one of text/image is set - an image attachment carries
        # no extracted "text" at all (see file_context's module
        # docstring on why there's no OCR step), a file attachment
        # carries no image. _send_message below branches on which one
        # each entry has to decide whether it's folded into the prompt
        # string or queued as a real ImagePart.
        entry = {"name": filename, "text": text, "image": image}
        chip = _make_attachment_chip(
            filename,
            self._theme,
            is_image=image is not None,
            # Thumbnail for anything with an image behind it - a dragged
            # picture and a screenshot taken with the global shortcut
            # both land here and both get one (see attach_screenshot).
            preview=_pixmap_from_pil(image) if image is not None else None,
        )
        entry["chip"] = chip
        btn_remove = chip.findChild(QPushButton, "attachmentRemove")
        btn_remove.clicked.connect(lambda: self._remove_attachment(entry))

        # Insert before the trailing stretch so chips stay left-aligned.
        self._attachmentsLayout.insertWidget(self._attachmentsLayout.count() - 1, chip)
        self._pending_attachments.append(entry)
        self._set_attachments_row_visible(True)
        self._update_send_enabled()

    def _remove_attachment(self, entry: dict) -> None:
        if entry not in self._pending_attachments:
            return
        self._pending_attachments.remove(entry)
        chip = entry["chip"]
        self._attachmentsLayout.removeWidget(chip)
        chip.deleteLater()
        if not self._pending_attachments:
            self._set_attachments_row_visible(False)
        self._update_send_enabled()

    def _clear_attachments(self) -> None:
        for entry in list(self._pending_attachments):
            self._remove_attachment(entry)

    # ── Private slots ──────────────────────────────────────────────────────────

    # Send is only clickable when the input isn't empty 
    def _on_text_changed(self):
        self._update_send_enabled()
        self._autosize_chat_input()
        # Measured a second time once Qt has actually laid the new text
        # out. The call above runs inside textChanged - before layout
        # so it works off the line count (see _document_pixel_height);
        # this one gets the real per-block geometry and corrects the
        # height if the two disagree, which they do whenever a line
        # wraps to a different number of visual lines than the
        # pre-layout count implied.
        QTimer.singleShot(0, self._autosize_chat_input)

    # Grow/shrink range for the composer - one line at the small end,
    # roughly 8-9 wrapped lines at the large end before it switches to
    # scrolling internally instead of growing the window further.
    _CHAT_INPUT_MIN_HEIGHT = 24
    _CHAT_INPUT_MAX_HEIGHT = 180
    # Expanded composer height, as a fraction of the chat page's own
    # height - see _build_expand_button / _current_input_cap.
    _CHAT_INPUT_EXPANDED_RATIO = 0.45
    # Vertical breathing room the input row needs around chatInput
    # itself (inputTopLayout's 12/10 margins + inputArea's 14/20)
    # inputArea's maximum height is kept at cap + this so raising the
    # cap while expanded isn't silently clipped by the .ui's own
    # 240px ceiling on that container.
    _INPUT_AREA_CHROME_HEIGHT = 70

    def _autosize_chat_input(self) -> None:
        """
        Resizes chatInput to fit however many lines it currently holds
        (wrapped or explicit newlines alike), clamped between
        _CHAT_INPUT_MIN_HEIGHT (a single line - its size at rest) and
        _CHAT_INPUT_MAX_HEIGHT (past which it scrolls internally instead
        of growing further, same as every other chat app's composer).
        Measures via the QTextDocument itself (setTextWidth to the
        viewport's current width, then reads back its laid-out height)
        rather than counting newlines - the only way to account for
        soft-wrapped lines, which don't contain a '\\n' at all.
        """
        doc = self.chatInput.document()
        viewport_width = self.chatInput.viewport().width()
        if viewport_width > 0:
            doc.setTextWidth(viewport_width)
        margins = self.chatInput.contentsMargins()
        frame = self.chatInput.frameWidth() * 2
        # _document_pixel_height, NOT doc.size().height() - chatInput is
        # a QPlainTextEdit, whose document layout reports its height in
        # LINES rather than pixels. See that function's own docstring:
        # measuring it the obvious way is what pinned this box at one
        # line forever regardless of how much was typed.
        content_height = (
            _document_pixel_height(self.chatInput)
            + margins.top() + margins.bottom() + frame + 8
        )

        cap = self._current_input_cap()
        # inputArea's own ceiling (240px, set in the .ui) would clip an
        # expanded composer, so it tracks the live cap instead.
        self.inputArea.setMaximumHeight(cap + self._INPUT_AREA_CHROME_HEIGHT)

        if self._composer_expanded:
            # A drafting area is a fixed, deliberate size - it shouldn't
            # shrink back down as you delete a paragraph mid-edit.
            new_height = cap
        else:
            new_height = max(self._CHAT_INPUT_MIN_HEIGHT, min(int(content_height), cap))

        # Offer the expand toggle only once there's enough text for it
        # to do anything - roughly two lines in.
        self._expandBtn.setVisible(
            self._composer_expanded or content_height > self._CHAT_INPUT_MIN_HEIGHT * 2
        )

        if self.chatInput.height() != new_height:
            self.chatInput.setFixedHeight(new_height)
            # Forces inputWrapper (the rounded background box around
            # chatInput/mic/send - what actually reads as "the input
            # outline" to look at) to re-query its layout's sizeHint
            # immediately, so its outline always tracks chatInput's real
            # height on the same pass rather than potentially lagging a
            # frame behind it.
            self.inputWrapperLayout.activate()
            self.inputWrapper.updateGeometry()
        # Against the LIVE cap, not the collapsed constant - an expanded
        # composer with room to spare shouldn't show a scrollbar, and a
        # collapsed one that's full should.
        at_cap = content_height > cap
        self.chatInput.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if at_cap else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def _update_send_enabled(self):
        has_text = bool(self.chatInput.toPlainText().strip())
        has_attachments = bool(self._pending_attachments)
        self.sendBtn.setEnabled(has_text or has_attachments)

    def _dismiss_pending_confirmation(self) -> None:
        """
        Removes an unanswered accept/decline card (see show_confirmation)
        from view - called right before a new message is sent while one
        is still showing. Previously the card just stayed on screen
        forever once superseded by whatever the user typed instead of
        answering it; moving on to a new message is a clear enough
        signal the suggestion isn't being acted on. Purely a UI cleanup
        - doesn't emit confirmation_response, since nothing on the
        backend depends on every suggestion eventually getting an
        explicit answer (an unanswered one just never starts autonomous
        mode, which is already the safe/correct default).
        """
        if self._pending_confirmation_row is None:
            return
        row = self._pending_confirmation_row
        self._pending_confirmation_row = None
        row.deleteLater()

    def clear_confirmation_card(self, chat_id: int) -> None:
        """
        Take the accept/decline card away once the run it started has
        finished.

        It used to stay in the conversation permanently, reading
        "Accepted - starting…" long after the task was over - a stale
        prompt for a decision already made, sitting in the transcript
        under the reply it produced.

        Only touches the card for `chat_id`'s own conversation, and only
        when that conversation is the one on screen; switching chats
        rebuilds the message list from the database anyway, and the card
        was never in it.
        """
        if chat_id != self._current_chat_id:
            self._answered_confirmation_row = None
            return
        row = self._answered_confirmation_row
        self._answered_confirmation_row = None
        self._pending_confirmation_row = None
        if row is None:
            return
        try:
            row.deleteLater()
        except RuntimeError:
            pass          # already gone with the chat that held it

    def _send_message(self, voice_reply: bool = False):
        """
        voice_reply=True marks this message as voice-originated (see
        MainWindow._on_voice_transcript_ready, which writes the mic's
        transcript into chatInput and calls this) - appends
        VOICE_REPLY_HINT to the PROMPT sent to the AI, asking for a short,
        speech-friendly reply, without changing what's shown in the
        bubble or saved to the DB (the user never said the hint, so it's
        not part of their message).
        """
        text = self.chatInput.toPlainText().strip()
        if (not text and not self._pending_attachments) or self._current_chat_id is None:
            return
        # A transcript staged for review (stage_voice_transcript) is
        # still a voice message when it's finally sent by hand, so the
        # flag it left behind is honored here and cleared either way
        # including when this send isn't the staged one at all, since
        # the composer having been retyped means the voice draft is
        # gone.
        if self._pending_voice_reply:
            voice_reply = True
            self._pending_voice_reply = False
        self._dismiss_pending_confirmation()

        # What the user typed (or a fallback if they only dropped files) is
        # what gets shown/saved as the message - the attached files'
        # actual content is folded into the prompt sent to the AI below,
        # not dumped into the visible/persisted chat bubble, to keep the
        # conversation readable and the DB from ballooning on a large PDF.
        display_text = text or f"Sent {len(self._pending_attachments)} file(s)."

        prompt = text
        image_attachments = []
        for entry in self._pending_attachments:
            if entry.get("image") is not None:
                image_attachments.append(entry["image"])
                continue
            block = file_context.format_attachment_block(entry["name"], entry["text"])
            prompt = f"{prompt}\n\n{block}" if prompt else block
        if image_attachments:
            # Real ImageParts for the next ai()/ai_stream() call, not
            # text - see ai.main.queue_chat_images / this class's own
            # _handle_dropped_file for the image-attachment path.
            ai_main.queue_chat_images(image_attachments)
        if voice_reply:
            prompt = f"{prompt}{VOICE_REPLY_HINT}"

        self.chatInput.clear()
        self._clear_attachments()
        self._set_attach_status_visible(False)
        self.sendBtn.setEnabled(False)
        if self._composer_expanded:
            # The draft it was expanded for has been sent - an empty
            # composer shouldn't keep half the window.
            self._toggle_composer_expanded()

        # Save to the DB + display immediately
        new_id = db.add_message(self._current_chat_id, "user", display_text)
        user_row = self._append_bubble(display_text, "user", message_id=new_id)
        # Deferred a tick rather than recentering inline: _clear_attachments()
        # a few lines up just collapsed the chip row, so the input area (and
        # with it the messages viewport) is still resizing at this point, and
        # the row that was appended one line ago has no real geometry yet.
        # See _recenter_on's own comment - reading either too early is what
        # sent the view to the top of the conversation on send.
        QTimer.singleShot(0, lambda: self._recenter_on(user_row))
        log_input(display_text)

        # Automatic title on the chat's 1st message
        if db.get_message_count(self._current_chat_id) == 1:
            self._name_chat_from(display_text)
        else:
            self.new_message_sent.emit(self._current_chat_id)

        # AI reply - gets the full prompt (typed/spoken text + attached
        # file content + the voice hint, if any); the bubble/DB only ever
        # got display_text, above. from_voice lets MainWindow speak the
        # reply back and mark any autonomous-mode suggestion it produces
        # as voice-originated too.
        if self._switch_in_flight:
            # A profile switch is still rebuilding the chat session in
            # the background (see _on_profile_picker_changed). The
            # message is already in the transcript and the DB - only
            # the handoff waits, so it isn't answered by a session
            # that's about to be replaced. _flush_deferred_send emits
            # this the moment the new one is live.
            self._deferred_send = (self._current_chat_id, prompt, voice_reply)
            return
        self.request_sent.emit(self._current_chat_id, prompt, voice_reply)

    def _on_mic_clicked(self):
        if self._current_chat_id is None:
            return
        if self._mic_state == "speaking":
            # Kestrel is talking - a click here means "stop", not "start a
            # new recording". This is the actual interrupt: playback is
            # the one phase that can be cut off cleanly and immediately
            # (see MainWindow._on_mic_stop_speaking_requested).
            self.mic_stop_speaking_requested.emit(self._current_chat_id)
            return
        if self._mic_state in ("listening", "preparing"):
            # Recording (or still setting up the mic - see "preparing"
            # below) - this now genuinely cancels it (see
            # VoiceWorker.request_cancel and MainWindow._on_mic_cancel_requested)
            # instead of being a no-op with the button disabled. Previously
            # there was no way to back out of an accidental/unwanted
            # recording short of waiting out the full silence timeout.
            self.mic_cancel_requested.emit(self._current_chat_id)
            return
        self.mic_requested.emit(self._current_chat_id)

    # ── Read aloud ────────────────────────────────────────────────────────
    # Deliberately separate from the mic state below. These are two
    # different features that happen to both make sound: the mic button
    # is a conversation, Read aloud is a button on one message. They used
    # to share _mic_state, which meant starting a Read aloud turned the
    # mic red and made it refuse to record.

    def _on_speak_clicked(self, label) -> None:
        """Start reading this message, or stop it if it is the one being
        read. Clicking a DIFFERENT message while one is being read
        switches to it, which is what ai.main._begin_speech already does
        by superseding the running utterance."""
        if self._speaking_bubble is label:
            self.stop_read_aloud_requested.emit(self._current_chat_id)
            return
        self._pending_speak_bubble = label
        self.speak_requested.emit(self._current_chat_id, _bubble_plain_text(label))

    def set_speaking_message(self, chat_id: int, speaking: bool) -> None:
        """
        Record which message is being read aloud, so its own button can
        offer to stop it.

        Called from MainWindow's read-aloud callbacks. The bubble is
        resolved from the click that started it rather than passed in,
        because the worker only knows the chat and the text.
        """
        if not speaking:
            previous, self._speaking_bubble = self._speaking_bubble, None
            self._sync_speak_button(previous, speaking=False)
            return
        self._speaking_bubble = self._pending_speak_bubble
        self._sync_speak_button(self._speaking_bubble, speaking=True)

    def _sync_speak_button(self, label, speaking: bool) -> None:
        """
        Swap the icon and text on whichever row owns `label`.

        Walks up from the bubble to the first ancestor that actually has
        a msgSpeakBtn, rather than matching a hardcoded row objectName -
        the row's name is not guaranteed and the button is what this
        needs anyway.

        Silently does nothing if the row has gone: the chat can be
        switched, cleared or rebuilt by a theme change while audio is
        still playing, and a stale reference must not take down the
        callback that arrives afterwards.
        """
        if label is None:
            return
        try:
            button = None
            node = label.parentWidget()
            while node is not None and button is None:
                button = node.findChild(QPushButton, "msgSpeakBtn")
                node = node.parentWidget()
            if button is None:
                return
            c = theme.colors(self._theme)
            button.setIcon(icons.icon("stop" if speaking else "speaker", c, "text_disabled"))
            button.setText(tr(" Stop") if speaking else tr(" Read aloud"))
        except RuntimeError:
            return          # the row was destroyed underneath us

    def set_mic_state(self, state: str) -> None:
        """Called by main_window as the VoiceWorker moves through its
        preparing → listening → processing → speaking lifecycle. `state`
        is one of 'idle', 'preparing', 'listening', or 'speaking' - see
        _on_mic_clicked for what each one means for the button's own
        click handling."""
        self._mic_state = state
        c = theme.colors(self._theme)
        if state == "speaking":
            self.micBtn.setEnabled(True)
            self.micBtn.setToolTip(f"Click to stop {self._display_assistant_name()}")
            self.micBtn.setIcon(icons.icon("mic", c, "danger"))
        elif state == "preparing":
            # Mic/recognizer setup + ambient-noise calibration in
            # progress - deliberately NOT the same red "Listening" icon
            # as the real capture state below, so nothing invites the
            # person to start talking before the microphone is actually
            # recording (see MainWindow._on_mic_requested's own comment
            # on why this state exists at all).
            self.micBtn.setEnabled(True)
            self.micBtn.setToolTip(tr("Getting ready… click to cancel"))
            self.micBtn.setIcon(icons.icon("mic", c, "text_muted"))
        elif state == "listening":
            self.micBtn.setEnabled(True)
            self.micBtn.setToolTip(tr("Listening… click to cancel"))
            self.micBtn.setIcon(icons.icon("mic", c, "danger"))
        else:
            self.micBtn.setEnabled(True)
            self.micBtn.setToolTip(f"Talk to {self._display_assistant_name()}")
            self.micBtn.setIcon(icons.icon("mic", c, "text_muted"))

    def _append_bubble(self, text: str, role: str, message_id: int | None = None, is_leaf: bool = False):
        stripped_text = text.strip()
        screenshot_match = SCREENSHOT_MARKER_RE.match(stripped_text)
        system_match = SYSTEM_MARKER_RE.match(stripped_text)
        terminal_match = TERMINAL_MARKER_RE.match(stripped_text)
        file_match = FILE_MARKER_RE.match(stripped_text)
        if screenshot_match:
            row = _make_screenshot_row(screenshot_match.group(1), self._theme)
        elif system_match:
            row = _make_system_row(system_match.group(1), system_match.group(2), self._theme)
        elif terminal_match:
            try:
                payload = decode_terminal_marker(terminal_match.group(1))
                row = _make_terminal_row(payload["command"], payload["output"], payload["success"], self._theme)
            except Exception:
                # Corrupted/truncated marker (e.g. a hand-edited or
                # partially-migrated DB row) - fall back to a plain
                # bubble showing the raw marker text rather than crash
                # the whole chat load over one bad row.
                row = _make_row(text, role, self._theme, message_id=message_id)
                self._wire_message_actions(row, role, message_id, is_leaf=is_leaf)
        elif file_match:
            try:
                payload = decode_file_marker(file_match.group(1))
                row = _make_file_row(payload["path"], payload.get("action", "created"), self._theme)
            except Exception:
                # Same corrupted-marker fallback as the terminal branch above.
                row = _make_row(text, role, self._theme, message_id=message_id)
                self._wire_message_actions(row, role, message_id, is_leaf=is_leaf)
        else:
            row = _make_row(text, role, self._theme, message_id=message_id)
            self._wire_message_actions(row, role, message_id, is_leaf=is_leaf)
        self._insert_before_stretch(row)
        return row

    def _wire_message_actions(self, row: QWidget, role: str, message_id: int | None, is_leaf: bool = False) -> None:
        """
        Connects the Copy/Read-aloud/Edit/Regenerate buttons _make_row
        built into `row`. Reads the bubble's CURRENT label text at
        click-time (not whatever text was passed in when the row was
        built) so Copy and Read aloud both work correctly on an
        assistant bubble that's still mid-typewriter-reveal
        (_reveal_reply) when clicked.

        `is_leaf` (only meaningful for role=="assistant") is whether
        this message is the chat's current LAST message - see
        load_chat's call site (the last item of db.get_messages'
        active-thread walk) - and gates the Regenerate button:
        regenerating anything OTHER than the actual last reply would
        silently resurrect whatever branch followed it (see
        MainWindow._on_regenerate_requested's docstring), so it's
        simply not offered there. A reply that just finished
        streaming in over the wire doesn't have is_leaf known at
        construction time (see reveal_response/begin_streaming_response,
        which build with is_leaf defaulted False since no message_id
        exists yet either) - attach_message_id enables it after the
        fact once the DB write settles that question.
        """
        label = row.findChild(QWidget, "bubbleUser" if role == "user" else "bubbleAi")
        if label is None:
            return

        btn_copy = row.findChild(QPushButton, "msgCopyBtn")
        if btn_copy is not None:
            btn_copy.clicked.connect(lambda: self._on_copy_clicked(btn_copy, label))

        if role == "assistant":
            btn_speak = row.findChild(QPushButton, "msgSpeakBtn")
            if btn_speak is not None:
                # A toggle, not a fire-and-forget. Read aloud used to be
                # stoppable only through the MIC button, because it put
                # the mic into "speaking" state and borrowed its Stop -
                # which is exactly the coupling that had to go. Its own
                # button is where stopping it belongs.
                #
                # Keyed on the bubble widget rather than a message id:
                # the id is not known yet for a reply still streaming in
                # (see attach_message_id), and the bubble is stable for
                # the row's whole life.
                btn_speak.clicked.connect(
                    lambda _=False, lbl=label: self._on_speak_clicked(lbl)
                )

            btn_regenerate = row.findChild(QPushButton, "msgRegenerateBtn")
            if btn_regenerate is not None:
                if message_id is not None and is_leaf:
                    btn_regenerate.setEnabled(True)
                    btn_regenerate.clicked.connect(lambda: self._on_regenerate_clicked(message_id))
                else:
                    btn_regenerate.setEnabled(False)
                    btn_regenerate.setToolTip(tr("Only the latest reply can be regenerated"))
        else:
            btn_edit = row.findChild(QPushButton, "msgEditBtn")
            if btn_edit is not None:
                if message_id is None:
                    # Not-yet-persisted or historical row we couldn't
                    # attach an id to - nothing safe to delete-from, so
                    # don't offer an edit that can't actually work.
                    btn_edit.setEnabled(False)
                    btn_edit.setToolTip(tr("Can't edit this message"))
                else:
                    btn_edit.clicked.connect(lambda: self._start_edit(row, message_id))

            if message_id is not None:
                self._wire_branch_switcher(row, message_id)

            btn_cancel = row.findChild(QPushButton, "editCancelBtn")
            if btn_cancel is not None:
                btn_cancel.clicked.connect(lambda: self._cancel_edit(row))
            btn_save = row.findChild(QPushButton, "editSaveBtn")
            if btn_save is not None and message_id is not None:
                btn_save.clicked.connect(lambda: self._confirm_edit(row, message_id))

    def _wire_branch_switcher(self, row: QWidget, message_id: int) -> None:
        """
        Adds a "‹ 2 / 3 ›" selector into a user bubble's action row when
        it has more than one sibling (db.list_siblings) - i.e. it's been
        edited into a new branch at least once (see db.branch_message).
        Only ever meaningful for user messages: under this branching
        model an assistant reply is generated exactly once per branch -
        the reply itself is never what gets edited - so it never has
        siblings; _wire_message_actions only calls this for role=="user".
        """
        siblings = db.list_siblings(message_id)
        if len(siblings) <= 1:
            return
        actions_container = row.findChild(QWidget, "msgActionsRow")
        actions_row = actions_container.layout() if actions_container is not None else None
        if actions_row is None:
            return

        sibling_ids = [s["id"] for s in siblings]
        position = sibling_ids.index(message_id)  # 0-based
        c = theme.colors(self._theme)

        switcher = QWidget()
        switcher_row = QHBoxLayout(switcher)
        switcher_row.setContentsMargins(0, 0, 0, 0)
        switcher_row.setSpacing(0)

        btn_prev = QPushButton("‹")
        btn_prev.setObjectName("branchPrevBtn")
        btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_prev.setStyleSheet(_msg_action_btn_style(c))
        btn_prev.setEnabled(position > 0)
        if position > 0:
            btn_prev.clicked.connect(lambda: self._on_switch_branch(sibling_ids[position - 1]))
        switcher_row.addWidget(btn_prev)

        position_label = QLabel(f"{position + 1}/{len(sibling_ids)}")
        position_label.setObjectName("branchPositionLabel")
        position_label.setStyleSheet(
            f"color:{c['text_disabled']}; font-size:11px; background:transparent; padding:0 2px;"
        )
        switcher_row.addWidget(position_label)

        btn_next = QPushButton("›")
        btn_next.setObjectName("branchNextBtn")
        btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_next.setStyleSheet(_msg_action_btn_style(c))
        btn_next.setEnabled(position < len(sibling_ids) - 1)
        if position < len(sibling_ids) - 1:
            btn_next.clicked.connect(lambda: self._on_switch_branch(sibling_ids[position + 1]))
        switcher_row.addWidget(btn_next)

        # Index 1, not 0 - index 0 in a user row's actions_row is the
        # leading addStretch() (see _make_row: "actions_row.addStretch();
        # actions_row.addWidget(btn_copy); ..."), so this lands right
        # after the stretch and before Copy/Edit - inside the same
        # right-aligned cluster those sit in, not off on its own margin.
        actions_row.insertWidget(1, switcher)

    def _on_switch_branch(self, sibling_message_id: int) -> None:
        """Makes `sibling_message_id` the active branch at its position
        (db.switch_branch) and reloads the chat to show it - same
        "just reload everything" approach load_chat's every other
        caller already uses rather than trying to patch the view in
        place."""
        if self._current_chat_id is None:
            return
        db.switch_branch(self._current_chat_id, sibling_message_id)
        self.load_chat(self._current_chat_id)

    def _on_copy_clicked(self, btn_copy: QPushButton, label: QLabel) -> None:
        """
        Copies the bubble's text, then briefly flips the button to "
        Copied" - clicking it previously gave no acknowledgement at all
        that anything happened (the clipboard write is instant and
        silent), which read as unresponsive even though it worked.
        Restores the original text/icon after a beat rather than leaving
        it changed, so a second copy still shows the same confirmation.
        """
        QApplication.clipboard().setText(_bubble_plain_text(label))

        original_text = btn_copy.text()
        original_icon = btn_copy.icon()
        c = theme.colors(self._theme)
        btn_copy.setText(tr(" Copied"))
        btn_copy.setIcon(icons.icon("check", c, "text_disabled"))
        btn_copy.setEnabled(False)

        def _restore():
            btn_copy.setText(original_text)
            btn_copy.setIcon(original_icon)
            btn_copy.setEnabled(True)

        QTimer.singleShot(1200, _restore)

    # ── Editing a user message (ChatGPT-style: inline, not the compose box) ──

    def _start_edit(self, row: QWidget, message_id: int) -> None:
        """
        Switches `row`'s bubble from display to edit mode in place -
        pre-filled with its current text - and hides (NOT deletes)
        every message after it, since the AI's reply to the un-edited
        question is about to stop being the right answer and shouldn't
        keep sitting there while the user decides what to change. Nothing
        is touched in the DB yet; Cancel (_cancel_edit) puts everything
        back exactly as it was. Only one message can be mid-edit at a
        time - starting a new edit cancels whichever one was already
        active.
        """
        if self._active_edit_row is not None and self._active_edit_row is not row:
            self._cancel_edit(self._active_edit_row)
        self._active_edit_row = row

        label = row.findChild(QLabel, "bubbleUser")
        edit_container = row.findChild(QWidget, "editContainer")
        actions_row = row.findChild(QWidget, "msgActionsRow")
        edit_input = row.findChild(QTextEdit, "editTextEdit")
        if None in (label, edit_container, actions_row, edit_input):
            return

        edit_input.setPlainText(_bubble_plain_text(label))
        label.setVisible(False)
        actions_row.setVisible(False)
        # A long message's "Show more" toggle goes with the bubble it
        # belongs to - the editor below holds the complete text either
        # way, so a collapse control floating over it would do nothing
        # but confuse.
        expand_btn = row.findChild(QPushButton, "msgExpandBtn")
        if expand_btn is not None:
            expand_btn.setVisible(False)
        edit_container.setVisible(True)
        edit_input.setFocus()
        cursor = edit_input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        edit_input.setTextCursor(cursor)
        # Re-measure now that it's actually visible - before this,
        # viewport().width() reads 0 (or stale), which would size it
        # wrong on the very first open.
        QTimer.singleShot(0, lambda: _autosize_text_edit(edit_input))

        self._hidden_rows_during_edit = self._rows_after(row)
        for hidden_row in self._hidden_rows_during_edit:
            hidden_row.setVisible(False)

    def _cancel_edit(self, row: QWidget) -> None:
        """Reverts `row` to display mode with its ORIGINAL text (untouched - edit mode only ever wrote into the separate editTextEdit) and un-hides whatever _start_edit hid. No DB changes to undo, since none were made."""
        label = row.findChild(QLabel, "bubbleUser")
        edit_container = row.findChild(QWidget, "editContainer")
        actions_row = row.findChild(QWidget, "msgActionsRow")
        if None not in (label, edit_container, actions_row):
            edit_container.setVisible(False)
            label.setVisible(True)
            actions_row.setVisible(True)
            expand_btn = row.findChild(QPushButton, "msgExpandBtn")
            if expand_btn is not None:
                expand_btn.setVisible(True)  # see _start_edit, which hid it

        if self._active_edit_row is row:
            for hidden_row in self._hidden_rows_during_edit:
                hidden_row.setVisible(True)
            self._hidden_rows_during_edit = []
            self._active_edit_row = None

    def _confirm_edit(self, row: QWidget, message_id: int) -> None:
        """
        Commits the edit as a NEW BRANCH (db.branch_message) rather than
        deleting this message and everything after it - the original
        message and whatever it led to stay in the DB as a switchable-
        back-to inactive branch (see db.switch_branch / the ‹ › selector
        _wire_branch_switcher adds next to a bubble with more than one
        sibling). Reloads to show the new branch, then triggers a fresh
        AI reply to the edited text directly (request_sent) - NOT
        through _send_message, since branch_message already persisted
        the edited user message itself; calling _send_message too would
        insert it a second time.
        """
        if self._current_chat_id is None:
            return
        edit_input = row.findChild(QTextEdit, "editTextEdit")
        if edit_input is None:
            return
        new_text = edit_input.toPlainText().strip()
        if not new_text:
            return  # nothing to resend - same as trying to send an empty message normally

        self._hidden_rows_during_edit = []
        self._active_edit_row = None

        db.branch_message(self._current_chat_id, message_id, new_text)
        self.load_chat(self._current_chat_id)

        # Same "did this just become/stay the chat's opening message"
        # bookkeeping _send_message always does for a normal send - an
        # edited first message still needs the auto-title recomputed
        # from its new text, and either way the sidebar needs telling
        # so this chat rises back to the top of recency there too
        # (get_message_count/db's own updated_at bump only cover the DB
        # side of that, not the sidebar's live-in-memory list).
        if db.get_message_count(self._current_chat_id) == 1:
            self._name_chat_from(new_text)
        else:
            self.new_message_sent.emit(self._current_chat_id)

        self.request_sent.emit(self._current_chat_id, new_text, False)

    def _name_chat_from(self, message: str) -> None:
        """
        Title the chat from its opening message: the truncated text now,
        and a title describing what was actually asked a moment later.

        Provisional first because the sidebar row needs a label
        immediately, and a chat briefly called "New chat" while a network
        call lands reads as broken. The model's answer only replaces it
        if the title is still the provisional one, so a rename typed in
        the meantime wins.
        """
        import ai.main as ai_main

        chat_id = self._current_chat_id
        provisional = ai_main.fallback_chat_title(message)
        db.update_chat_title(chat_id, provisional)
        self.chatTitle.setText(provisional)
        self.chat_title_changed.emit(chat_id, provisional)

        # No word-count guard here. suggest_chat_title already decides
        # cheaply whether a message is worth a call, and it knows about
        # gibberish, which a word count does not: "aazeeeeeeeeeeeee" is
        # one word and needs the model more than a real sentence does.

        self._title_thread = QThread()
        self._title_worker = TitleWorker()
        self._title_worker.moveToThread(self._title_thread)
        self._title_thread.started.connect(
            lambda: self._title_worker.start.emit(chat_id, message)
        )
        self._title_worker.finished.connect(
            lambda cid, title, was=provisional: self._on_title_ready(cid, title, was)
        )
        self._title_worker.finished.connect(self._title_thread.quit)
        self._title_worker.finished.connect(self._title_worker.deleteLater)
        self._title_thread.finished.connect(self._title_thread.deleteLater)
        self._title_thread.start()

    def _on_title_ready(self, chat_id: int, title: str, provisional: str) -> None:
        if not title or title == provisional:
            return
        current = db.get_chat_title(chat_id)
        if current is not None and current != provisional:
            return  # renamed while the model was thinking; that wins
        db.update_chat_title(chat_id, title)
        if self._current_chat_id == chat_id:
            self.chatTitle.setText(title)
        self.chat_title_changed.emit(chat_id, title)

    def _rows_after(self, row: QWidget) -> list[QWidget]:
        """Every message row below `row` in the layout - used by _start_edit to know what to hide."""
        index = self._messages_layout.indexOf(row)
        if index == -1:
            return []
        rows = []
        for i in range(index + 1, self._messages_layout.count()):
            item = self._messages_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                rows.append(widget)
        return rows

    # ── Regenerating an assistant reply ───────────────────────────────

    def _last_message_row(self) -> "QWidget | None":
        """The last actual message row in the layout - everything ever
        gets inserted before the single trailing stretch _insert_before_
        stretch keeps at the end (added once in __init__), so that
        stretch item's own `.widget()` is None and gets skipped here.
        Used by attach_message_id to find the bubble a just-finished
        reply belongs to."""
        for i in range(self._messages_layout.count() - 1, -1, -1):
            item = self._messages_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                return widget
        return None

    def _on_regenerate_clicked(self, message_id: int) -> None:
        if self._current_chat_id is None:
            return
        self.regenerate_requested.emit(self._current_chat_id, message_id)

    def attach_message_id(self, chat_id: int, message_id: int) -> None:
        """
        Called once by MainWindow right after a fresh reply is
        persisted (db.add_message's returned id) and its bubble has
        already been built (reveal_response / finish_streaming_response
        already ran) - those two build the bubble with message_id=None
        and is_leaf=False (see their own _append_bubble("", "assistant")
        calls), since neither knows the DB id or the leaf status until
        AFTER the AI actually replies. This retroactively:

          1. Disables any OTHER row's Regenerate button that's still
             enabled from earlier in this same live session - it's no
             longer the chat's last message now that this new reply
             exists, and regenerating it would silently resurrect
             whatever branch this new reply now supersedes (see
             MainWindow._on_regenerate_requested's docstring).
          2. Stamps message_id onto the new reply's own row and enables
             + wires ITS Regenerate button.

        A no-op if chat_id isn't the chat currently showing - same
        guard reveal_response/begin_streaming_response already use,
        for the same reason (the reply still saved to the DB
        regardless; there's just no row here to attach anything to).
        """
        if self._current_chat_id != chat_id:
            return

        for btn in self.findChildren(QPushButton, "msgRegenerateBtn"):
            if btn.isEnabled():
                btn.setEnabled(False)
                btn.setToolTip(tr("Only the latest reply can be regenerated"))
                try:
                    btn.clicked.disconnect()
                except TypeError:
                    pass  # nothing was connected - harmless

        row = self._last_message_row()
        if row is None:
            return
        row.setProperty("message_id", message_id)
        btn_regenerate = row.findChild(QPushButton, "msgRegenerateBtn")
        if btn_regenerate is not None:
            btn_regenerate.setEnabled(True)
            btn_regenerate.setToolTip("")
            btn_regenerate.clicked.connect(lambda: self._on_regenerate_clicked(message_id))

    def show_confirmation(self, chat_id: int, goal: str, voice_hint: bool = False) -> None:
        """
        Shows an inline accept/decline card for an autonomous-mode
        suggestion, right in the chat, so the user can see what's about
        to run before agreeing - instead of a modal popup. Only one card
        is ever "live" at a time: answering an older one (by any means)
        is a no-op if a newer one has since replaced it as pending.
        `voice_hint` shows the "say accept/refuse" line - set by
        main_window only when the request that led to this suggestion
        itself came from voice mode (see MainWindow._on_autonomous_suggested).
        Emits confirmation_response once answered, by click or by voice.
        """
        row = _make_confirmation_row(goal, voice_hint, self._theme)
        self._pending_confirmation_row = row
        # Also held after it is answered: _pending_ is cleared the moment
        # the user responds, but the card itself stays on screen showing
        # "Accepted - starting…" and there was nothing left holding it to
        # take it away again when the run ended.
        self._answered_confirmation_row = row
        btn_accept = row.findChild(QPushButton, "confirmAccept")
        btn_cancel = row.findChild(QPushButton, "confirmCancel")

        def _respond(accepted: bool) -> None:
            if self._pending_confirmation_row is not row:
                return
            self._pending_confirmation_row = None
            btn_accept.setEnabled(False)
            btn_cancel.setEnabled(False)
            hint = row.findChild(QLabel, "confirmHint")
            if hint is not None:
                hint.setText("Accepted - starting…" if accepted else "Declined")
            self.confirmation_response.emit(chat_id, accepted)

        btn_accept.clicked.connect(lambda: _respond(True))
        btn_cancel.clicked.connect(lambda: _respond(False))
        self._insert_before_stretch(row)
        QTimer.singleShot(30, self._scroll_to_bottom)

    def respond_to_pending_confirmation(self, accepted: bool) -> bool:
        """
        Called by main_window when the wake-word listener hears "accept"
        or "refuse" while a confirmation card is showing. Just clicks the
        matching button so the exact same path (and the exact same
        confirmation_response emission) runs either way. Returns False if
        there's nothing pending, so the caller knows the word didn't
        actually apply to anything.
        """
        if self._pending_confirmation_row is None:
            return False
        row = self._pending_confirmation_row
        button = row.findChild(QPushButton, "confirmAccept" if accepted else "confirmCancel")
        if button is not None:
            button.click()
        return True

    def note_pending_human_handoff(self, chat_id: int, reason: str) -> None:
        """
        Records that `chat_id` has an unresolved human handoff, without
        building the visual card - used when the handoff happens on a
        chat that isn't currently open, so load_chat can still re-attach
        a working Continue card whenever the user switches to it later.
        show_human_handoff (for the chat that IS currently open) already
        does this itself; this is the same bookkeeping for the "not
        currently open" case.
        """
        self._pending_handoff_chat_id = chat_id
        self._pending_handoff_reason = reason

    def clear_pending_human_handoff(self, chat_id: int) -> None:
        """
        Called once an autonomous run for `chat_id` is done (finished,
        errored, or stopped) - drops the tracked pending-handoff state so
        a later reload of this chat doesn't try to re-attach a Continue
        card for a run that isn't waiting on anything anymore.
        """
        if self._pending_handoff_chat_id == chat_id:
            self._pending_handoff_chat_id = None
            self._pending_handoff_reason = None

    def show_human_handoff(self, chat_id: int, reason: str) -> None:
        """
        Tier 2 of the automation pipeline: shows the "Continue" card for
        a paused autonomous run waiting on a human to resolve a
        CAPTCHA/MFA/verification challenge (see
        ai.actions.tool_request_human_handoff / MainWindow._on_human_handoff_needed).
        Emits human_handoff_response once the human clicks Continue.
        Safe to call again for the same still-pending handoff (e.g. from
        load_chat re-attaching it after a reload) - updates the tracked
        state either way.
        """
        self._pending_handoff_chat_id = chat_id
        self._pending_handoff_reason = reason
        row = _make_human_handoff_row(reason, self._theme)
        self._pending_handoff_row = row
        btn_continue = row.findChild(QPushButton, "handoffContinue")

        def _respond() -> None:
            if self._pending_handoff_row is not row:
                return
            self._pending_handoff_row = None
            self._pending_handoff_chat_id = None
            self._pending_handoff_reason = None
            btn_continue.setEnabled(False)
            hint = row.findChild(QLabel, "handoffHint")
            if hint is not None:
                hint.setText(tr("Continuing…"))
            self.human_handoff_response.emit(chat_id)

        btn_continue.clicked.connect(_respond)
        self._insert_before_stretch(row)
        QTimer.singleShot(30, self._scroll_to_bottom)

    def reveal_response(self, chat_id, full_text: str, chars_per_tick: int = 3, interval_ms: int = 15):
        """
        Custom "typewriter" reveal for the assistant's reply. The full
        response is already in hand at this point (one plain ai() call,
        no network streaming involved) - this just displays it
        progressively for a similar visual effect, a few characters at a
        time on a local QTimer. Entirely local: nothing here depends on
        the Gemini SDK supporting (or not supporting) streaming.
        """
        if self._current_chat_id != chat_id:
            return
        self._hide_typing(chat_id)
        row = self._append_bubble("", "assistant")
        container = row.findChild(QWidget, "bubbleAi")
        if container is None:
            return
        c = theme.colors(self._theme)
        # populate_ai_message("") above (inside _append_bubble → _make_row)
        # left exactly one placeholder QLabel in the container - that's
        # what gets updated cheaply on every tick below instead of
        # rebuilding the whole container's widgets from scratch 60+ times
        # a second; the container only gets its FINAL, proper multi-widget
        # render (real code-block widgets included) once the whole reply
        # is done revealing, below.
        inner_label = container.layout().itemAt(0).widget()
        if not full_text:
            populate_ai_message(container, full_text, c)
            self._recenter_on(row)
            return

        state = {"pos": 0}
        timer = QTimer(self)
        self._reveal_timers.add(timer)

        def tick():
            try:
                pos = state["pos"]
                if pos >= len(full_text):
                    timer.stop()
                    timer.deleteLater()
                    self._reveal_timers.discard(timer)
                    return
                end = min(pos + chars_per_tick, len(full_text))
                partial = full_text[:end]
                set_streaming_bubble_text(inner_label, partial)
                container.setProperty("raw_text", partial)
                state["pos"] = end
                # A word-wrapped QLabel growing via rapid setText() calls
                # (every interval_ms, inside a scroll area) can have its
                # own height fall behind how many lines it now actually
                # needs - Qt normally defers that recomputation to the
                # next event-loop pass, and a tight timer loop can outrun
                # it, visually clipping the last line or two of the
                # FINAL setText() even though label.text() genuinely
                # holds the complete string (this was the actual cause
                # of the reply looking cut off mid-sentence - the text
                # itself was never truncated, only its displayed height
                # was stale). label.updateGeometry() forces that back in
                # sync before _recenter_on reads the row's height below
                # (which does its own row.updateGeometry() +
                # layout.activate(), but needs the label's own geometry
                # to already be current first).
                inner_label.updateGeometry()
                if end >= len(full_text):
                    # Done - upgrade from the plain interim label to the
                    # real, final render (proper code-block widgets and
                    # all - see populate_ai_message's own docstring).
                    populate_ai_message(container, full_text, c)
                    # Finished, so it can be capped now - never during
                    # the reveal above, which would look like it stopped
                    # short. See _install_assistant_collapse.
                    _install_assistant_collapse(row, container, full_text, c)
                self._recenter_on(row)
            except RuntimeError:
                # The label (or the whole row) was deleted mid-reveal
                # chat got switched away and its widgets torn down. Stop
                # quietly rather than raising into the Qt event loop.
                timer.stop()
                timer.deleteLater()
                self._reveal_timers.discard(timer)

        timer.timeout.connect(tick)
        timer.start(interval_ms)

    def _stop_reveal_timers(self):
        for timer in list(self._reveal_timers):
            timer.stop()
            timer.deleteLater()
        self._reveal_timers.clear()

    # ── Extended thinking (Settings > Extended thinking) ──────────────
    # Counterpart to the streaming-response block below, for the
    # collapsible "Thinking…" section claude.ai itself shows above a
    # Claude reply - see AIWorker.thinking_chunk / MainWindow's
    # _on_ai_thinking_chunk for how these get called, and
    # ai/providers/anthropic_provider.py for where the thinking text
    # actually comes from. A turn with thinking off (or a non-Claude
    # provider) simply never calls any of these - the answer bubble
    # opens exactly as it always did.

    def begin_thinking_block(self, chat_id) -> None:
        """
        Called on the FIRST thinking delta for a turn: hides the typing
        indicator (same as begin_streaming_response - the first visible
        sign of life for this reply might be thinking, not answer text)
        and opens an empty, expanded thinking row that
        append_thinking_chunk() grows piece by piece.
        """
        if self._current_chat_id != chat_id:
            return
        self._hide_typing(chat_id)
        row = _make_thinking_row(self._theme)
        header = row.findChild(QPushButton, "thinkingHeader")
        body = row.findChild(QLabel, "thinkingBody")
        if header is None or body is None:
            return
        self._insert_before_stretch(row)
        self._thinking_chat_id = chat_id
        self._thinking_row = row
        self._thinking_header = header
        self._thinking_body = body
        self._thinking_text = ""
        self._thinking_started_at = time.monotonic()
        QTimer.singleShot(30, lambda: self._recenter_on(row))

    def append_thinking_chunk(self, chat_id, delta: str) -> None:
        """
        Appends one piece of streamed thinking text (see
        AIWorker.thinking_chunk) to the block opened by
        begin_thinking_block(). No-ops quietly if thinking for isn't
        the one currently showing - same "switched chats mid-stream"
        guard as append_stream_chunk().
        """
        if self._thinking_body is None or self._thinking_chat_id != chat_id:
            return
        if self._current_chat_id != chat_id:
            return
        try:
            self._thinking_text += delta
            self._thinking_body.setText(markdown_to_html(self._thinking_text))
            self._thinking_body.updateGeometry()
            self._recenter_on(self._thinking_row)
        except RuntimeError:
            self._thinking_chat_id = None
            self._thinking_row = None
            self._thinking_header = None
            self._thinking_body = None

    def is_streaming_thinking(self, chat_id) -> bool:
        """True while a thinking block for this chat is still open - MainWindow checks this to know whether finish_thinking_block()
        has anything left to close out."""
        return self._thinking_chat_id == chat_id

    def finish_thinking_block(self, chat_id) -> None:
        """
        Closes out the thinking block once the model has moved on to
        its actual answer (or finished without one): swaps the header
        to "Thought for Ns" and collapses the body, same as claude.ai's
        own default - clicking the header still re-expands it, the
        reasoning isn't discarded, just tucked away now that there's an
        answer to read instead.
        """
        is_current = self._thinking_chat_id == chat_id
        header = self._thinking_header
        body = self._thinking_body
        row = self._thinking_row
        elapsed = time.monotonic() - self._thinking_started_at
        self._thinking_chat_id = None
        self._thinking_row = None
        self._thinking_header = None
        self._thinking_body = None
        if is_current and self._current_chat_id == chat_id and header is not None and body is not None:
            try:
                seconds = max(1, round(elapsed))
                # Through the same state the header's own toggle reads
                # (see _make_thinking_row) rather than writing the
                # button's text directly - a raw setText here is what
                # left the toggle re-parsing a label it no longer
                # recognised, and the section then read as gone rather
                # than collapsed.
                header.setProperty("base_label", f"Thought for {seconds}s")
                header.setProperty("expanded", False)
                header.setText(f"▸  Thought for {seconds}s")
                header.setToolTip(tr("Show the model's reasoning"))
                header.setVisible(True)
                body.setVisible(False)
                self._recenter_on(row)
            except RuntimeError:
                pass  # block torn down mid-stream - nothing left to correct

    # ── Real streaming (Settings > Stream responses) ─────────────────
    # Counterpart to reveal_response() above for when a genuine
    # network stream is arriving (ai.main.ai_stream via AIWorker),
    # rather than one finished reply being typed out locally.

    def begin_streaming_response(self, chat_id) -> None:
        """
        Called on the FIRST chunk of a streamed reply: hides the typing
        indicator and opens an empty bubble that append_stream_chunk()
        grows piece by piece. Mirrors reveal_response()'s own
        _hide_typing + _append_bubble("") opening - see _recenter_on for
        the centering this (and every subsequent chunk) keeps current.
        """
        if self._current_chat_id != chat_id:
            return
        self._hide_typing(chat_id)
        row = self._append_bubble("", "assistant")
        container = row.findChild(QWidget, "bubbleAi")
        if container is None:
            return
        # See reveal_response's own comment on this same pattern: the
        # container starts with exactly one placeholder QLabel (from
        # populate_ai_message("") inside _append_bubble), which is what
        # append_stream_chunk cheaply updates chunk by chunk - the
        # container only gets rebuilt into its final, proper multi-widget
        # form (real code-block widgets included) once the stream ends,
        # in finish_streaming_response.
        inner_label = container.layout().itemAt(0).widget()
        self._streaming_chat_id = chat_id
        self._streaming_row = row
        self._streaming_container = container
        self._streaming_label = inner_label
        self._streaming_text = ""
        self._recenter_on(row)

    def append_stream_chunk(self, chat_id, delta: str) -> None:
        """
        Appends one piece of a streamed reply (see AIWorker.chunk) to
        the bubble opened by begin_streaming_response(). No-ops quietly
        if the chat streaming for isn't the one currently showing (the
        user switched away mid-stream) - the full reply still lands in
        the DB via MainWindow.on_ai_response regardless; it just isn't
        animated live for a backgrounded chat, same as reveal_response.
        """
        if self._streaming_label is None or self._streaming_chat_id != chat_id:
            return
        if self._current_chat_id != chat_id:
            return
        try:
            self._streaming_text += delta
            # Placeholder instead of raw code while a fence is still
            # open - see set_streaming_bubble_text.
            set_streaming_bubble_text(self._streaming_label, self._streaming_text)
            if self._streaming_container is not None:
                self._streaming_container.setProperty("raw_text", self._streaming_text)
            # Same reasoning as reveal_response()'s tick(): force the
            # label's own geometry current before _recenter_on reads
            # the row's height off the back of it.
            self._streaming_label.updateGeometry()
            self._recenter_on(self._streaming_row)
        except RuntimeError:
            # The bubble (or the whole row) was torn down mid-stream
            # chat got switched away/reloaded. Stop quietly, same as
            # reveal_response()'s own RuntimeError guard.
            self._streaming_chat_id = None
            self._streaming_row = None
            self._streaming_container = None
            self._streaming_label = None

    def is_streaming_response(self, chat_id) -> bool:
        """True while a streamed reply for this chat is still in
        progress - MainWindow.on_ai_response checks this to decide
        between finish_streaming_response() (a stream was live) and
        reveal_response() (streaming was off, or errored out before a
        single chunk arrived, so nothing's on screen yet to finish)."""
        return self._streaming_chat_id == chat_id

    def finish_streaming_response(self, chat_id, final_text: str) -> None:
        """
        Sets the bubble to the AUTHORITATIVE final text - the same
        _process_full_response() result that got saved to the DB -
        rather than trusting the sum of every streamed chunk on its
        own. ai_stream()'s own docstring calls out why those two can
        differ in small ways (a trailing .strip(), or a placeholder
        like "Got it." for a turn that turned out to be nothing but a
        control tag): this is what guarantees the bubble always ends
        up showing exactly what was persisted, even on those edges.
        """
        is_current = self._streaming_chat_id == chat_id
        row = self._streaming_row
        container = self._streaming_container
        self._streaming_chat_id = None
        self._streaming_row = None
        self._streaming_container = None
        self._streaming_label = None
        if is_current and self._current_chat_id == chat_id and container is not None:
            try:
                # Upgrades from the plain interim label append_stream_chunk
                # was updating to the real, final render - proper code-
                # block widgets and all (see populate_ai_message's own
                # docstring for why that's a full rebuild rather than
                # another setText()).
                populate_ai_message(container, final_text, theme.colors(self._theme))
                # The stream is over, so a very long reply can be capped
                # with a Show more toggle now - doing it any earlier
                # would have cut the reply off while it was still
                # arriving. See _install_assistant_collapse.
                _install_assistant_collapse(row, container, final_text, theme.colors(self._theme))
                container.updateGeometry()
                # final_text can differ slightly from the summed chunks
                # (see the docstring above) - one last recenter keeps
                # the bubble properly placed even if that edit nudged
                # its height.
                self._recenter_on(row)
            except RuntimeError:
                pass  # bubble torn down mid-stream - nothing left to correct

    def _show_typing(self, chat_id, text: str = TYPING_DOTS):
        db.set_ai_working(chat_id, True)
        self._typing_row = _make_typing_row(text, self._theme)
        self._insert_before_stretch(self._typing_row)
        QTimer.singleShot(30, self._scroll_to_bottom)

    def set_typing_label(self, text: str):
        """Updates the already-displayed indicator's text (avoids recreating it).

        Also un-hides that label: the row starts with it hidden when
        the text is just the placeholder dots (the animated logo says
        "busy" on its own - see _make_typing_row), so a real status
        arriving afterwards, like autonomous mode's step commentary,
        has to bring it back."""
        if self._typing_row is None:
            return
        label = self._typing_row.findChild(QLabel, "typingBubble")
        if label is not None:
            label.setText(text)
            has_logo = self._typing_row.findChild(QLabel, "typingLogo") is not None
            label.setVisible(bool(text) and (text != TYPING_DOTS or not has_logo))

    def _hide_typing(self, chat_id):
        db.set_ai_working(chat_id, False)
        if self._current_chat_id == chat_id and self._typing_row is not None:
            self._messages_layout.removeWidget(self._typing_row)
            self._typing_row.deleteLater()
            self._typing_row = None

    def _clear_messages(self):
        """Removes all bubbles while keeping the final layout stretch."""
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Any widget still referenced by _typing_row has just been destroyed
        # above - without this reset, a later _hide_typing() call could skip
        # its own cleanup (if the chat changed in the meantime), leaving
        # this reference pointing at an already-deleted widget. Same thing
        # for any reveal in progress: its bubble was just removed too, so
        # we cleanly stop its timer instead of letting
        # letting tick()'s RuntimeError guard handle it later.
        self._typing_row = None
        self._pending_confirmation_row = None
        self._pending_handoff_row = None
        self._stop_reveal_timers()
        # Same reasoning again: a stream in progress had its bubble just
        # destroyed above too - drop the references rather than let
        # append_stream_chunk()/finish_streaming_response() later try to
        # touch an already-deleted widget (its own RuntimeError guards
        # would catch it anyway, but there's no reason to wait for that).
        self._streaming_chat_id = None
        self._streaming_row = None
        self._streaming_container = None
        self._streaming_label = None
        # Same reasoning again: a thinking block in progress had its row
        # just destroyed above too - drop the references rather than let
        # append_thinking_chunk()/finish_thinking_block() later try to
        # touch an already-deleted widget.
        self._thinking_chat_id = None
        self._thinking_row = None
        self._thinking_header = None
        self._thinking_body = None
        # Same reasoning as the three resets above - any row mid-edit was
        # just destroyed too, so drop the reference rather than let
        # Cancel/Save later try to touch an already-deleted widget.
        self._active_edit_row = None
        self._hidden_rows_during_edit = []

    # ── Public API ─────────────────────────────────────────────────────────────
    def load_title(self, cid:int=None, title:str=None):
        """Fetches and displays the title"""
        if title:
            self.chatTitle.setText(title)
        elif cid:
            for c in db.get_all_chats():
                if c["id"] == cid:
                    self.chatTitle.setText(c["title"])
                    break

    def set_theme(self, theme_name: str) -> None:
        """
        Re-skins the chrome (background, input box, buttons - everything
        the .ui file's own baked-in styleSheet covers) immediately, then
        fully reloads the current chat so every already-rendered message
        bubble, confirmation card, etc. picks up the new theme too rather
        than staying stuck in whatever theme was active when it was first
        drawn. load_chat already knows how to re-attach a live pending
        human-handoff card (see its docstring) so that survives this too.
        """
        self._theme = theme_name
        c = theme.colors(theme_name)
        self.setStyleSheet(_build_chrome_style(c))
        self.chatInput.setStyleSheet(_chat_input_style(c))
        self._attachStatus.setStyleSheet(_attachment_error_style(c))
        self._profilePicker.apply_theme(theme_name)
        self._modelPicker.apply_theme(theme_name)
        # Baked QIcon bitmaps, which no stylesheet can re-tint - same
        # reason the mic icon is re-applied below.
        if hasattr(self, "_expandBtn"):
            self._expandBtn.setIcon(icons.icon(
                "chevron-down" if self._composer_expanded else "chevron-up", c, "text_muted"))
        if hasattr(self, "_attachBtn"):
            self._attachBtn.setIcon(icons.icon("plus", c, "text_muted"))
        self._sync_thinking_button()
        # The mic icon's color is baked in at set_mic_state() call time
        # (a qtawesome pixmap, not something the stylesheet cascade can
        # re-tint on its own) - re-apply for the CURRENT state so it
        # doesn't stay stuck in the old theme's color.
        self.set_mic_state(self._mic_state)
        if self._current_chat_id is not None:
            self.load_chat(self._current_chat_id)

    def load_chat(self, chat_id: int):
        """Loads an existing chat from the DB and displays it."""
        print("load_chat")
        self._current_chat_id = chat_id
        self._clear_messages()

        self.load_title(cid=chat_id)

        messages = db.get_messages(chat_id)
        leaf_id = messages[-1]["id"] if messages else None
        for msg in messages:
            self._append_bubble(msg["content"], msg["role"], message_id=msg["id"], is_leaf=(msg["id"] == leaf_id))

        if db.is_ai_working(chat_id):
            self._show_typing(chat_id)

        # Re-attach a WORKING Continue card if this chat has an unresolved
        # human handoff - otherwise the run above (db.get_messages) only
        # replayed db.add_message's plain-text note, and the button that
        # actually lets a human resume the run would be gone (see
        # _pending_handoff_chat_id's docstring in __init__).
        if (self._pending_handoff_chat_id == chat_id
                and self._pending_handoff_reason is not None):
            self.show_human_handoff(chat_id, self._pending_handoff_reason)

        QTimer.singleShot(50, self._scroll_to_bottom)

    def start_new_chat(self, chat_id: int):
        """Prepares the UI for a new, empty chat."""
        print("start_new_chat")
        self._current_chat_id = chat_id
        self._clear_messages()
        self.chatTitle.setText(tr("New chat"))
        self.chatInput.setFocus()
