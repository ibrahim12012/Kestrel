/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * win_main.c - the program: command line, the install and uninstall
 * jobs, and the window that shows ui.c's screens.
 *
 * One executable does both jobs. With a payload appended (see payload.h)
 * it is the installer; without one, sitting in the install folder as
 * "Uninstall Kestrel.exe", it is the uninstaller. The uninstaller removes
 * the folder it runs from, in one process: Windows won't delete a running
 * program's file, but it will rename it, so just before deleting the
 * folder it moves itself out to %TEMP% (see move_self_out_of).
 *
 * Install, per user, no administrator rights needed:
 *   %LOCALAPPDATA%\Programs\Kestrel      the app (replaced on update)
 *   %LOCALAPPDATA%\Kestrel               chats, macros, settings (never
 *                                         touched by setup; offered for
 *                                         deletion on uninstall)
 *   Start menu shortcut (with the app's AppUserModelID), optional desktop
 *   and sign-in shortcuts, an entry in Settings > Apps, the .ktl file
 *   association, and an App Paths entry so Win+R "kestrel" works.
 *
 * Updates are atomic: the new version is unpacked next to the old one and
 * swapped in with two renames, so a failed or canceled update leaves the
 * installed version exactly as it was, and files a previous version had
 * but this one does not are gone afterwards rather than left behind.
 *
 * Command line (all optional):
 *   /S                     silent: no window, defaults unless given below
 *   /D=<folder>            install folder (last argument, may have spaces)
 *   --desktop-shortcut=0|1 --startup=0|1 --launch --force-close
 * Uninstaller:
 *   /S --delete-data       silent uninstall, optionally deleting user data
 */

#include "win32.h"
#include "assets.h"
#include "sys.h"
#include "ui.h"

#include <dwmapi.h>
#include <objbase.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>

#define IDI_APP 1
#define WM_APP_JOB_DONE (WM_APP + 1)
#define TIMER_FRAME 1
#define DAT_NAME L"uninstall.dat"
#define UNINSTALL_KEY L"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
#define APP_PATHS_KEY L"Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
#define CLASSES_KEY L"Software\\Classes\\"
#define CLOSE_TIMEOUT_MS 9000

/* ── State ──────────────────────────────────────────────────────────── */

typedef enum { PH_PREPARE, PH_LEGACY, PH_COPY, PH_FINISH, PH_REMOVE, PH_DATA } Phase;
typedef enum { JOB_NONE, JOB_RUNNING, JOB_OK, JOB_FAILED, JOB_CANCELED } JobStatus;

typedef struct {
    bool uninstall;
    bool silent;
    WCHAR *dir;
    int desktop;             /* -1: not given */
    int startup;
    bool launch;
    bool delete_data;
    bool force_close;
} Args;

typedef struct {
    /* What is being installed (from the payload, or uninstall.dat) */
    WCHAR *app_name, *app_version, *publisher, *app_id, *exe_name, *aumid, *description;
    WCHAR *url_about, *url_help, *file_ext, *file_progid, *file_type_name;
    WCHAR *user_data_dir, *install_dir_name, *uninstaller_name, *quit_message;
    char *legacy_keys;
    uint64_t total_bytes;

    /* Where */
    WCHAR *install_dir;
    WCHAR *existing_version;     /* set when updating */
    WCHAR *legacy_uninstaller;   /* the Inno Setup uninstaller being replaced */
    WCHAR *legacy_dir;
    WCHAR *legacy_key;
    HKEY legacy_root;
} Setup;

typedef struct {
    volatile int32_t status;
    volatile int32_t phase;
    ExtractState ex;
    volatile int64_t files_done;
    volatile int64_t files_total;
    bool opt_desktop, opt_startup, opt_delete_data;
    char error_title[128];
    char error_text[1024];
    volatile DWORD sink_error;
    KsThread thread;
    bool thread_started;
} Job;

static HINSTANCE g_inst;
static Args g_args;
static Setup g_setup;
static Payload g_payload;
static bool g_have_payload;
static HANDLE g_self_file = INVALID_HANDLE_VALUE;
static PayloadSource g_source;
static Job g_job;
static WCHAR *g_self_path;
static WCHAR *g_self_moved;      /* where the uninstaller moved its own file */

static HWND g_hwnd;
static Ui g_ui;
static Image g_logo;
static Canvas g_main, g_scratch;
static HBITMAP g_bmp_main, g_bmp_scratch;
static int g_client_w, g_client_h;
static UINT g_dpi = 96;
static bool g_timer_on;
static HCURSOR g_cursor;
static LARGE_INTEGER g_freq;

static ProcessSet g_running;
static double g_close_started;

/* A finished job's progress bar fills up before the last page shows. */
static bool g_finishing;
static double g_finish_deadline, g_full_since;

static double now_seconds(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)g_freq.QuadPart;
}

static void free_w(WCHAR **p)
{
    ks_free(*p);
    *p = NULL;
}

static void set_utf8(char *dst, size_t cap, const WCHAR *src)
{
    char *u = w_to_utf8(src ? src : L"");
    ks_strlcpy(dst, u ? u : "", cap);
    ks_free(u);
}

/* ── Command line ───────────────────────────────────────────────────── */

static void parse_args(void)
{
    int argc = 0;
    WCHAR **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    g_args.desktop = -1;
    g_args.startup = -1;
    for (int i = 1; argv && i < argc; i++) {
        WCHAR *a = argv[i];
        if (w_eq_nocase(a, L"/S") || w_eq_nocase(a, L"--silent")) {
            g_args.silent = true;
        } else if (w_starts_with_nocase(a, L"/D=")) {
            /* NSIS convention: the rest of the command line, spaces and all. */
            const WCHAR *cl = GetCommandLineW();
            const WCHAR *at = cl;
            for (const WCHAR *p = cl; *p; p++)
                if ((p[0] == L'/' && (p[1] == L'D' || p[1] == L'd') && p[2] == L'='))
                    at = p + 3;
            free_w(&g_args.dir);
            g_args.dir = w_dup(at);
            size_t n = w_len(g_args.dir);
            while (n && (g_args.dir[n - 1] == L' ' || g_args.dir[n - 1] == L'"'))
                g_args.dir[--n] = 0;
            if (g_args.dir[0] == L'"')
                memmove(g_args.dir, g_args.dir + 1, n * sizeof(WCHAR));
            break;
        } else if (w_eq_nocase(a, L"--dir") && i + 1 < argc) {
            free_w(&g_args.dir);
            g_args.dir = w_dup(argv[++i]);
        } else if (w_eq_nocase(a, L"--desktop-shortcut=0")) {
            g_args.desktop = 0;
        } else if (w_eq_nocase(a, L"--desktop-shortcut=1")) {
            g_args.desktop = 1;
        } else if (w_eq_nocase(a, L"--startup=0")) {
            g_args.startup = 0;
        } else if (w_eq_nocase(a, L"--startup=1")) {
            g_args.startup = 1;
        } else if (w_eq_nocase(a, L"--launch")) {
            g_args.launch = true;
        } else if (w_eq_nocase(a, L"--delete-data")) {
            g_args.delete_data = true;
        } else if (w_eq_nocase(a, L"--force-close")) {
            g_args.force_close = true;
        }
    }
    if (argv)
        LocalFree(argv);
}

/* ── Metadata ───────────────────────────────────────────────────────── */

static WCHAR *meta_w(const char *key, const WCHAR *fallback)
{
    const char *v = g_have_payload ? payload_meta(&g_payload, key) : NULL;
    return v ? w_from_utf8(v) : w_dup(fallback);
}

static void load_meta_from_payload(void)
{
    g_setup.app_name = meta_w("app_name", L"Kestrel");
    g_setup.app_version = meta_w("app_version", L"");
    g_setup.publisher = meta_w("publisher", g_setup.app_name);
    g_setup.app_id = meta_w("app_id", g_setup.app_name);
    g_setup.exe_name = meta_w("exe_name", L"Kestrel.exe");
    g_setup.aumid = meta_w("aumid", L"");
    g_setup.description = meta_w("description", L"");
    g_setup.url_about = meta_w("url_about", L"");
    g_setup.url_help = meta_w("url_help", L"");
    g_setup.file_ext = meta_w("file_ext", L"");
    g_setup.file_progid = meta_w("file_progid", L"");
    g_setup.file_type_name = meta_w("file_type_name", L"");
    g_setup.user_data_dir = meta_w("user_data_dir", g_setup.app_name);
    g_setup.install_dir_name = meta_w("install_dir_name", g_setup.app_name);
    g_setup.uninstaller_name = meta_w("uninstaller_name", L"Uninstall.exe");
    g_setup.quit_message = meta_w("quit_message", L"Kestrel.Installer.RequestQuit");
    const char *lk = g_have_payload ? payload_meta(&g_payload, "legacy_uninstall_keys") : NULL;
    g_setup.legacy_keys = ks_strdup(lk ? lk : "");
    g_setup.total_bytes = g_have_payload ? g_payload.total_unpacked : 0;
}

/* uninstall.dat is "key=value" lines, written at install time. */
static WCHAR *dat_get(const char *dat, const char *key)
{
    size_t kl = ks_strlen(key);
    for (const char *p = dat; p && *p;) {
        const char *eol = p;
        while (*eol && *eol != '\n')
            eol++;
        if ((size_t)(eol - p) > kl && memcmp(p, key, kl) == 0 && p[kl] == '=') {
            size_t vl = (size_t)(eol - p) - kl - 1;
            if (vl && p[kl + 1 + vl - 1] == '\r')
                vl--;
            char *v = ks_strndup(p + kl + 1, vl);
            WCHAR *w = w_from_utf8(v);
            ks_free(v);
            return w;
        }
        p = *eol ? eol + 1 : eol;
    }
    return NULL;
}

