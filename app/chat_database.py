# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime

from paths import BASE_PATH, USER_DATA_PATH
root_path = USER_DATA_PATH

DB_PATH = os.path.join(root_path, "data", "chats.db")


def _migrate_legacy_db() -> None:
    """
    One-time upgrade path, same reasoning as ai.main's
    _migrate_legacy_env_file: earlier builds put chats.db under BASE_PATH
    (the install directory - commonly Program Files, not writable by a
    standard user once actually installed) instead of USER_DATA_PATH.
    Copies an existing chat history over on first run after upgrading,
    rather than silently starting everyone's chat history over from
    empty. No-op when running from source, where the two paths are the
    same directory already.
    """
    old_path = os.path.join(BASE_PATH, "data", "chats.db")
    if old_path == DB_PATH:
        return
    if os.path.exists(DB_PATH) or not os.path.exists(old_path):
        return
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(old_path, "rb") as src, open(DB_PATH, "wb") as dst:
            dst.write(src.read())
    except Exception as e:
        print(f"[chat_database] Couldn't migrate old chats.db ({old_path} -> {DB_PATH}): {e}")


_migrate_legacy_db()


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # SQLite has foreign key enforcement OFF by default on every new connection.
    # Without this, messages.chat_id's "ON DELETE CASCADE" is silently ignored
    # and deleting a chat leaves its messages behind as orphaned rows forever.
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_connection():
    """
    Context manager that both commits/rolls back (like a bare sqlite3
    connection does) AND closes the connection afterwards.

    Using `with get_connection() as conn:` on its own only handles the
    transaction - sqlite3.Connection.__exit__ never closes the connection,
    so every call used to leak an open connection/file handle. Use this
    instead everywhere a connection is needed.
    """
    conn = get_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    """Initializes the database and creates the tables if they don't exist."""
    with db_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                title     TEXT    NOT NULL DEFAULT 'New chat',
                created_at TEXT   NOT NULL,
                updated_at TEXT   NOT NULL,
                pinned    INTEGER NOT NULL DEFAULT 0,
                is_ai_working    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role       TEXT    NOT NULL CHECK(role IN ('user', 'assistant')),
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );
        """)
        # Migration: adds the 'is_ai_working' column if it doesn't exist yet
        # (for existing databases created before this version)
        try:
            conn.execute("ALTER TABLE chats ADD COLUMN is_ai_working INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # The column already exists

        # Message branching (editing a message creates a sibling instead of
        # deleting everything after it - see branch_message/switch_branch
        # below). Three columns, added the same try/ALTER/except way as
        # is_ai_working just above, for the same reason: an existing
        # chats.db from before this version doesn't have them yet.
        #   - messages.parent_id: which message this one follows in its
        #     branch (NULL only for a chat's very first message - or the
        #     first message of an alternate branch starting from scratch,
        #     though nothing in this app currently creates one of those).
        #   - messages.active_child_id: which of THIS message's children
        #     (if it has more than one - i.e. it's been edited past) is
        #     the one currently shown. NULL means either "no reply yet"
        #     (a genuine leaf) or "has children but none marked active",
        #     which shouldn't happen in practice - add_message() always
        #     sets this on the parent the moment it inserts a child.
        #   - chats.active_root_id: same idea as active_child_id, but for
        #     the special case of the chat's first message itself ever
        #     being edited (there's no parent row to hold that pointer on).
        # No FK constraints on any of the three: they're self-referential
        # (messages -> messages) or point across tables in a direction
        # that would otherwise require deferred constraint checking to
        # insert safely (a message must exist before its parent can point
        # active_child_id at it, but the chat's active_root_id needs to
        # point at a message that doesn't exist until AFTER the chat row
        # already does) - enforced at the application level instead
        # (add_message/branch_message/switch_branch are the only writers).
        for ddl in (
            "ALTER TABLE messages ADD COLUMN parent_id INTEGER",
            "ALTER TABLE messages ADD COLUMN active_child_id INTEGER",
            "ALTER TABLE chats ADD COLUMN active_root_id INTEGER",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # The column already exists

        # Backfill for a database that already had messages before this
        # version: without parent_id set, get_messages()'s active-thread
        # walk (root -> active_child_id -> active_child_id -> ...) would
        # see every existing chat as empty. Wires every chat's existing
        # messages into a single straight-line branch, in the same
        # chronological order they'd have shown in before - i.e.
        # reproduces exactly what get_messages() used to return via a
        # plain "ORDER BY created_at" query, just expressed as a chain of
        # parent_id/active_child_id pointers instead. A one-time no-op on
        # any database that's never had a NULL parent_id row to begin
        # with (every row inserted by add_message() from here on always
        # has a resolved parent_id, so this only ever matches legacy rows).
        conn.execute("""
            WITH ordered AS (
                SELECT id, chat_id,
                       ROW_NUMBER() OVER (PARTITION BY chat_id ORDER BY created_at ASC, id ASC) AS rn
                FROM messages
                WHERE parent_id IS NULL
            )
            UPDATE messages
            SET parent_id = (
                SELECT o2.id FROM ordered o1
                JOIN ordered o2 ON o2.chat_id = o1.chat_id AND o2.rn = o1.rn - 1
                WHERE o1.id = messages.id
            )
            WHERE id IN (SELECT id FROM ordered WHERE rn > 1)
        """)
        conn.execute("""
            UPDATE messages SET active_child_id = (
                SELECT m2.id FROM messages m2 WHERE m2.parent_id = messages.id
                ORDER BY m2.id ASC LIMIT 1
            )
            WHERE active_child_id IS NULL
              AND EXISTS (SELECT 1 FROM messages m2 WHERE m2.parent_id = messages.id)
        """)
        conn.execute("""
            UPDATE chats SET active_root_id = (
                SELECT m.id FROM messages m
                WHERE m.chat_id = chats.id AND m.parent_id IS NULL
                ORDER BY m.id ASC LIMIT 1
            )
            WHERE active_root_id IS NULL
        """)

        # No request can legitimately be "in progress" at app startup
        # if a chat has is_ai_working=1 here, it must be a leftover from a
        # previous session that closed/crashed while a reply was pending.
        # Without this cleanup, that chat would show the typing indicator
        # (●●●) indefinitely on every launch, since no worker will ever
        # come along to turn it off.
        conn.execute("UPDATE chats SET is_ai_working=0 WHERE is_ai_working=1")

        # Images sent with a message (a screenshot, a dropped picture),
        # so the chat can show them on the message afterwards. Paths
        # only: the files themselves stay where they are (screenshots in
        # the app's screenshot folder, pasted images in "attachments").
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_attachments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                path       TEXT    NOT NULL,
                kind       TEXT    NOT NULL DEFAULT 'image'
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_message ON message_attachments(message_id)"
        )

        # A conversation's summary, once it has been compacted (see
        # ai/compaction.py): the earlier part of the chat, condensed, and
        # the id of the last message it covers. The model is given the
        # summary plus everything after that message instead of the whole
        # history. One row per chat, replaced when compacted again.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_summaries (
                chat_id         INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
                summary         TEXT    NOT NULL,
                upto_message_id INTEGER NOT NULL,
                created_at      TEXT    NOT NULL
            )
        """)


