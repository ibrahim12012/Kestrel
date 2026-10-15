/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * win32.h - the Windows side: wide strings, logging, files, registry,
 * shortcuts, processes, and the install/uninstall jobs.
 */

#ifndef KS_WIN32_H
#define KS_WIN32_H

#ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
#endif
#ifndef UNICODE
#  define UNICODE
#endif
#ifndef _UNICODE
#  define _UNICODE
#endif

#include <windows.h>

#include "common.h"
#include "payload.h"

/* ── Wide strings ───────────────────────────────────────────────────── */

size_t w_len(const WCHAR *s);
WCHAR *w_dup(const WCHAR *s);
WCHAR *w_cat(const WCHAR *a, const WCHAR *b);                 /* new string a+b */
WCHAR *w_join(const WCHAR *dir, const WCHAR *name);           /* dir\name */
bool w_eq_nocase(const WCHAR *a, const WCHAR *b);
bool w_starts_with_nocase(const WCHAR *s, const WCHAR *prefix);
WCHAR *w_from_utf8(const char *s);
char *w_to_utf8(const WCHAR *s);
WCHAR *w_parent(const WCHAR *path);                           /* NULL for a root */
const WCHAR *w_basename(const WCHAR *path);
WCHAR *w_full_path(const WCHAR *path);                        /* absolute, normalized, no trailing \ */
WCHAR *w_long_path(const WCHAR *full);                        /* \\?\ form for file APIs */

/* ── Log (%TEMP%\Kestrel Setup.log) ─────────────────────────────────── */

void log_open(const WCHAR *name);
void log_line(const char *fmt, ...);
void log_last_error(const char *what);
const WCHAR *log_path(void);

/* ── Files ──────────────────────────────────────────────────────────── */

bool file_exists(const WCHAR *path);
bool dir_exists(const WCHAR *path);
bool dir_is_empty(const WCHAR *path);
/* True when both paths name the same file or folder (through different
   spellings, short names, or links). */
bool same_file_object(const WCHAR *a, const WCHAR *b);
bool make_dirs(const WCHAR *path);
/* Removes a whole tree. Does not follow junctions or symbolic links; it
   removes the link itself. Reports each file removed to `progress`. */
bool delete_tree(const WCHAR *path, volatile int64_t *progress);
int64_t count_tree_files(const WCHAR *path);
bool move_with_retry(const WCHAR *from, const WCHAR *to);
char *read_text_file(const WCHAR *path);                      /* UTF-8, NUL-terminated */
bool write_text_file(const WCHAR *path, const char *utf8);
uint64_t disk_free_bytes(const WCHAR *path);

/* ── Known folders ──────────────────────────────────────────────────── */

WCHAR *known_folder(const GUID *id);
extern const GUID KS_FOLDERID_UserProgramFiles;
extern const GUID KS_FOLDERID_LocalAppData;
extern const GUID KS_FOLDERID_Programs;
extern const GUID KS_FOLDERID_Desktop;
extern const GUID KS_FOLDERID_Startup;

/* ── Registry (HKEY_CURRENT_USER unless given) ──────────────────────── */

bool reg_set_string(HKEY root, const WCHAR *key, const WCHAR *name, const WCHAR *value);
bool reg_set_dword(HKEY root, const WCHAR *key, const WCHAR *name, DWORD value);
WCHAR *reg_get_string(HKEY root, const WCHAR *key, const WCHAR *name, REGSAM view);
bool reg_delete_tree(HKEY root, const WCHAR *key);
bool reg_key_exists(HKEY root, const WCHAR *key, REGSAM view);

/* ── Shortcuts ──────────────────────────────────────────────────────── */

bool create_shortcut(const WCHAR *lnk, const WCHAR *target, const WCHAR *workdir, const WCHAR *description,
                     const WCHAR *aumid);

/* ── Processes ──────────────────────────────────────────────────────── */

#define MAX_TRACKED 16
typedef struct {
    int count;
    DWORD pid[MAX_TRACKED];
    HANDLE handle[MAX_TRACKED];
} ProcessSet;

/* Every running process whose executable is inside `dir`. */
void find_processes_in(const WCHAR *dir, ProcessSet *out);
void close_process_set(ProcessSet *set);
/* Sets the app's quit event, Local\<quit_name>. False when there is none:
   nothing is listening, so there is no point waiting for it to close. */
bool request_quit(ProcessSet *set, const WCHAR *quit_name);
/* True once every process in the set has exited (waits up to ms). */
bool wait_processes(ProcessSet *set, DWORD ms);
void terminate_processes(ProcessSet *set);

#endif