static WCHAR *dat_or(const char *dat, const char *key, const WCHAR *fallback)
{
    WCHAR *v = dat_get(dat, key);
    return v ? v : w_dup(fallback);
}

static bool load_meta_from_dat(const WCHAR *dir)
{
    WCHAR *path = w_join(dir, DAT_NAME);
    char *dat = read_text_file(path);
    ks_free(path);
    if (!dat)
        return false;
    g_setup.app_name = dat_or(dat, "app_name", L"Kestrel");
    g_setup.app_version = dat_or(dat, "app_version", L"");
    g_setup.publisher = dat_or(dat, "publisher", g_setup.app_name);
    g_setup.app_id = dat_or(dat, "app_id", g_setup.app_name);
    g_setup.exe_name = dat_or(dat, "exe_name", L"Kestrel.exe");
    g_setup.aumid = dat_or(dat, "aumid", L"");
    g_setup.file_ext = dat_or(dat, "file_ext", L"");
    g_setup.file_progid = dat_or(dat, "file_progid", L"");
    g_setup.user_data_dir = dat_or(dat, "user_data_dir", L"");
    g_setup.uninstaller_name = dat_or(dat, "uninstaller_name", L"");
    g_setup.quit_message = dat_or(dat, "quit_message", L"Kestrel.Installer.RequestQuit");
    g_setup.install_dir = w_dup(dir);
    ks_free(dat);
    return true;
}

static char *dat_line(char *acc, const char *key, const WCHAR *value)
{
    char *v = w_to_utf8(value ? value : L"");
    for (char *c = v; c && *c; c++)
        if (*c == '\n' || *c == '\r')
            *c = ' ';
    size_t need = ks_strlen(acc) + ks_strlen(key) + ks_strlen(v) + 4;
    char *r = (char *)ks_realloc(acc, need);
    if (r) {
        ks_strlcat(r, key, need);
        ks_strlcat(r, "=", need);
        ks_strlcat(r, v, need);
        ks_strlcat(r, "\r\n", need);
    }
    ks_free(v);
    return r ? r : acc;
}

static bool write_dat(const WCHAR *dir, bool desktop, bool startup)
{
    char *acc = ks_strdup("");
    acc = dat_line(acc, "app_name", g_setup.app_name);
    acc = dat_line(acc, "app_version", g_setup.app_version);
    acc = dat_line(acc, "publisher", g_setup.publisher);
    acc = dat_line(acc, "app_id", g_setup.app_id);
    acc = dat_line(acc, "exe_name", g_setup.exe_name);
    acc = dat_line(acc, "aumid", g_setup.aumid);
    acc = dat_line(acc, "file_ext", g_setup.file_ext);
    acc = dat_line(acc, "file_progid", g_setup.file_progid);
    acc = dat_line(acc, "user_data_dir", g_setup.user_data_dir);
    acc = dat_line(acc, "uninstaller_name", g_setup.uninstaller_name);
    acc = dat_line(acc, "quit_message", g_setup.quit_message);
    acc = dat_line(acc, "shortcut_desktop", desktop ? L"1" : L"0");
    acc = dat_line(acc, "shortcut_startup", startup ? L"1" : L"0");
    WCHAR *path = w_join(dir, DAT_NAME);
    bool ok = write_text_file(path, acc);
    ks_free(path);
    ks_free(acc);
    return ok;
}

/* ── Where to install ───────────────────────────────────────────────── */

static WCHAR *uninstall_key(void)
{
    return w_cat(UNINSTALL_KEY, g_setup.app_id);
}

static WCHAR *default_install_dir(void)
{
    WCHAR *base = known_folder(&KS_FOLDERID_UserProgramFiles);
    if (!base) {
        WCHAR *local = known_folder(&KS_FOLDERID_LocalAppData);
        base = local ? w_join(local, L"Programs") : NULL;
        ks_free(local);
    }
    WCHAR *dir = base ? w_join(base, g_setup.install_dir_name) : NULL;
    ks_free(base);
    return dir;
}

static bool dir_is_ours(const WCHAR *dir)
{
    WCHAR *dat = w_join(dir, DAT_NAME);
    bool ours = file_exists(dat);
    ks_free(dat);
    return ours;
}

/* The Inno Setup install this installer replaces, if there is one. */
static void find_legacy_install(void)
{
    const char *p = g_setup.legacy_keys;
    while (p && *p) {
        const char *eol = p;
        while (*eol && *eol != '\n')
            eol++;
        char *name = ks_strndup(p, (size_t)(eol - p));
        WCHAR *wname = w_from_utf8(name);
        WCHAR *key = w_cat(UNINSTALL_KEY, wname);
        static const HKEY roots[] = { HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE };
        static const REGSAM views[] = { KEY_WOW64_64KEY, KEY_WOW64_32KEY };
        for (int r = 0; r < 2 && !g_setup.legacy_uninstaller; r++) {
            for (int v = 0; v < 2 && !g_setup.legacy_uninstaller; v++) {
                WCHAR *cmd = reg_get_string(roots[r], key, L"UninstallString", views[v]);
                if (!cmd)
                    continue;
                /* "C:\...\unins000.exe" -> C:\...\unins000.exe */
                WCHAR *exe = w_dup(cmd[0] == L'"' ? cmd + 1 : cmd);
                for (WCHAR *c = exe; *c; c++)
                    if (*c == L'"') {
                        *c = 0;
                        break;
                    }
                if (file_exists(exe)) {
                    g_setup.legacy_uninstaller = exe;
                    g_setup.legacy_root = roots[r];
                    g_setup.legacy_key = w_dup(key);
                    WCHAR *loc = reg_get_string(roots[r], key, L"InstallLocation", views[v]);
                    g_setup.legacy_dir = loc ? w_full_path(loc) : w_parent(exe);
                    ks_free(loc);
                    WCHAR *ver = reg_get_string(roots[r], key, L"DisplayVersion", views[v]);
                    if (ver && !g_setup.existing_version)
                        g_setup.existing_version = ver;
                    else
                        ks_free(ver);
                    char *u = w_to_utf8(exe);
                    log_line("previous Inno Setup install found: %s", u);
                    ks_free(u);
                } else {
                    ks_free(exe);
                }
                ks_free(cmd);
            }
        }
        ks_free(key);
        ks_free(wname);
        ks_free(name);
        p = *eol ? eol + 1 : eol;
    }
}

static void choose_install_dir(void)
{
    WCHAR *key = uninstall_key();
    WCHAR *loc = reg_get_string(HKEY_CURRENT_USER, key, L"InstallLocation", 0);
    if (loc && dir_is_ours(loc)) {
        g_setup.install_dir = w_full_path(loc);
        g_setup.existing_version = reg_get_string(HKEY_CURRENT_USER, key, L"DisplayVersion", 0);
        if (!g_setup.existing_version)
            g_setup.existing_version = w_dup(L"?");
    }
    ks_free(loc);
    ks_free(key);

    find_legacy_install();

    if (g_args.dir && g_args.dir[0]) {
        free_w(&g_setup.install_dir);
        g_setup.install_dir = w_full_path(g_args.dir);
    }
    if (!g_setup.install_dir) {
        WCHAR *local = known_folder(&KS_FOLDERID_LocalAppData);
        /* Take over the previous install's folder when it is the user's
           own (the Inno installer's per-user default was the same). */
        if (g_setup.legacy_dir && local && w_starts_with_nocase(g_setup.legacy_dir, local))
            g_setup.install_dir = w_dup(g_setup.legacy_dir);
        else
            g_setup.install_dir = default_install_dir();
        ks_free(local);
    }
}

/* Shortcut paths for this app. */
static WCHAR *shortcut_path(const GUID *folder)
{
    WCHAR *dir = known_folder(folder);
    if (!dir)
        return NULL;
    WCHAR *name = w_cat(g_setup.app_name, L".lnk");
    WCHAR *p = w_join(dir, name);
    ks_free(name);
    ks_free(dir);
    return p;
}

/* ── Job helpers ────────────────────────────────────────────────────── */

static void job_error(const char *title, const char *fmt, ...)
{
    ks_strlcpy(g_job.error_title, title, sizeof g_job.error_title);
    va_list ap;
    va_start(ap, fmt);
    ks_vformat(g_job.error_text, sizeof g_job.error_text, fmt, ap);
    va_end(ap);
    log_line("ERROR: %s: %s", g_job.error_title, g_job.error_text);
}

static char *u8(const WCHAR *w)
{
    return w_to_utf8(w ? w : L"");
}

/* Eight hex digits, for unique-enough file names. */
static void hex8(WCHAR *out, DWORD v)
{
    for (int i = 0; i < 8; i++)
        out[i] = L"0123456789abcdef"[(v >> (28 - 4 * i)) & 0xF];
    out[8] = 0;
}

/* Leftovers next to the install folder: ".Kestrel.new-*" and
   ".Kestrel.old-*" folders from an interrupted update, and
   ".Kestrel.old-uninstaller-*.exe" from an uninstall that couldn't move
   itself to %TEMP% (see move_self_out_of). */