# ── Chats ──────────────────────────────────────────────────────────────────────

def create_chat(title: str = "New chat") -> int:
    """Creates a new chat and returns its id."""
    now = datetime.now().isoformat()
    with db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chats (title, created_at, updated_at, pinned) VALUES (?, ?, ?, 0)",
            (title, now, now),
        )
        return cur.lastrowid


def get_all_chats() -> list[sqlite3.Row]:
    """Returns all chats sorted: pinned first, then most recent to oldest."""
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM chats ORDER BY pinned DESC, updated_at DESC"
        ).fetchall()


def get_chat_title(chat_id: int) -> "str | None":
    """The chat's current title, or None if there is no such chat. Used
    to tell an auto-generated title apart from one someone typed."""
    with db_connection() as conn:
        row = conn.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
    return row["title"] if row else None


def update_chat_title(chat_id: int, title: str):
    with db_connection() as conn:
        conn.execute(
            "UPDATE chats SET title=?, updated_at=? WHERE id=?",
            (title, datetime.now().isoformat(), chat_id),
        )


def delete_chat(chat_id: int):
    with db_connection() as conn:
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))


def set_chat_pinned(chat_id: int, pinned: bool):
    """Pins or unpins a chat."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE chats SET pinned=? WHERE id=?",
            (1 if pinned else 0, chat_id),
        )


def set_ai_working(chat_id: int, is_ai_working: bool):
    """Records the AI's state (currently replying / done replying)."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE chats SET is_ai_working=? WHERE id=?",
            (1 if is_ai_working else 0, chat_id),
        )


def is_ai_working(chat_id: int) -> bool:
    """Returns True if the AI hasn't finished replying yet."""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT is_ai_working FROM chats WHERE id=?", (chat_id,)
        ).fetchone()
        return bool(row["is_ai_working"]) if row else False


