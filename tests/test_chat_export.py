# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_export.py
===========================
Search over message content, exporting one conversation, and the backup
archive.

The restore path gets the most attention here. It replaces live data,
which makes it the one operation in the app that can lose someone's
chats, so what it does with existing files and with a hostile archive
both need to be nailed down rather than assumed.
"""

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.chat_database as db
from app import chat_export


def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"), raising=False)
    db.init_db()


# ══════════════════════════════════════════════════════════════════
# Search
# ══════════════════════════════════════════════════════════════════

def test_search_finds_a_chat_by_its_message_content(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("Untitled thing")
    db.add_message(chat_id, "user", "how do I deploy to kubernetes")

    results = db.search_messages("kubernetes")
    assert [r["id"] for r in results] == [chat_id]
    assert results[0]["matched_title"] is False


def test_search_still_finds_a_chat_by_its_title(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("Kubernetes deploys")
    db.add_message(chat_id, "user", "unrelated text")

    results = db.search_messages("kubernetes")
    assert [r["id"] for r in results] == [chat_id]
    assert results[0]["matched_title"] is True


def test_search_is_case_insensitive(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("x")
    db.add_message(chat_id, "user", "Deploy To Kubernetes")
    assert db.search_messages("KUBERNETES")


def test_a_chat_is_returned_once_however_many_messages_match(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("x")
    for _ in range(4):
        db.add_message(chat_id, "user", "kubernetes again")
    assert len(db.search_messages("kubernetes")) == 1


def test_a_snippet_shows_why_the_chat_matched(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("x")
    db.add_message(chat_id, "user", "a" * 300 + " kubernetes " + "b" * 300)

    snippet = db.search_messages("kubernetes")[0]["snippet"]
    assert "kubernetes" in snippet
    assert len(snippet) < 200, "a snippet is context, not the whole message"
    assert snippet.startswith("\u2026") and snippet.endswith("\u2026")


def test_sql_wildcards_are_matched_literally(tmp_path, monkeypatch):
    """A percent sign typed into a search box is a percent sign, not
    "match everything"."""
    _isolated_db(tmp_path, monkeypatch)
    wanted = db.create_chat("x")
    db.add_message(wanted, "user", "the build is 100% broken")
    other = db.create_chat("y")
    db.add_message(other, "user", "nothing relevant here")

    results = db.search_messages("100%")
    assert [r["id"] for r in results] == [wanted]


def test_an_underscore_is_matched_literally(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    wanted = db.create_chat("x")
    db.add_message(wanted, "user", "call tool_read_url next")
    other = db.create_chat("y")
    db.add_message(other, "user", "toolXreadXurl")

    assert [r["id"] for r in db.search_messages("tool_read_url")] == [wanted]


def test_an_empty_query_finds_nothing(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("x")
    db.add_message(chat_id, "user", "anything")
    assert db.search_messages("") == []
    assert db.search_messages("   ") == []


# ══════════════════════════════════════════════════════════════════
# Exporting one conversation
# ══════════════════════════════════════════════════════════════════

def _sample_chat():
    return {
        "id": 1,
        "title": "Kubernetes deploys",
        "created_at": "2026-01-05T10:00:00",
        "messages": [
            {"role": "user", "content": "how do I deploy", "created_at": "2026-01-05T10:00:00"},
            {"role": "assistant", "content": "like this", "created_at": "2026-01-05T10:00:05"},
        ],
    }


def test_markdown_has_the_title_and_both_speakers():
    text = chat_export.chat_to_markdown(_sample_chat(), assistant_name="Vera")
    assert text.startswith("# Kubernetes deploys")
    assert "### You" in text and "### Vera" in text
    assert "how do I deploy" in text and "like this" in text


def test_json_round_trips():
    chat = _sample_chat()
    assert json.loads(chat_export.chat_to_json(chat)) == chat


def test_the_extension_chooses_the_format(tmp_path):
    chat = _sample_chat()
    md = chat_export.write_chat(chat, str(tmp_path / "a.md"))
    js = chat_export.write_chat(chat, str(tmp_path / "a.json"))
    assert open(md, encoding="utf-8").read().startswith("#")
    assert json.loads(open(js, encoding="utf-8").read())["title"] == chat["title"]


def test_a_suggested_filename_is_safe_on_windows():
    name = chat_export.suggested_filename('Why does C:\\dir/file* "break"?')
    assert not any(c in name for c in '\\/:*?"<>|')
    assert name.endswith(".md")


def test_a_blank_title_still_gets_a_filename():
    assert chat_export.suggested_filename("") == "conversation.md"
    assert chat_export.suggested_filename("???").startswith("conversation")


def test_export_chat_reads_the_active_thread(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("Thread")
    db.add_message(chat_id, "user", "first")
    db.add_message(chat_id, "assistant", "second")

    exported = db.export_chat(chat_id)
    assert exported["title"] == "Thread"
    assert [m["content"] for m in exported["messages"]] == ["first", "second"]


def test_exporting_a_chat_that_does_not_exist_raises(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    try:
        db.export_chat(9999)
    except ValueError:
        pass
    else:
        raise AssertionError("a missing chat should have raised")


# ══════════════════════════════════════════════════════════════════
# Backup
# ══════════════════════════════════════════════════════════════════

def _fake_user_data(tmp_path):
    root = tmp_path / "userdata"
    (root / "macros").mkdir(parents=True)
    (root / "chats.db").write_bytes(b"SQLite format 3\x00pretend")
    (root / "profiles.json").write_text('{"profiles": []}', encoding="utf-8")
    (root / ".env").write_text("KESTREL_THEME=dark\n", encoding="utf-8")
    (root / "macros" / "one.json").write_text('{"name": "one"}', encoding="utf-8")
    return str(root)


def test_a_backup_contains_the_files_that_matter(tmp_path):
    source = _fake_user_data(tmp_path)
    path, included = chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    assert "chats.db" in names
    assert "profiles.json" in names
    assert ".env" in names
    assert any(n.startswith("macros/") for n in names)
    assert "backup.json" in names
    assert included


def test_a_backup_is_labelled_so_it_can_be_recognised(tmp_path):
    source = _fake_user_data(tmp_path)
    path, _ = chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)
    meta = chat_export.read_backup(path)
    assert meta["format"].endswith(".backup")
    assert meta["created_at"]


def test_a_missing_optional_file_does_not_fail_the_backup(tmp_path):
    source = _fake_user_data(tmp_path)
    os.remove(os.path.join(source, "profiles.json"))
    path, included = chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)
    assert os.path.isfile(path)
    assert "profiles.json" not in included


def test_no_database_means_a_sentence_not_a_traceback(tmp_path):
    source = str(tmp_path / "empty")
    os.makedirs(source)
    try:
        chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)
    except chat_export.ExportError as e:
        assert "nothing to back up" in str(e)
    else:
        raise AssertionError("an empty data folder should have been refused")


def test_a_failed_backup_leaves_no_partial_file(tmp_path):
    source = str(tmp_path / "empty")
    os.makedirs(source)
    target = str(tmp_path / "b.zip")
    try:
        chat_export.create_backup(target, source_dir=source)
    except chat_export.ExportError:
        pass
    assert not os.path.exists(target)
    assert not os.path.exists(target + ".part")


def test_a_zip_that_is_not_a_backup_is_refused(tmp_path):
    path = str(tmp_path / "other.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "not a backup")
    try:
        chat_export.read_backup(path)
    except chat_export.ExportError as e:
        assert "backup" in str(e).lower()
    else:
        raise AssertionError("a foreign zip should have been refused")


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    path = str(tmp_path / "notes.txt")
    open(path, "w").write("hello")
    try:
        chat_export.read_backup(path)
    except chat_export.ExportError:
        pass
    else:
        raise AssertionError("a text file should have been refused")


# -- restore, which is the one that can lose data ----------------------

def test_restore_writes_the_backup_over_the_live_folder(tmp_path):
    source = _fake_user_data(tmp_path)
    archive_path, _ = chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)

    target = str(tmp_path / "live")
    os.makedirs(target)
    restored = chat_export.restore_backup(archive_path, target_dir=target)

    assert "chats.db" in restored
    assert open(os.path.join(target, "profiles.json"), encoding="utf-8").read() == '{"profiles": []}'


def test_restore_keeps_the_files_it_replaced(tmp_path):
    """Restoring the wrong backup is a thing people do. The old files go
    into a dated folder, not into the bin."""
    source = _fake_user_data(tmp_path)
    archive_path, _ = chat_export.create_backup(str(tmp_path / "b.zip"), source_dir=source)

    target = str(tmp_path / "live")
    os.makedirs(target)
    open(os.path.join(target, "chats.db"), "w").write("THE ONE I STILL WANT")

    chat_export.restore_backup(archive_path, target_dir=target)

    kept = [d for d in os.listdir(target) if d.startswith("before-restore-")]
    assert kept, "the replaced files were not kept anywhere"
    recovered = os.path.join(target, kept[0], "chats.db")
    assert open(recovered).read() == "THE ONE I STILL WANT"


def test_a_backup_that_escapes_the_folder_is_refused(tmp_path):
    """A zip can name ../../somewhere. Writing there would put files
    anywhere on the disk the process can reach."""
    path = str(tmp_path / "evil.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backup.json", json.dumps({"format": "kestrel.backup", "version": 1}))
        archive.writestr("../../escaped.txt", "should never be written")

    target = str(tmp_path / "live")
    os.makedirs(target)
    try:
        chat_export.restore_backup(path, target_dir=target)
    except chat_export.ExportError as e:
        assert "unsafe path" in str(e)
    else:
        raise AssertionError("a path traversal should have been refused")
    assert not os.path.exists(str(tmp_path / "escaped.txt"))


def test_an_empty_backup_is_refused(tmp_path):
    path = str(tmp_path / "empty.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backup.json", json.dumps({"format": "kestrel.backup", "version": 1}))
    try:
        chat_export.restore_backup(path, target_dir=str(tmp_path))
    except chat_export.ExportError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("an empty backup should have been refused")