static void remove_leftovers(const WCHAR *install_dir)
{
    WCHAR *parent = w_parent(install_dir);
    if (!parent)
        return;
    WCHAR *prefix = w_cat(L".", w_basename(install_dir));
    WCHAR *pattern_dir = w_long_path(parent);
    WCHAR *pattern = w_cat(pattern_dir, L"\\*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, 0);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            WCHAR *new_prefix = w_cat(prefix, L".new-");
            WCHAR *old_prefix = w_cat(prefix, L".old-");
            if (w_starts_with_nocase(fd.cFileName, new_prefix) || w_starts_with_nocase(fd.cFileName, old_prefix)) {
                WCHAR *victim = w_join(parent, fd.cFileName);
                char *u = u8(victim);
                log_line("removing leftover %s", u);
                ks_free(u);
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                    delete_tree(victim, NULL);
                } else {
                    /* No retrying: an uninstaller that is somehow still
                       running just stays until next time. */
                    WCHAR *lv = w_long_path(victim);
                    SetFileAttributesW(lv, FILE_ATTRIBUTE_NORMAL);
                    DeleteFileW(lv);
                    ks_free(lv);
                }
                ks_free(victim);
            }
            ks_free(new_prefix);
            ks_free(old_prefix);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    ks_free(pattern);
    ks_free(pattern_dir);
    ks_free(prefix);
    ks_free(parent);
}

/* ── Extraction sink ────────────────────────────────────────────────── */

typedef struct {
    WCHAR *root;                  /* \\?\ form */
} WinSink;

static WCHAR *sink_path(WinSink *s, const char *rel)
{
    WCHAR *w = w_from_utf8(rel);
    for (WCHAR *c = w; c && *c; c++)
        if (*c == L'/')
            *c = L'\\';
    WCHAR *p = w_join(s->root, w);
    ks_free(w);
    return p;
}

static bool sink_make_dir(void *ctx, const char *rel)
{
    WCHAR *p = sink_path((WinSink *)ctx, rel);
    BOOL ok = p && (CreateDirectoryW(p, NULL) || GetLastError() == ERROR_ALREADY_EXISTS);
    if (!ok) {
        g_job.sink_error = GetLastError();
        log_last_error("CreateDirectory");
    }
    ks_free(p);
    return ok;
}

static void *sink_open(void *ctx, const char *rel, uint64_t size)
{
    WCHAR *p = sink_path((WinSink *)ctx, rel);
    HANDLE h = p ? CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL)
                 : INVALID_HANDLE_VALUE;
    if (h == INVALID_HANDLE_VALUE) {
        g_job.sink_error = GetLastError();
        log_last_error("CreateFile");
        ks_free(p);
        return NULL;
    }
    ks_free(p);
    if (size > 0) {
        /* Reserve the space up front: less fragmentation, and a full disk
           is found before writing rather than halfway through. */
        LARGE_INTEGER end, zero;
        end.QuadPart = (LONGLONG)size;
        zero.QuadPart = 0;
        if (!SetFilePointerEx(h, end, NULL, FILE_BEGIN) || !SetEndOfFile(h)) {
            g_job.sink_error = GetLastError();
            log_last_error("reserve space");
            CloseHandle(h);
            return NULL;
        }
        SetFilePointerEx(h, zero, NULL, FILE_BEGIN);
    }
    return (void *)h;
}

static bool sink_write(void *ctx, void *handle, const void *data, size_t len)
{
    (void)ctx;
    const uint8_t *p = (const uint8_t *)data;
    while (len > 0) {
        DWORD n = len > (1u << 30) ? (1u << 30) : (DWORD)len, written = 0;
        if (!WriteFile((HANDLE)handle, p, n, &written, NULL) || written == 0) {
            g_job.sink_error = GetLastError();
            log_last_error("WriteFile");
            return false;
        }
        p += written;
        len -= written;
    }
    return true;
}

static bool sink_close(void *ctx, void *handle, bool ok)
{
    (void)ctx;
    (void)ok;
    return CloseHandle((HANDLE)handle) != 0;
}

static bool read_self(void *ctx, uint64_t offset, void *buf, size_t len)
{
    (void)ctx;
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        OVERLAPPED ov;
        memset(&ov, 0, sizeof ov);
        ov.Offset = (DWORD)offset;
        ov.OffsetHigh = (DWORD)(offset >> 32);
        DWORD n = len > (1u << 30) ? (1u << 30) : (DWORD)len, got = 0;
        if (!ReadFile(g_self_file, p, n, &got, &ov) || got == 0)
            return false;
        p += got;
        len -= got;
        offset += got;
    }
    return true;
}

/* ── Install job ────────────────────────────────────────────────────── */

static bool run_legacy_uninstaller(void)
{
    if (!g_setup.legacy_uninstaller)
        return true;
    g_job.phase = PH_LEGACY;
    char *u = u8(g_setup.legacy_uninstaller);
    log_line("running the previous uninstaller: %s", u);
    ks_free(u);
    SHELLEXECUTEINFOW sei;
    memset(&sei, 0, sizeof sei);
    sei.cbSize = sizeof sei;
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC;
    sei.lpFile = g_setup.legacy_uninstaller;
    sei.lpParameters = L"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART";
    sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei) || !sei.hProcess) {
        log_last_error("start previous uninstaller");
        return false;
    }
    WaitForSingleObject(sei.hProcess, 180000);
    CloseHandle(sei.hProcess);
    /* Its second phase runs from a temporary copy; the registry key going
       away is what says it has finished. */
    for (int i = 0; i < 600 && reg_key_exists(g_setup.legacy_root, g_setup.legacy_key, KEY_WOW64_64KEY); i++)
        Sleep(100);
    log_line("previous version removed");
    return true;
}

static void register_app(const WCHAR *dir, const WCHAR *exe)
{
    WCHAR *key = uninstall_key();
    WCHAR *uninstaller = w_join(dir, g_setup.uninstaller_name);
    WCHAR *quoted = w_cat(L"\"", uninstaller);
    WCHAR *uninstall_cmd = w_cat(quoted, L"\"");
    WCHAR *quiet_cmd = w_cat(uninstall_cmd, L" /S");
    WCHAR *icon = w_cat(exe, L",0");
    SYSTEMTIME t;
    GetLocalTime(&t);
    WCHAR date[9];
    int y = t.wYear, m = t.wMonth, d = t.wDay;
    WCHAR digits[] = L"0123456789";
    date[0] = digits[(y / 1000) % 10];
    date[1] = digits[(y / 100) % 10];
    date[2] = digits[(y / 10) % 10];
    date[3] = digits[y % 10];
    date[4] = digits[m / 10];
    date[5] = digits[m % 10];
    date[6] = digits[d / 10];
    date[7] = digits[d % 10];
    date[8] = 0;

    bool ok = reg_set_string(HKEY_CURRENT_USER, key, L"DisplayName", g_setup.app_name)
              && reg_set_string(HKEY_CURRENT_USER, key, L"DisplayVersion", g_setup.app_version)
              && reg_set_string(HKEY_CURRENT_USER, key, L"Publisher", g_setup.publisher)
              && reg_set_string(HKEY_CURRENT_USER, key, L"DisplayIcon", icon)
              && reg_set_string(HKEY_CURRENT_USER, key, L"InstallLocation", dir)
              && reg_set_string(HKEY_CURRENT_USER, key, L"UninstallString", uninstall_cmd)
              && reg_set_string(HKEY_CURRENT_USER, key, L"QuietUninstallString", quiet_cmd)
              && reg_set_string(HKEY_CURRENT_USER, key, L"InstallDate", date)
              && reg_set_dword(HKEY_CURRENT_USER, key, L"NoModify", 1)
              && reg_set_dword(HKEY_CURRENT_USER, key, L"NoRepair", 1)
              && reg_set_dword(HKEY_CURRENT_USER, key, L"EstimatedSize", (DWORD)((g_setup.total_bytes + 1023) / 1024));
    if (g_setup.url_about[0])
        reg_set_string(HKEY_CURRENT_USER, key, L"URLInfoAbout", g_setup.url_about);
    if (g_setup.url_help[0])
        reg_set_string(HKEY_CURRENT_USER, key, L"HelpLink", g_setup.url_help);
    if (!ok)
        log_last_error("register in Apps & Features");

    /* Win+R "kestrel" */
    WCHAR *app_paths = w_cat(APP_PATHS_KEY, g_setup.exe_name);
    reg_set_string(HKEY_CURRENT_USER, app_paths, NULL, exe);
    reg_set_string(HKEY_CURRENT_USER, app_paths, L"Path", dir);

    /* .ktl macro packs open in Kestrel. */
    if (g_setup.file_ext[0] && g_setup.file_progid[0]) {
        WCHAR *ext_key = w_cat(CLASSES_KEY, g_setup.file_ext);
        WCHAR *prog_key = w_cat(CLASSES_KEY, g_setup.file_progid);
        WCHAR *icon_key = w_cat(prog_key, L"\\DefaultIcon");
        WCHAR *cmd_key = w_cat(prog_key, L"\\shell\\open\\command");
        WCHAR *q1 = w_cat(L"\"", exe);
        WCHAR *q2 = w_cat(q1, L"\"");
        WCHAR *icon_value = w_cat(q2, L",0");
        WCHAR *open_value = w_cat(q2, L" \"%1\"");
        WCHAR *apps_key0 = w_cat(CLASSES_KEY L"Applications\\", g_setup.exe_name);
        WCHAR *apps_key = w_cat(apps_key0, L"\\SupportedTypes");
        reg_set_string(HKEY_CURRENT_USER, ext_key, NULL, g_setup.file_progid);
        reg_set_string(HKEY_CURRENT_USER, prog_key, NULL, g_setup.file_type_name);
        reg_set_string(HKEY_CURRENT_USER, icon_key, NULL, icon_value);
        reg_set_string(HKEY_CURRENT_USER, cmd_key, NULL, open_value);
        reg_set_string(HKEY_CURRENT_USER, apps_key, g_setup.file_ext, L"");
        ks_free(ext_key);
        ks_free(prog_key);
        ks_free(icon_key);
        ks_free(cmd_key);
        ks_free(q1);
        ks_free(q2);
        ks_free(icon_value);
        ks_free(open_value);
        ks_free(apps_key0);
        ks_free(apps_key);
    }
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, NULL, NULL);

    ks_free(app_paths);
    ks_free(key);
    ks_free(uninstaller);
    ks_free(quoted);
    ks_free(uninstall_cmd);
    ks_free(quiet_cmd);
    ks_free(icon);
}