# ── Messages ───────────────────────────────────────────────────────────────────
#
# Branching model: a message's parent_id is whatever it follows in its
# branch (NULL only for a chat's first message). A message can have more
# than one child - every time a user message gets edited (branch_message,
# below), the edit becomes a NEW child of the same parent rather than
# overwriting or deleting the original, so both versions keep existing
# side by side in the DB. Which child is currently shown is tracked by
# active_child_id on the PARENT (or active_root_id on the chat, for the
# first message) - get_messages() below is exactly that pointer chain
# walked start to finish, i.e. "the active thread", which is the only
# thing chat_widget.py's load_chat() ever actually renders. An old branch
# never gets deleted or hidden from the DB, just left with nothing
# pointing at it until switch_branch() repoints something back to it.

_AUTO_PARENT = object()  # sentinel: "resolve to the chat's current active leaf" - see add_message


def _active_leaf_id(conn: sqlite3.Connection, chat_id: int) -> "int | None":
    """
    Walks chats.active_root_id, then each message's own active_child_id,
    as far as it goes - the last message in the chat's currently active
    thread, i.e. exactly what the NEXT message (a new user turn, or the
    AI's reply to one) should be parented onto. None only for a chat
    that doesn't have a single message in it yet.
    """
    row = conn.execute("SELECT active_root_id FROM chats WHERE id=?", (chat_id,)).fetchone()
    node_id = row["active_root_id"] if row else None
    while node_id is not None:
        row = conn.execute("SELECT active_child_id FROM messages WHERE id=?", (node_id,)).fetchone()
        if row is None or row["active_child_id"] is None:
            break
        node_id = row["active_child_id"]
    return node_id


def add_message(chat_id: int, role: str, content: str, parent_id=_AUTO_PARENT) -> int:
    """
    `parent_id` defaults to auto-resolving to the chat's current active
    leaf (see _active_leaf_id) - every pre-existing call site in this
    codebase (a plain add_message(chat_id, role, content), same 3
    positional args as always, in main_window.py's various AI-reply spots
    and chat_widget.py's own normal send) needs no changes at all: it
    just keeps appending onto the live conversation exactly like before,
    with no caller needing to know or track message ids.

    The only reason to ever pass parent_id explicitly is branching itself
    (see branch_message, below) - creating a sibling of an existing
    message rather than a reply after it. Passing parent_id=None
    explicitly means "no parent, this is a first message" - used when
    branching the very first message of a chat.

    Either way, inserting a message with a resolved parent_id also marks
    it as that parent's ACTIVE child (or the chat's active_root_id, if
    parent_id is None) - a freshly added message is always the one
    that's now showing, whether it's a normal new turn or a brand new
    edit-branch; see switch_branch() for how the user can navigate back
    to an older one afterward.
    """
    now = datetime.now().isoformat()
    with db_connection() as conn:
        resolved_parent = _active_leaf_id(conn, chat_id) if parent_id is _AUTO_PARENT else parent_id

        cur = conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at, parent_id) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, now, resolved_parent),
        )
        new_id = cur.lastrowid

        if resolved_parent is not None:
            conn.execute("UPDATE messages SET active_child_id=? WHERE id=?", (new_id, resolved_parent))
        else:
            conn.execute("UPDATE chats SET active_root_id=? WHERE id=?", (new_id, chat_id))

        # Bumps updated_at so the chat rises back to the top of history
        conn.execute(
            "UPDATE chats SET updated_at=? WHERE id=?",
            (now, chat_id),
        )

        return new_id


def branch_message(chat_id: int, message_id: int, new_content: str) -> int:
    """
    Edits `message_id` by adding a NEW sibling message (same parent, same
    role) holding `new_content` - replaces what the old delete_messages_from
    + resend flow did (permanently deleting the message and everything
    after it). The original message, and whatever it led to, stay in the
    DB exactly as they were: an inactive branch, switchable back to later
    with switch_branch()/list_siblings() rather than gone for good.

    The new sibling becomes the active branch immediately (add_message's
    own doing whenever a parent_id is passed - see its docstring), so
    whatever happens right after this (chat_widget.py gets a fresh AI
    reply to the edited text) naturally continues from it, the same as
    any other new turn.

    Returns the new message's id - chat_widget.py's _confirm_edit still
    needs it to trigger getting that AI reply, the same way it needed
    the old add_message()'s returned id for a normal send.
    """
    with db_connection() as conn:
        original = conn.execute(
            "SELECT chat_id, role, parent_id FROM messages WHERE id=?", (message_id,)
        ).fetchone()
    if original is None:
        raise ValueError(f"No message with id {message_id!r}.")
    if original["chat_id"] != chat_id:
        raise ValueError(f"Message {message_id!r} doesn't belong to chat {chat_id!r}.")
    return add_message(chat_id, original["role"], new_content, parent_id=original["parent_id"])