static void make_shortcuts(const WCHAR *dir, const WCHAR *exe)
{
    struct {
        const GUID *folder;
        bool want;
    } links[] = {
        { &KS_FOLDERID_Programs, true },
        { &KS_FOLDERID_Desktop, g_job.opt_desktop },
        { &KS_FOLDERID_Startup, g_job.opt_startup },
    };
    for (int i = 0; i < 3; i++) {
        WCHAR *lnk = shortcut_path(links[i].folder);
        if (!lnk)
            continue;
        if (links[i].want) {
            if (!create_shortcut(lnk, exe, dir, g_setup.description, g_setup.aumid)) {
                char *u = u8(lnk);
                log_line("could not create shortcut %s", u);
                ks_free(u);
            }
        } else if (file_exists(lnk)) {
            DeleteFileW(lnk);
        }
        ks_free(lnk);
    }
    SHChangeNotify(SHCNE_UPDATEDIR, SHCNF_PATHW, NULL, NULL);
}

static bool install_steps(void)
{
    const WCHAR *dir = g_setup.install_dir;
    char *udir = u8(dir);
    log_line("installing %s into %s", "payload", udir);

    /* A folder we can take over: new, empty, ours, or the old install's. */
    if (dir_exists(dir) && !dir_is_empty(dir) && !dir_is_ours(dir)
        && !(g_setup.legacy_dir && w_eq_nocase(dir, g_setup.legacy_dir))) {
        job_error("Choose another folder",
                  "%s already has other files in it. Setup only installs into an empty folder, or over an "
                  "earlier install of the app. Choose an empty folder, or a new one.", udir);
        ks_free(udir);
        return false;
    }
    WCHAR *parent = w_parent(dir);
    if (!parent) {
        job_error("Choose another folder", "Setup can't install into the root of a drive. Choose a folder.");
        ks_free(udir);
        return false;
    }

    uint64_t free_bytes = disk_free_bytes(parent);
    uint64_t need = g_setup.total_bytes + 32ull * 1024 * 1024;
    if (free_bytes < need) {
        char a[32], b[32];
        ks_format_size(a, sizeof a, need);
        ks_format_size(b, sizeof b, free_bytes);
        job_error("Not enough space",
                  "There isn't enough free space for the app: it needs %s, and %s is free. Free up some space, "
                  "or choose another location, and try again.", a, b);
        ks_free(parent);
        ks_free(udir);
        return false;
    }

    if (!make_dirs(parent)) {
        char *up = u8(parent);
        log_last_error("create parent folder");
        job_error("Can't install there",
                  "Setup can't create %s. Choose a folder you can write to, such as the one setup suggests.", up);
        ks_free(up);
        ks_free(parent);
        ks_free(udir);
        return false;
    }

    remove_leftovers(dir);

    if (!run_legacy_uninstaller())
        log_line("continuing without removing the previous version");

    /* Unpack next to the target, then swap. */
    WCHAR stamp[9];
    hex8(stamp, GetTickCount() ^ (GetCurrentProcessId() << 16));
    WCHAR *base_dot = w_cat(L".", w_basename(dir));
    WCHAR *new_name0 = w_cat(base_dot, L".new-");
    WCHAR *new_name = w_cat(new_name0, stamp);
    WCHAR *old_name0 = w_cat(base_dot, L".old-");
    WCHAR *old_name = w_cat(old_name0, stamp);
    WCHAR *staging = w_join(parent, new_name);
    WCHAR *old = w_join(parent, old_name);
    ks_free(base_dot);
    ks_free(new_name0);
    ks_free(new_name);
    ks_free(old_name0);
    ks_free(old_name);

    bool ok = false;
    WCHAR *staging_long = w_long_path(staging);
    if (!CreateDirectoryW(staging_long, NULL)) {
        DWORD e = GetLastError();
        log_last_error("create staging folder");
        char *up = u8(parent);
        if (e == ERROR_ACCESS_DENIED)
            job_error("Can't install there",
                      "You don't have permission to write to %s. Choose a folder in your own user folder, "
                      "such as the one setup suggests.", up);
        else
            job_error("Can't install there", "Setup couldn't create a folder in %s (error %u).", up, (unsigned)e);
        ks_free(up);
        goto done;
    }

    g_job.phase = PH_COPY;
    WinSink sink_ctx = { staging_long };
    PayloadSink sink = { &sink_ctx, sink_make_dir, sink_open, sink_write, sink_close };
    int threads = KS_CLAMP(ks_cpu_count(), 1, 8);
    log_line("unpacking %d files in %d chunks on %d threads", g_payload.file_count, g_payload.chunk_count, threads);
    if (!payload_extract(&g_payload, &g_source, &sink, threads, &g_job.ex)) {
        if (g_job.ex.cancel && !g_job.ex.failed) {
            log_line("canceled while unpacking");
        } else if (g_job.sink_error == ERROR_DISK_FULL || g_job.sink_error == ERROR_HANDLE_DISK_FULL) {
            job_error("Not enough space", "The disk filled up while unpacking. Free up some space and try again.");
        } else {
            job_error("Couldn't install", "%s", g_job.ex.error);
        }
        delete_tree(staging, NULL);
        goto done;
    }
    log_line("unpacked %lld bytes", (long long)g_job.ex.bytes_done);

    if (!write_dat(staging, g_job.opt_desktop, g_job.opt_startup)) {
        log_last_error("write uninstall.dat");
        job_error("Couldn't install", "Setup couldn't write its uninstall information.");
        delete_tree(staging, NULL);
        goto done;
    }

    /* From here the install can no longer be canceled. */
    g_job.phase = PH_FINISH;
    bool had_old = dir_exists(dir);
    if (had_old && !move_with_retry(dir, old)) {
        log_last_error("move the current version aside");
        job_error("Couldn't update",
                  "Some of the current version's files are in use, so it can't be replaced. Quit the app "
                  "(right-click its icon near the clock and choose Quit), then try again.");
        delete_tree(staging, NULL);
        goto done;
    }
    if (!move_with_retry(staging, dir)) {
        log_last_error("move the new version into place");
        if (had_old)
            move_with_retry(old, dir);
        job_error("Couldn't install", "Setup couldn't move the app into %s.", udir);
        delete_tree(staging, NULL);
        goto done;
    }
    if (had_old && !delete_tree(old, NULL))
        log_line("the previous version's folder could not be fully removed; it will be cleaned up next time");

    WCHAR *exe = w_join(dir, g_setup.exe_name);
    make_shortcuts(dir, exe);
    register_app(dir, exe);
    ks_free(exe);
    log_line("installed");
    ok = true;

done:
    ks_free(staging_long);
    ks_free(staging);
    ks_free(old);
    ks_free(parent);
    ks_free(udir);
    return ok;
}

/* ── Uninstall job ──────────────────────────────────────────────────── */

static bool is_plain_name(const WCHAR *s)
{
    if (!s || !s[0])
        return false;
    for (const WCHAR *c = s; *c; c++)
        if (*c == L'\\' || *c == L'/' || *c == L':')
            return false;
    return !(s[0] == L'.' && (s[1] == 0 || (s[1] == L'.' && s[2] == 0)));
}

/* The uninstaller runs from the folder it removes. Windows won't delete a
   running program's file, but it will rename it (on the same drive), so
   just before the folder is deleted the uninstaller moves itself out: to
   %TEMP%, where the next setup run or Storage Sense deletes it, or, when
   %TEMP% is on another drive, next to the folder, hidden, where the next
   setup run removes it with the other leftovers. */
static void move_self_out_of(const WCHAR *dir)
{
    if (g_self_moved)
        return;
    WCHAR *self_dir = w_parent(g_self_path);
    bool inside = self_dir && same_file_object(self_dir, dir);
    ks_free(self_dir);
    if (!inside)
        return;

    WCHAR stamp[9];
    hex8(stamp, GetTickCount() ^ (GetCurrentProcessId() << 12));
    WCHAR *targets[2] = { NULL, NULL };
    WCHAR temp[MAX_PATH + 1];
    DWORD tn = GetTempPathW(MAX_PATH, temp);
    if (tn && tn <= MAX_PATH) {
        WCHAR *name = w_cat(L"Kestrel-Uninstall-", stamp);
        WCHAR *file = w_cat(name, L".exe");
        targets[0] = w_cat(temp, file);         /* GetTempPath ends in a backslash */
        ks_free(name);
        ks_free(file);
    }
    WCHAR *parent = w_parent(dir);
    if (parent) {
        WCHAR *a = w_cat(L".", w_basename(dir));
        WCHAR *b = w_cat(a, L".old-uninstaller-");
        WCHAR *c = w_cat(b, stamp);
        WCHAR *d = w_cat(c, L".exe");
        targets[1] = w_join(parent, d);
        ks_free(a);
        ks_free(b);
        ks_free(c);
        ks_free(d);
        ks_free(parent);
    }
    WCHAR *from = w_long_path(g_self_path);
    for (int i = 0; i < 2 && !g_self_moved; i++) {
        if (!targets[i])
            continue;
        WCHAR *to = w_long_path(targets[i]);
        /* No MOVEFILE_COPY_ALLOWED: a copy would leave this file where it is. */
        if (to && MoveFileExW(from, to, 0)) {
            if (i == 1)
                SetFileAttributesW(to, FILE_ATTRIBUTE_HIDDEN);
            g_self_moved = targets[i];
            targets[i] = NULL;
            char *u = u8(g_self_moved);
            log_line("moved the uninstaller out of the folder, to %s", u);
            ks_free(u);
        } else {
            log_last_error("move the uninstaller out of the folder");
        }
        ks_free(to);
    }
    ks_free(from);
    ks_free(targets[0]);
    ks_free(targets[1]);
}