def list_siblings(message_id: int) -> list[sqlite3.Row]:
    """
    Every message sharing the same (chat, parent) as `message_id` -
    every version it's ever been edited into or from, INCLUDING itself
    - oldest first. Backs the "‹ 2 / 3 ›" branch switcher in
    chat_widget.py: len(list_siblings(id)) is the total version count,
    and 1-indexed position of `message_id` within it is which version is
    currently showing.
    """
    with db_connection() as conn:
        this = conn.execute(
            "SELECT chat_id, parent_id FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        if this is None:
            return []
        if this["parent_id"] is None:
            return conn.execute(
                "SELECT * FROM messages WHERE chat_id=? AND parent_id IS NULL ORDER BY id ASC",
                (this["chat_id"],),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM messages WHERE parent_id=? ORDER BY id ASC",
            (this["parent_id"],),
        ).fetchall()


def switch_branch(chat_id: int, message_id: int) -> None:
    """
    Makes `message_id` the active sibling at its position - repoints its
    parent's active_child_id at it (or the chat's active_root_id, if
    it's a first-message branch with no parent row to hold that pointer).
    Nothing else needs touching: whatever sub-thread THIS branch last had
    active below it (its own active_child_id, and that message's, and so
    on) was never altered while it sat inactive, so get_messages()'s walk
    picks it back up automatically, exactly as it was left - not
    stripped down to just this one message.
    """
    with db_connection() as conn:
        this = conn.execute(
            "SELECT parent_id FROM messages WHERE id=? AND chat_id=?", (message_id, chat_id)
        ).fetchone()
        if this is None:
            raise ValueError(f"No message {message_id!r} in chat {chat_id!r}.")
        if this["parent_id"] is None:
            conn.execute("UPDATE chats SET active_root_id=? WHERE id=?", (message_id, chat_id))
        else:
            conn.execute("UPDATE messages SET active_child_id=? WHERE id=?", (message_id, this["parent_id"]))


def search_messages(query: str, limit: int = 60) -> list:
    """
    Chats containing `query`, in their titles or in anything ever said in
    them, most recently updated first.

    Searching only titles was the previous behaviour and it is close to
    useless: nobody remembers what a chat was called, they remember what
    was in it. The search deliberately crosses branches, matching any
    message row rather than only the active thread (see get_messages):
    something said in a branch that was later edited away is still
    something that was said, and a search that cannot find it is a
    search that lies.

    Each result carries `snippet`, the matched text with a little either
    side of it, and `matched_title` saying which of the two hit.
    """
    query = (query or "").strip()
    if not query:
        return []

    # LIKE with escaped wildcards rather than FTS5: the message table is
    # small, FTS would mean a second table to keep in step, and a
    # substring match is what someone typing into a filter box expects.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.updated_at, c.pinned,
                   MAX(CASE WHEN c.title LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END) AS matched_title,
                   (SELECT m2.content FROM messages m2
                     WHERE m2.chat_id = c.id AND m2.content LIKE ? ESCAPE '\\'
                     ORDER BY m2.id LIMIT 1) AS hit
              FROM chats c
              LEFT JOIN messages m ON m.chat_id = c.id
             WHERE c.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\'
             GROUP BY c.id
             ORDER BY c.updated_at DESC
             LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, int(limit)),
        ).fetchall()

    results = []
    for row in rows:
        record = dict(row)
        record["matched_title"] = bool(record.get("matched_title"))
        record["snippet"] = _snippet(record.pop("hit", None), query)
        results.append(record)
    return results