static bool g_uninstall_verified;

static bool uninstall_steps(void)
{
    const WCHAR *dir = g_setup.install_dir;
    char *udir = u8(dir);
    log_line("uninstalling from %s", udir);
    /* Checked once: a retry after a partial removal may no longer find
       uninstall.dat, and the metadata it gave is already loaded. */
    if (!g_uninstall_verified && !dir_is_ours(dir)) {
        job_error("Couldn't uninstall", "%s doesn't look like an install made by this setup, so nothing was removed.", udir);
        ks_free(udir);
        return false;
    }
    g_uninstall_verified = true;

    g_job.phase = PH_REMOVE;
    const GUID *folders[] = { &KS_FOLDERID_Programs, &KS_FOLDERID_Desktop, &KS_FOLDERID_Startup };
    for (int i = 0; i < 3; i++) {
        WCHAR *lnk = shortcut_path(folders[i]);
        if (lnk && file_exists(lnk))
            DeleteFileW(lnk);
        ks_free(lnk);
    }

    WCHAR *key = uninstall_key();
    reg_delete_tree(HKEY_CURRENT_USER, key);
    ks_free(key);
    WCHAR *app_paths = w_cat(APP_PATHS_KEY, g_setup.exe_name);
    reg_delete_tree(HKEY_CURRENT_USER, app_paths);
    ks_free(app_paths);
    if (g_setup.file_ext[0] && g_setup.file_progid[0]) {
        WCHAR *ext_key = w_cat(CLASSES_KEY, g_setup.file_ext);
        WCHAR *current = reg_get_string(HKEY_CURRENT_USER, ext_key, NULL, 0);
        if (current && w_eq_nocase(current, g_setup.file_progid))
            reg_delete_tree(HKEY_CURRENT_USER, ext_key);
        ks_free(current);
        ks_free(ext_key);
        WCHAR *prog_key = w_cat(CLASSES_KEY, g_setup.file_progid);
        reg_delete_tree(HKEY_CURRENT_USER, prog_key);
        ks_free(prog_key);
        WCHAR *apps_key = w_cat(CLASSES_KEY L"Applications\\", g_setup.exe_name);
        reg_delete_tree(HKEY_CURRENT_USER, apps_key);
        ks_free(apps_key);
    }
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, NULL, NULL);

    move_self_out_of(dir);
    g_job.files_total = count_tree_files(dir);
    bool files_ok = delete_tree(dir, &g_job.files_done);
    if (!files_ok)
        log_line("some files could not be removed");

    if (g_job.opt_delete_data && is_plain_name(g_setup.user_data_dir)) {
        g_job.phase = PH_DATA;
        WCHAR *local = known_folder(&KS_FOLDERID_LocalAppData);
        WCHAR *data = local ? w_join(local, g_setup.user_data_dir) : NULL;
        if (data && dir_exists(data)) {
            char *ud = u8(data);
            log_line("deleting user data %s", ud);
            ks_free(ud);
            if (!delete_tree(data, NULL))
                log_line("some user data could not be deleted");
        }
        ks_free(data);
        ks_free(local);
    }
    SHChangeNotify(SHCNE_UPDATEDIR, SHCNF_PATHW, NULL, NULL);
    log_line(files_ok ? "uninstalled" : "uninstalled, with leftovers");
    if (!files_ok) {
        job_error("Partly removed",
                  "The app was removed, but some files in %s were in use and could not be deleted. They will "
                  "not do anything; you can delete the folder yourself.", udir);
        ks_free(udir);
        return false;
    }
    ks_free(udir);
    return true;
}

/* ── Running the job ────────────────────────────────────────────────── */

static KS_THREAD_FN(job_thread)
{
    (void)arg;
    HRESULT com = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    bool ok = g_args.uninstall ? uninstall_steps() : install_steps();
    if (SUCCEEDED(com))
        CoUninitialize();
    if (ok)
        g_job.status = JOB_OK;
    else if (g_job.ex.cancel && !g_job.ex.failed && !g_job.error_title[0])
        g_job.status = JOB_CANCELED;
    else
        g_job.status = JOB_FAILED;
    if (g_hwnd)
        PostMessageW(g_hwnd, WM_APP_JOB_DONE, 0, 0);
    KS_THREAD_RETURN;
}

static void job_reset(void)
{
    int32_t keep_desktop = g_job.opt_desktop, keep_startup = g_job.opt_startup, keep_data = g_job.opt_delete_data;
    memset(&g_job, 0, sizeof g_job);
    g_job.opt_desktop = keep_desktop;
    g_job.opt_startup = keep_startup;
    g_job.opt_delete_data = keep_data;
}

static bool job_start(void)
{
    job_reset();
    g_job.opt_desktop = g_ui.opt_desktop;
    g_job.opt_startup = g_ui.opt_startup;
    g_job.opt_delete_data = g_ui.opt_delete_data;
    g_job.status = JOB_RUNNING;
    g_job.thread_started = ks_thread_start(&g_job.thread, job_thread, NULL);
    if (!g_job.thread_started)
        g_job.status = JOB_FAILED;
    return g_job.thread_started;
}

static void launch_app(void)
{
    WCHAR *exe = w_join(g_setup.install_dir, g_setup.exe_name);
    char *u = u8(exe);
    log_line("launching %s", u);
    ks_free(u);
    /* If setup itself was started elevated, starting the app directly
       would run it as administrator too. Explorer starts it as the user. */
    HANDLE token = NULL;
    TOKEN_ELEVATION elevation = { 0 };
    DWORD len = 0;
    bool elevated = OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)
                    && GetTokenInformation(token, TokenElevation, &elevation, sizeof elevation, &len)
                    && elevation.TokenIsElevated;
    if (token)
        CloseHandle(token);
    if (elevated) {
        ShellExecuteW(NULL, L"open", L"explorer.exe", exe, NULL, SW_SHOWNORMAL);
    } else {
        STARTUPINFOW si;
        PROCESS_INFORMATION pi;
        memset(&si, 0, sizeof si);
        si.cb = sizeof si;
        WCHAR *q1 = w_cat(L"\"", exe);
        WCHAR *cmd = w_cat(q1, L"\"");
        if (CreateProcessW(exe, cmd, NULL, NULL, FALSE, 0, NULL, g_setup.install_dir, &si, &pi)) {
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
        } else {
            log_last_error("launch");
        }
        ks_free(q1);
        ks_free(cmd);
    }
    ks_free(exe);
}

/* ── Window: drawing ────────────────────────────────────────────────── */

static void free_canvas(Canvas *cv, HBITMAP *bmp)
{
    if (cv->native) {
        DeleteDC((HDC)cv->native);
        cv->native = NULL;
    }
    if (*bmp) {
        DeleteObject(*bmp);
        *bmp = NULL;
    }
}

static bool make_canvas(Canvas *cv, HBITMAP *bmp, int w, int h)
{
    free_canvas(cv, bmp);
    BITMAPINFO bi;
    memset(&bi, 0, sizeof bi);
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = w;
    bi.bmiHeader.biHeight = -h;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    HDC dc = CreateCompatibleDC(NULL);
    void *bits = NULL;
    *bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
    if (!*bmp || !bits) {
        DeleteDC(dc);
        return false;
    }
    SelectObject(dc, *bmp);
    gfx_canvas_init(cv, (uint32_t *)bits, w, h, w);
    cv->native = dc;
    return true;
}

static void resize_canvases(int w, int h)
{
    if (w <= 0 || h <= 0 || (w == g_client_w && h == g_client_h && g_main.px))
        return;
    g_client_w = w;
    g_client_h = h;
    make_canvas(&g_main, &g_bmp_main, w, h);
    make_canvas(&g_scratch, &g_bmp_scratch, w, h);
    ui_set_scale(&g_ui, (float)g_dpi / 96.0f, w, h);
}

static void ensure_timer(void)
{
    if (!g_timer_on && g_hwnd) {
        SetTimer(g_hwnd, TIMER_FRAME, 15, NULL);
        g_timer_on = true;
    }
}

static void sync_ui_from_job(void)
{
    if (g_ui.page != PAGE_PROGRESS)
        return;
    if (g_finishing) {
        g_ui.progress = 1.0f;
        g_ui.progress_cancelable = false;
        return;
    }
    bool un = g_args.uninstall;
    Phase ph = (Phase)g_job.phase;
    const char *status = "Getting ready";
    float progress = -1.0f;
    g_ui.progress_detail[0] = 0;
    if (ph == PH_LEGACY) {
        status = "Removing the previous version";
    } else if (ph == PH_COPY) {
        status = "Copying files";
        if (g_setup.total_bytes > 0) {
            float base = g_setup.legacy_uninstaller ? 0.10f : 0.02f;
            progress = base + (0.96f - base) * (float)((double)ks_atomic_load64(&g_job.ex.bytes_done) / (double)g_setup.total_bytes);
        }
        int32_t fi = ks_atomic_load32(&g_job.ex.current_file);
        if (fi >= 0 && fi < g_payload.file_count) {
            const char *path = g_payload.files[fi].path;
            ks_strlcpy(g_ui.progress_detail, path, sizeof g_ui.progress_detail);
            for (char *c = g_ui.progress_detail; *c; c++)
                if (*c == '/')
                    *c = '\\';
        }
    } else if (ph == PH_FINISH) {
        status = un ? "Cleaning up" : "Finishing up";
        progress = un ? 0.99f : 0.97f;
    } else if (ph == PH_REMOVE) {
        status = "Removing files";
        int64_t total = ks_atomic_load64(&g_job.files_total);
        if (total > 0)
            progress = 0.9f * (float)((double)ks_atomic_load64(&g_job.files_done) / (double)total);
    } else if (ph == PH_DATA) {
        status = "Deleting your chats, macros and settings";
        progress = 0.95f;
    }
    if (g_job.ex.cancel && !g_job.ex.failed)
        status = "Canceling";
    ks_strlcpy(g_ui.progress_status, status, sizeof g_ui.progress_status);
    if (progress >= 0.0f || g_ui.progress < 0.0f)
        g_ui.progress = progress;
    g_ui.progress_cancelable = !un && ph != PH_FINISH && !g_job.ex.cancel;
}

static void render(HDC target)
{
    if (!g_main.px)
        return;
    ui_render(&g_ui, &g_main, &g_scratch);
    GdiFlush();
    BitBlt(target, 0, 0, g_client_w, g_client_h, (HDC)g_main.native, 0, 0, SRCCOPY);
}

static void show_finished_page(void)
{
    g_finishing = false;
    g_ui.data_deleted = g_job.opt_delete_data;
    ui_set_page(&g_ui, g_args.uninstall ? PAGE_REMOVED : PAGE_DONE);
}

static void frame(void)
{
    sync_ui_from_job();
    if (g_finishing) {
        double t = now_seconds();
        if (g_ui.progress_shown < 0.999f)
            g_full_since = 0.0;
        else if (g_full_since == 0.0)
            g_full_since = t;
        if ((g_full_since != 0.0 && t - g_full_since > 0.15) || t > g_finish_deadline)
            show_finished_page();
    }
    /* Waiting for Kestrel to quit after asking it to. */
    if (g_ui.page == PAGE_APP_RUNNING && g_ui.app_closing && !g_ui.app_unresponsive) {
        if (wait_processes(&g_running, 0)) {
            log_line("the app closed");
            close_process_set(&g_running);
            g_ui.app_closing = false;
            host_ui_action(&g_ui, PAGE_APP_RUNNING, W_NONE);   /* proceed */
        } else if (now_seconds() - g_close_started > CLOSE_TIMEOUT_MS / 1000.0) {
            log_line("the app did not close in time");
            g_ui.app_unresponsive = true;
        }
    }
    bool moving = ui_tick(&g_ui, now_seconds());
    bool busy = g_job.status == JOB_RUNNING || g_finishing || (g_ui.page == PAGE_APP_RUNNING && g_ui.app_closing);
    InvalidateRect(g_hwnd, NULL, FALSE);
    if (!moving && !busy && g_timer_on) {
        KillTimer(g_hwnd, TIMER_FRAME);
        g_timer_on = false;
    }
}

/* ── UI actions ─────────────────────────────────────────────────────── */

static void start_job_ui(void)
{
    ks_format(g_ui.progress_title, sizeof g_ui.progress_title, g_args.uninstall ? "Removing %s" : "Installing %s", g_ui.app_name);
    if (!g_args.uninstall && g_ui.existing_version[0])
        ks_format(g_ui.progress_title, sizeof g_ui.progress_title,
                  ks_strcmp(g_ui.existing_version, g_ui.app_version) == 0 ? "Reinstalling %s" : "Updating %s",
                  g_ui.app_name);
    ks_strlcpy(g_ui.progress_status, "Getting ready", sizeof g_ui.progress_status);
    g_ui.progress = -1.0f;
    g_ui.progress_detail[0] = 0;
    g_ui.progress_cancelable = !g_args.uninstall;
    ui_set_page(&g_ui, PAGE_PROGRESS);
    if (!job_start()) {
        ks_strlcpy(g_ui.error_title, "Couldn't start", sizeof g_ui.error_title);
        ks_strlcpy(g_ui.error_text, "Setup couldn't start its work thread.", sizeof g_ui.error_text);
        ui_set_page(&g_ui, PAGE_ERROR);
    }
    ensure_timer();
}

/* Go ahead unless the app is running from the install folder. */
static void begin(void)
{
    close_process_set(&g_running);
    find_processes_in(g_setup.install_dir, &g_running);
    if (g_running.count > 0) {
        g_ui.app_closing = false;
        g_ui.app_unresponsive = false;
        ui_set_page(&g_ui, PAGE_APP_RUNNING);
        ensure_timer();
        return;
    }
    start_job_ui();
}