def _snippet(content: "str | None", query: str, width: int = 90) -> str:
    """The matched phrase with context either side, so a result row shows
    why it matched rather than just that it did."""
    if not content:
        return ""
    text = " ".join(content.split())
    position = text.lower().find(query.lower())
    if position < 0:
        return text[:width] + ("\u2026" if len(text) > width else "")
    start = max(0, position - width // 3)
    end = min(len(text), position + len(query) + (2 * width) // 3)
    return ("\u2026" if start else "") + text[start:end] + ("\u2026" if end < len(text) else "")


def export_chat(chat_id: int) -> dict:
    """
    Everything needed to write a chat out to a file: its title, its dates
    and its active thread. Reads through get_messages, so an export is
    the conversation as it currently reads, not every branch ever tried.
    """
    with db_connection() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if chat is None:
        raise ValueError(f"No chat with id {chat_id}.")
    return {
        "id": chat["id"],
        "title": chat["title"],
        "created_at": chat["created_at"],
        "updated_at": chat["updated_at"],
        "messages": [
            {"role": m["role"], "content": m["content"], "created_at": m["created_at"]}
            for m in get_messages(chat_id)
        ],
    }


def get_messages(chat_id: int) -> list[sqlite3.Row]:
    """
    Returns the chat's ACTIVE thread - walking chats.active_root_id, then
    each message's own active_child_id, start to finish (see
    _active_leaf_id's docstring for the same walk stopping early instead
    of collecting). NOT every message row that's ever existed for this
    chat_id: once a message has been edited at least once (branch_message),
    old branches keep their rows in the DB rather than being deleted, so a
    plain "every row for this chat_id, in id order" query would show old
    and new branches concatenated together - this walk is what keeps an
    inactive branch invisible unless the user actually switches back to
    it (switch_branch), same as a real chat product's edit history works.
    """
    with db_connection() as conn:
        row = conn.execute("SELECT active_root_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        node_id = row["active_root_id"] if row else None
        path: list[sqlite3.Row] = []
        while node_id is not None:
            msg = conn.execute("SELECT * FROM messages WHERE id=?", (node_id,)).fetchone()
            if msg is None:
                break  # a dangling pointer would mean a bug elsewhere - fail soft, not with a crash mid-render
            path.append(msg)
            node_id = msg["active_child_id"]
        return path


def delete_messages_from(chat_id: int, message_id: int) -> None:
    """
    Permanently deletes `message_id` and every message after it in the
    chat's CURRENT ACTIVE thread. No longer what chat_widget.py's "edit"
    action uses - see branch_message, which keeps the edited-away
    messages instead of destroying them - but kept as a real, separate
    operation for anything that specifically wants history gone for
    good rather than just switched away from (there's currently no such
    caller in this app, but "delete these messages, permanently" and
    "branch away from these messages, reversibly" are genuinely
    different operations worth keeping distinct rather than folding one
    into the other).
    Ordered by id rather than created_at: ids are guaranteed monotonic
    (AUTOINCREMENT), whereas two messages saved in the same call chain
    could in principle share a timestamp.
    """
    with db_connection() as conn:
        conn.execute(
            "DELETE FROM messages WHERE chat_id=? AND id >= ?",
            (chat_id, message_id),
        )


def get_message(message_id: int) -> "sqlite3.Row | None":
    """Single-row lookup by id - id, chat_id, role, content, parent_id,
    active_child_id, created_at. Used by anything that needs to walk
    the branching tree by hand from a specific message rather than
    the whole active thread (get_messages) - currently just
    MainWindow's regenerate flow (app/main_window.py's
    _on_regenerate_requested), which needs a regenerating reply's
    parent_id and that parent's own content to re-ask the same
    question."""
    with db_connection() as conn:
        return conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()


# ── Attachments ────────────────────────────────────────────────────────────────

def add_attachments(message_id: int, paths: "list[str]", kind: str = "image") -> None:
    """Records the images sent with a message (see message_attachments)."""
    paths = [p for p in paths if p]
    if not paths:
        return
    with db_connection() as conn:
        conn.executemany(
            "INSERT INTO message_attachments (message_id, path, kind) VALUES (?, ?, ?)",
            [(message_id, p, kind) for p in paths],
        )


def get_attachments(message_id: int) -> "list[str]":
    """The image paths sent with a message, in the order they were sent."""
    try:
        with db_connection() as conn:
            rows = conn.execute(
                "SELECT path FROM message_attachments WHERE message_id=? ORDER BY id",
                (message_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []   # a database from before the table existed, not initialised yet
    return [r["path"] for r in rows]


# ── Conversation summaries (compaction) ────────────────────────────────────────

def get_chat_summary(chat_id: int) -> "sqlite3.Row | None":
    """(summary, upto_message_id, created_at) for a compacted chat, else None."""
    try:
        with db_connection() as conn:
            return conn.execute(
                "SELECT summary, upto_message_id, created_at FROM chat_summaries WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None


def set_chat_summary(chat_id: int, summary: str, upto_message_id: int) -> None:
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO chat_summaries (chat_id, summary, upto_message_id, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET summary=excluded.summary, "
            "upto_message_id=excluded.upto_message_id, created_at=excluded.created_at",
            (chat_id, summary, upto_message_id, datetime.now().isoformat()),
        )


def clear_chat_summary(chat_id: int) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_summaries WHERE chat_id=?", (chat_id,))


def get_message_count(chat_id: int) -> int:
    """Length of the chat's ACTIVE thread (see get_messages) - not a
    total row count across every branch that's ever existed for this
    chat_id, which get_messages() deliberately does NOT return."""
    return len(get_messages(chat_id))