static void browse_for_folder(void)
{
    IFileOpenDialog *dlg = NULL;
    static const CLSID clsid = { 0xdc1c5a9c, 0xe88a, 0x4dde, { 0xa5, 0xa1, 0x60, 0xf8, 0x2a, 0x20, 0xae, 0xf7 } };
    static const IID iid = { 0xd57c7288, 0xd4ad, 0x4768, { 0xbe, 0x02, 0x9d, 0x96, 0x95, 0x32, 0xd9, 0x60 } };
    static const IID iid_item = { 0x43826d1e, 0xe718, 0x42ee, { 0xbc, 0x55, 0xa1, 0xe2, 0x61, 0xc3, 0x7b, 0xfe } };
    if (FAILED(CoCreateInstance(&clsid, NULL, CLSCTX_INPROC_SERVER, &iid, (void **)&dlg)) || !dlg)
        return;
    FILEOPENDIALOGOPTIONS opts = 0;
    dlg->lpVtbl->GetOptions(dlg, &opts);
    dlg->lpVtbl->SetOptions(dlg, opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
    dlg->lpVtbl->SetTitle(dlg, L"Choose where to install");
    WCHAR *start = w_parent(g_setup.install_dir);
    while (start && !dir_exists(start)) {
        WCHAR *up = w_parent(start);
        ks_free(start);
        start = up;
    }
    if (start) {
        IShellItem *item = NULL;
        if (SUCCEEDED(SHCreateItemFromParsingName(start, NULL, &iid_item, (void **)&item)) && item) {
            dlg->lpVtbl->SetFolder(dlg, item);
            item->lpVtbl->Release(item);
        }
        ks_free(start);
    }
    if (SUCCEEDED(dlg->lpVtbl->Show(dlg, g_hwnd))) {
        IShellItem *result = NULL;
        if (SUCCEEDED(dlg->lpVtbl->GetResult(dlg, &result)) && result) {
            PWSTR path = NULL;
            if (SUCCEEDED(result->lpVtbl->GetDisplayName(result, SIGDN_FILESYSPATH, &path)) && path) {
                WCHAR *full = w_full_path(path);
                /* Like most installers: picking "D:\Apps" means
                   "D:\Apps\Kestrel", unless the folder is already that. */
                if (full && !w_eq_nocase(w_basename(full), g_setup.install_dir_name)) {
                    WCHAR *joined = w_join(full, g_setup.install_dir_name);
                    ks_free(full);
                    full = joined;
                }
                if (full) {
                    free_w(&g_setup.install_dir);
                    g_setup.install_dir = full;
                    set_utf8(g_ui.install_dir, sizeof g_ui.install_dir, full);
                }
                CoTaskMemFree(path);
            }
            result->lpVtbl->Release(result);
        }
    }
    dlg->lpVtbl->Release(dlg);
}

void host_ui_action(Ui *ui, PageId page, WidgetId w)
{
    switch (page) {
    case PAGE_WELCOME:
    case PAGE_LICENSE:
        if (w == W_PRIMARY)
            begin();
        else if (w == W_SECONDARY)
            DestroyWindow(g_hwnd);
        else if (w == W_CHANGE_DIR)
            browse_for_folder();
        break;
    case PAGE_UNINSTALL:
        if (w == W_PRIMARY)
            begin();
        else if (w == W_SECONDARY)
            DestroyWindow(g_hwnd);
        break;
    case PAGE_APP_RUNNING:
        if (w == W_NONE) {                     /* it closed */
            start_job_ui();
        } else if (w == W_PRIMARY) {
            if (ui->app_unresponsive) {
                terminate_processes(&g_running);
                close_process_set(&g_running);
                ui->app_unresponsive = false;
                start_job_ui();
            } else {
                bool asked = request_quit(&g_running, g_setup.quit_message);
                ui->app_closing = true;
                /* Nothing was listening, so there is nothing to wait for:
                   the next frame either finds it gone or offers to end it. */
                g_close_started = asked ? now_seconds() : now_seconds() - CLOSE_TIMEOUT_MS / 1000.0;
                ensure_timer();
            }
        } else if (w == W_SECONDARY) {
            close_process_set(&g_running);
            ui->app_closing = ui->app_unresponsive = false;
            ui_set_page(ui, g_args.uninstall ? PAGE_UNINSTALL : PAGE_WELCOME);
        }
        break;
    case PAGE_PROGRESS:
        if (w == W_SECONDARY && ui->progress_cancelable) {
            log_line("cancel requested");
            g_job.ex.cancel = 1;
            ui->progress_cancelable = false;
        }
        break;
    case PAGE_DONE:
        if (w == W_PRIMARY)
            launch_app();
        DestroyWindow(g_hwnd);
        break;
    case PAGE_ERROR:
        if (w == W_PRIMARY)
            begin();
        else if (w == W_SECONDARY)
            DestroyWindow(g_hwnd);
        else if (w == W_LINK)
            ShellExecuteW(g_hwnd, L"open", log_path(), NULL, NULL, SW_SHOWNORMAL);
        break;
    case PAGE_CANCELED:
    case PAGE_REMOVED:
        DestroyWindow(g_hwnd);
        break;
    default:
        break;
    }
    ensure_timer();
}

void host_ui_set_cursor(Ui *ui, CursorKind cursor)
{
    (void)ui;
    g_cursor = LoadCursorW(NULL, cursor == CURSOR_HAND ? IDC_HAND : IDC_ARROW);
    SetCursor(g_cursor);
}

static void on_job_done(void)
{
    if (g_job.thread_started) {
        ks_thread_join(g_job.thread);
        g_job.thread_started = false;
    }
    JobStatus st = (JobStatus)g_job.status;
    if (st == JOB_OK) {
        g_ui.progress = 1.0f;
        if (g_ui.page == PAGE_PROGRESS) {
            g_finishing = true;
            g_full_since = 0.0;
            g_finish_deadline = now_seconds() + 0.9;
        } else {
            show_finished_page();
        }
    } else if (st == JOB_CANCELED) {
        ui_set_page(&g_ui, PAGE_CANCELED);
    } else {
        ks_strlcpy(g_ui.error_title, g_job.error_title[0] ? g_job.error_title : "Something went wrong", sizeof g_ui.error_title);
        ks_strlcpy(g_ui.error_text, g_job.error_text[0] ? g_job.error_text : "Setup ran into a problem. The log has the details.",
                   sizeof g_ui.error_text);
        ui_set_page(&g_ui, PAGE_ERROR);
    }
    ensure_timer();
}

/* ── Window: messages ───────────────────────────────────────────────── */

typedef UINT(WINAPI *GetDpiForWindowFn)(HWND);
typedef BOOL(WINAPI *AdjustWindowRectExForDpiFn)(LPRECT, DWORD, BOOL, DWORD, UINT);

static const DWORD WINDOW_STYLE = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;

static SIZE window_size_for_dpi(UINT dpi)
{
    RECT r = { 0, 0, (LONG)(UI_WIDTH * dpi / 96), (LONG)(UI_HEIGHT * dpi / 96) };
    HMODULE user = GetModuleHandleW(L"user32.dll");
    AdjustWindowRectExForDpiFn adjust = user ? (AdjustWindowRectExForDpiFn)(void *)GetProcAddress(user, "AdjustWindowRectExForDpi") : NULL;
    if (adjust)
        adjust(&r, WINDOW_STYLE, FALSE, 0, dpi);
    else
        AdjustWindowRectEx(&r, WINDOW_STYLE, FALSE, 0);
    SIZE s = { r.right - r.left, r.bottom - r.top };
    return s;
}

static void apply_dark_title_bar(HWND hwnd)
{
    BOOL dark = TRUE;
    if (FAILED(DwmSetWindowAttribute(hwnd, 20, &dark, sizeof dark)))       /* DWMWA_USE_IMMERSIVE_DARK_MODE */
        DwmSetWindowAttribute(hwnd, 19, &dark, sizeof dark);               /* the same, before Windows 10 20H1 */
    COLORREF caption = RGB(0x0B, 0x0B, 0x0E), text = RGB(0xF2, 0xF2, 0xF4), border = RGB(0x26, 0x26, 0x2D);
    DwmSetWindowAttribute(hwnd, 35, &caption, sizeof caption);             /* DWMWA_CAPTION_COLOR, Windows 11 */
    DwmSetWindowAttribute(hwnd, 36, &text, sizeof text);                   /* DWMWA_TEXT_COLOR */
    DwmSetWindowAttribute(hwnd, 34, &border, sizeof border);               /* DWMWA_BORDER_COLOR */
}

static KeyCode map_key(WPARAM vk)
{
    switch (vk) {
    case VK_TAB: return KEY_TAB;
    case VK_RETURN: return KEY_ENTER;
    case VK_ESCAPE: return KEY_ESCAPE;
    case VK_SPACE: return KEY_SPACE;
    case VK_UP: return KEY_UP;
    case VK_DOWN: return KEY_DOWN;
    case VK_PRIOR: return KEY_PAGE_UP;
    case VK_NEXT: return KEY_PAGE_DOWN;
    case VK_HOME: return KEY_HOME;
    case VK_END: return KEY_END;
    default: return KEY_OTHER;
    }
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        render(dc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_ERASEBKGND:
        return 1;
    case WM_SIZE:
        if (wp != SIZE_MINIMIZED) {
            resize_canvases(LOWORD(lp), HIWORD(lp));
            InvalidateRect(hwnd, NULL, FALSE);
        }
        return 0;
    case WM_DPICHANGED: {
        g_dpi = HIWORD(wp);
        const RECT *suggested = (const RECT *)lp;
        SIZE s = window_size_for_dpi(g_dpi);
        g_client_w = g_client_h = 0;     /* force a rebuild at the new scale */
        SetWindowPos(hwnd, NULL, suggested->left, suggested->top, s.cx, s.cy, SWP_NOZORDER | SWP_NOACTIVATE);
        RECT rc;
        GetClientRect(hwnd, &rc);
        resize_canvases(rc.right, rc.bottom);
        InvalidateRect(hwnd, NULL, FALSE);
        return 0;
    }
    case WM_TIMER:
        if (wp == TIMER_FRAME)
            frame();
        return 0;
    case WM_APP_JOB_DONE:
        on_job_done();
        return 0;
    case WM_MOUSEMOVE: {
        TRACKMOUSEEVENT tme = { sizeof tme, TME_LEAVE, hwnd, 0 };
        TrackMouseEvent(&tme);
        ui_mouse_move(&g_ui, (float)(short)LOWORD(lp), (float)(short)HIWORD(lp));
        ensure_timer();
        return 0;
    }
    case WM_MOUSELEAVE:
        ui_mouse_leave(&g_ui);
        ensure_timer();
        return 0;
    case WM_LBUTTONDOWN:
        SetCapture(hwnd);
        ui_mouse_down(&g_ui, (float)(short)LOWORD(lp), (float)(short)HIWORD(lp));
        ensure_timer();
        return 0;
    case WM_LBUTTONUP:
        ReleaseCapture();
        ui_mouse_up(&g_ui, (float)(short)LOWORD(lp), (float)(short)HIWORD(lp));
        ensure_timer();
        return 0;
    case WM_MOUSEWHEEL:
        ui_mouse_wheel(&g_ui, (float)(short)HIWORD(wp) / (float)WHEEL_DELTA);
        ensure_timer();
        return 0;
    case WM_KEYDOWN: {
        KeyCode k = map_key(wp);
        if (k != KEY_OTHER) {
            ui_key_down(&g_ui, k, (GetKeyState(VK_SHIFT) & 0x8000) != 0);
            ensure_timer();
            return 0;
        }
        break;
    }
    case WM_SETCURSOR:
        if (LOWORD(lp) == HTCLIENT) {
            SetCursor(g_cursor ? g_cursor : LoadCursorW(NULL, IDC_ARROW));
            return TRUE;
        }
        break;
    case WM_CLOSE:
        if (g_job.status == JOB_RUNNING) {
            /* The title bar's close button does what Cancel does. */
            if (g_ui.page == PAGE_PROGRESS && g_ui.progress_cancelable)
                host_ui_action(&g_ui, PAGE_PROGRESS, W_SECONDARY);
            return 0;
        }
        DestroyWindow(hwnd);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static int run_window(void)
{
    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof wc);
    wc.cbSize = sizeof wc;
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = g_inst;
    wc.hIcon = (HICON)LoadImageW(g_inst, MAKEINTRESOURCEW(IDI_APP), IMAGE_ICON, GetSystemMetrics(SM_CXICON),
                                 GetSystemMetrics(SM_CYICON), 0);
    wc.hIconSm = (HICON)LoadImageW(g_inst, MAKEINTRESOURCEW(IDI_APP), IMAGE_ICON, GetSystemMetrics(SM_CXSMICON),
                                   GetSystemMetrics(SM_CYSMICON), 0);
    wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
    wc.hbrBackground = CreateSolidBrush(RGB(0x0B, 0x0B, 0x0E));
    wc.lpszClassName = L"KestrelSetupWindow";
    if (!RegisterClassExW(&wc))
        return 1;

    WCHAR title[160];
    WCHAR *t = w_cat(g_setup.app_name, g_args.uninstall ? L" Uninstall" : L" Setup");
    size_t tn = KS_MIN(w_len(t), (size_t)KS_ARRAY_LEN(title) - 1);
    memcpy(title, t, tn * sizeof(WCHAR));
    title[tn] = 0;
    ks_free(t);

    g_hwnd = CreateWindowExW(0, wc.lpszClassName, title, WINDOW_STYLE, CW_USEDEFAULT, CW_USEDEFAULT, 400, 300, NULL,
                             NULL, g_inst, NULL);
    if (!g_hwnd)
        return 1;
    apply_dark_title_bar(g_hwnd);

    HMODULE user = GetModuleHandleW(L"user32.dll");
    GetDpiForWindowFn get_dpi = user ? (GetDpiForWindowFn)(void *)GetProcAddress(user, "GetDpiForWindow") : NULL;
    g_dpi = get_dpi ? get_dpi(g_hwnd) : 96;
    if (!g_dpi)
        g_dpi = 96;

    /* Centre on the monitor under the pointer: where the user just
       double-clicked, which is where they are looking. */
    POINT cursor;
    GetCursorPos(&cursor);
    HMONITOR mon = MonitorFromPoint(cursor, MONITOR_DEFAULTTOPRIMARY);
    MONITORINFO mi = { sizeof mi };
    GetMonitorInfoW(mon, &mi);
    SIZE s = window_size_for_dpi(g_dpi);
    int x = mi.rcWork.left + (mi.rcWork.right - mi.rcWork.left - s.cx) / 2;
    int y = mi.rcWork.top + (mi.rcWork.bottom - mi.rcWork.top - s.cy) / 2;
    SetWindowPos(g_hwnd, NULL, x, y, s.cx, s.cy, SWP_NOZORDER | SWP_NOACTIVATE);
    if (get_dpi && get_dpi(g_hwnd) != g_dpi) {
        /* Moved onto a monitor with a different scale. */
        g_dpi = get_dpi(g_hwnd);
        s = window_size_for_dpi(g_dpi);
        SetWindowPos(g_hwnd, NULL, x, y, s.cx, s.cy, SWP_NOZORDER | SWP_NOACTIVATE);
    }
    RECT rc;
    GetClientRect(g_hwnd, &rc);
    resize_canvases(rc.right, rc.bottom);
    g_ui.now = now_seconds();
    g_ui.page_started = g_ui.now - 10.0;
    ShowWindow(g_hwnd, SW_SHOWNORMAL);
    SetForegroundWindow(g_hwnd);
    UpdateWindow(g_hwnd);
    ensure_timer();

    MSG m;
    while (GetMessageW(&m, NULL, 0, 0) > 0) {
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    free_canvas(&g_main, &g_bmp_main);
    free_canvas(&g_scratch, &g_bmp_scratch);
    return 0;
}

/* ── Modes ──────────────────────────────────────────────────────────── */

static void init_ui_model(void)
{
    ui_init(&g_ui, g_args.uninstall);
    set_utf8(g_ui.app_name, sizeof g_ui.app_name, g_setup.app_name);
    set_utf8(g_ui.app_version, sizeof g_ui.app_version, g_setup.app_version);
    set_utf8(g_ui.install_dir, sizeof g_ui.install_dir, g_setup.install_dir);
    if (g_setup.existing_version)
        set_utf8(g_ui.existing_version, sizeof g_ui.existing_version, g_setup.existing_version);
    WCHAR *local = known_folder(&KS_FOLDERID_LocalAppData);
    if (local && g_setup.user_data_dir && g_setup.user_data_dir[0]) {
        WCHAR *data = w_join(local, g_setup.user_data_dir);
        set_utf8(g_ui.data_dir, sizeof g_ui.data_dir, data);
        ks_free(data);
    }
    ks_free(local);
    if (g_have_payload)
        g_ui.license = payload_meta(&g_payload, "license");
    if (assets_load_logo(&g_logo))
        g_ui.logo = g_logo;

    /* Keep the choices an update's user made last time. */
    WCHAR *dat_path = w_join(g_setup.install_dir, DAT_NAME);
    char *dat = read_text_file(dat_path);
    if (dat) {
        WCHAR *d = dat_get(dat, "shortcut_desktop"), *s = dat_get(dat, "shortcut_startup");
        if (d)
            g_ui.opt_desktop = d[0] == L'1';
        if (s)
            g_ui.opt_startup = s[0] == L'1';
        ks_free(d);
        ks_free(s);
        ks_free(dat);
    }
    ks_free(dat_path);
    if (g_args.desktop >= 0)
        g_ui.opt_desktop = g_args.desktop == 1;
    if (g_args.startup >= 0)
        g_ui.opt_startup = g_args.startup == 1;
    g_ui.opt_delete_data = g_args.delete_data;
    g_ui.check_t[W_OPT_DESKTOP] = g_ui.opt_desktop ? 1.0f : 0.0f;
    g_ui.check_t[W_OPT_STARTUP] = g_ui.opt_startup ? 1.0f : 0.0f;
    g_ui.check_t[W_OPT_DELETE_DATA] = g_ui.opt_delete_data ? 1.0f : 0.0f;
}

static int run_silent(void)
{
    g_job.opt_desktop = g_ui.opt_desktop;
    g_job.opt_startup = g_ui.opt_startup;
    g_job.opt_delete_data = g_ui.opt_delete_data;
    ProcessSet running;
    find_processes_in(g_setup.install_dir, &running);
    if (running.count > 0) {
        bool asked = request_quit(&running, g_setup.quit_message);
        if (!wait_processes(&running, asked ? CLOSE_TIMEOUT_MS : 0)) {
            if (!g_args.force_close) {
                log_line("the app is running and did not close; use --force-close to end it");
                close_process_set(&running);
                return 3;
            }
            terminate_processes(&running);
        }
        close_process_set(&running);
    }
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    bool ok = g_args.uninstall ? uninstall_steps() : install_steps();
    CoUninitialize();
    if (ok && !g_args.uninstall && g_args.launch)
        launch_app();
    return ok ? 0 : 1;
}

/* Uninstallers that moved themselves to %TEMP% (see move_self_out_of). */
static void clean_old_temp_copies(void)
{
    WCHAR temp[MAX_PATH + 1];
    DWORD n = GetTempPathW(MAX_PATH, temp);
    if (!n || n > MAX_PATH)
        return;
    WCHAR *pattern = w_cat(temp, L"Kestrel-Uninstall-*.exe");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            WCHAR *p = w_cat(temp, fd.cFileName);
            if (!g_self_path || !w_eq_nocase(p, g_self_path))
                DeleteFileW(p);    /* fails harmlessly for one still running */
            ks_free(p);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    ks_free(pattern);
}

static int real_main(void)
{
    g_inst = GetModuleHandleW(NULL);
    QueryPerformanceFrequency(&g_freq);
    ks_crc_init();
    parse_args();

    DWORD cap = 32768;
    g_self_path = (WCHAR *)ks_alloc(cap * sizeof(WCHAR));
    if (!g_self_path || !GetModuleFileNameW(NULL, g_self_path, cap))
        return 1;

    g_self_file = CreateFileW(g_self_path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    LARGE_INTEGER size = { 0 };
    if (g_self_file != INVALID_HANDLE_VALUE)
        GetFileSizeEx(g_self_file, &size);
    g_source.ctx = NULL;
    g_source.read_at = read_self;
    g_source.size = (uint64_t)size.QuadPart;
    char err[256] = "Couldn't read the setup file.";
    g_have_payload = g_self_file != INVALID_HANDLE_VALUE && payload_open(&g_source, &g_payload, err, sizeof err);
    g_args.uninstall = !g_have_payload;

    log_open(L"Kestrel Setup.log");
    char *cl = w_to_utf8(GetCommandLineW());
    log_line("---- %s started: %s", g_args.uninstall ? "uninstaller" : "setup", cl);
    ks_free(cl);
    clean_old_temp_copies();

    if (g_args.uninstall) {
        if (g_self_file != INVALID_HANDLE_VALUE) {
            CloseHandle(g_self_file);
            g_self_file = INVALID_HANDLE_VALUE;
        }
        /* The folder this uninstaller is in (or the one given with --dir).
           A setup file whose payload is damaged also gets here, and must
           not be mistaken for an uninstaller: it isn't in an install. */
        WCHAR *dir = g_args.dir && g_args.dir[0] ? w_full_path(g_args.dir) : w_parent(g_self_path);
        if (!dir || !dir_is_ours(dir) || !load_meta_from_dat(dir)) {
            log_line("no payload, and no install here: %s", err);
            if (!g_args.silent) {
                WCHAR *m = w_from_utf8(g_args.dir ? "The uninstall information is missing, so the app can't be removed "
                                                    "automatically." : err);
                MessageBoxW(NULL, m, g_args.dir ? L"Uninstall" : L"Kestrel Setup", MB_OK | MB_ICONERROR);
                ks_free(m);
            }
            ks_free(dir);
            return 1;
        }
        free_w(&g_setup.install_dir);
        g_setup.install_dir = dir;
        g_setup.description = w_dup(L"");
        /* A process's current directory can't be deleted, and double-
           clicking the uninstaller makes it the install folder. */
        WCHAR temp[MAX_PATH + 1];
        DWORD tn = GetTempPathW(MAX_PATH, temp);
        if (tn && tn <= MAX_PATH)
            SetCurrentDirectoryW(temp);
    } else {
        load_meta_from_payload();
        choose_install_dir();
        char *u = u8(g_setup.install_dir);
        log_line("app %s, target %s", payload_meta(&g_payload, "app_version") ? payload_meta(&g_payload, "app_version") : "?", u);
        ks_free(u);
    }

    init_ui_model();

    int rc;
    if (g_args.silent) {
        rc = run_silent();
    } else {
        /* One setup window at a time. */
        HANDLE mutex = CreateMutexW(NULL, TRUE, L"Local\\KestrelSetup.SingleInstance");
        if (mutex && GetLastError() == ERROR_ALREADY_EXISTS) {
            log_line("another setup window is open; showing that one");
            HWND other = FindWindowW(L"KestrelSetupWindow", NULL);
            if (other) {
                ShowWindow(other, SW_RESTORE);
                SetForegroundWindow(other);
            }
            return 0;
        }
        CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
        rc = run_window();
        CoUninitialize();
        if (g_job.thread_started) {
            g_job.ex.cancel = 1;
            ks_thread_join(g_job.thread);
        }
        if (mutex)
            CloseHandle(mutex);
    }

    if (g_self_moved)
        MoveFileExW(g_self_moved, NULL, MOVEFILE_DELAY_UNTIL_REBOOT);   /* needs administrator rights; harmless without */
    log_line("---- exit %d", rc);
    return rc;
}

/* No C runtime: this is the process entry point (see build.py). */
void __stdcall ks_entry(void)
{
    ExitProcess((UINT)real_main());
}
