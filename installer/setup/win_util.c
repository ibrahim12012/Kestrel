/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * win_util.c - Windows helpers: strings, log, files, registry, shortcuts,
 * processes. See win32.h.
 *
 * File operations go through \\?\ paths, so a deep folder under a long
 * user name never runs into the 260-character limit.
 */

#include "win32.h"

#include <objbase.h>
#include <objidl.h>
#include <propidl.h>
#include <propsys.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <tlhelp32.h>

/* GUIDs, defined here so no uuid library is needed at link time. */
const GUID KS_FOLDERID_UserProgramFiles = { 0x5cd7aee2, 0x2219, 0x4a67, { 0xb8, 0x5d, 0x6c, 0x9c, 0xe1, 0x56, 0x60, 0xcb } };
const GUID KS_FOLDERID_LocalAppData = { 0xf1b32785, 0x6fba, 0x4fcf, { 0x9d, 0x55, 0x7b, 0x8e, 0x7f, 0x15, 0x70, 0x91 } };
const GUID KS_FOLDERID_Programs = { 0xa77f5d77, 0x2e2b, 0x44c3, { 0xa6, 0xa2, 0xab, 0xa6, 0x01, 0x05, 0x4a, 0x51 } };
const GUID KS_FOLDERID_Desktop = { 0xb4bfcc3a, 0xdb2c, 0x424c, { 0xb0, 0x29, 0x7f, 0xe9, 0x9a, 0x87, 0xc6, 0x41 } };
const GUID KS_FOLDERID_Startup = { 0xb97d20bb, 0xf46a, 0x4c97, { 0xba, 0x10, 0x5e, 0x36, 0x08, 0x43, 0x08, 0x54 } };
static const CLSID KS_CLSID_ShellLink = { 0x00021401, 0x0000, 0x0000, { 0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 } };
static const IID KS_IID_IShellLinkW = { 0x000214f9, 0x0000, 0x0000, { 0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 } };
static const IID KS_IID_IPersistFile = { 0x0000010b, 0x0000, 0x0000, { 0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 } };
static const IID KS_IID_IPropertyStore = { 0x886d8eeb, 0x8cf2, 0x4446, { 0x8d, 0x02, 0xcd, 0xba, 0x1d, 0xbd, 0xcf, 0x99 } };
static const PROPERTYKEY KS_PKEY_AppUserModel_ID = { { 0x9f4c2855, 0x9f79, 0x4b39, { 0xa8, 0xd0, 0xe1, 0xd4, 0x2d, 0xe1, 0xd5, 0xf3 } }, 5 };

/* ── Wide strings ───────────────────────────────────────────────────── */

size_t w_len(const WCHAR *s)
{
    size_t n = 0;
    if (s)
        while (s[n])
            n++;
    return n;
}

WCHAR *w_dup(const WCHAR *s)
{
    size_t n = w_len(s);
    WCHAR *r = (WCHAR *)ks_alloc((n + 1) * sizeof(WCHAR));
    if (r && n)
        memcpy(r, s, n * sizeof(WCHAR));
    return r;
}

WCHAR *w_cat(const WCHAR *a, const WCHAR *b)
{
    size_t na = w_len(a), nb = w_len(b);
    WCHAR *r = (WCHAR *)ks_alloc((na + nb + 1) * sizeof(WCHAR));
    if (!r)
        return NULL;
    if (na)
        memcpy(r, a, na * sizeof(WCHAR));
    if (nb)
        memcpy(r + na, b, nb * sizeof(WCHAR));
    return r;
}

WCHAR *w_join(const WCHAR *dir, const WCHAR *name)
{
    size_t nd = w_len(dir);
    bool slash = nd > 0 && dir[nd - 1] != L'\\';
    WCHAR *tmp = slash ? w_cat(dir, L"\\") : w_dup(dir);
    WCHAR *r = w_cat(tmp, name);
    ks_free(tmp);
    return r;
}

bool w_eq_nocase(const WCHAR *a, const WCHAR *b)
{
    return CompareStringOrdinal(a, -1, b, -1, TRUE) == CSTR_EQUAL;
}

bool w_starts_with_nocase(const WCHAR *s, const WCHAR *prefix)
{
    size_t np = w_len(prefix);
    if (w_len(s) < np)
        return false;
    return CompareStringOrdinal(s, (int)np, prefix, (int)np, TRUE) == CSTR_EQUAL;
}

WCHAR *w_from_utf8(const char *s)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (n <= 0)
        return w_dup(L"");
    WCHAR *r = (WCHAR *)ks_alloc((size_t)n * sizeof(WCHAR));
    if (r)
        MultiByteToWideChar(CP_UTF8, 0, s, -1, r, n);
    return r;
}

char *w_to_utf8(const WCHAR *s)
{
    int n = WideCharToMultiByte(CP_UTF8, 0, s, -1, NULL, 0, NULL, NULL);
    if (n <= 0)
        return ks_strdup("");
    char *r = (char *)ks_alloc((size_t)n);
    if (r)
        WideCharToMultiByte(CP_UTF8, 0, s, -1, r, n, NULL, NULL);
    return r;
}

WCHAR *w_parent(const WCHAR *path)
{
    size_t n = w_len(path);
    while (n > 0 && path[n - 1] == L'\\')
        n--;
    while (n > 0 && path[n - 1] != L'\\')
        n--;
    if (n == 0)
        return NULL;
    /* Keep "C:\" whole; drop the separator otherwise. */
    size_t keep = (n == 3 && path[1] == L':') ? n : n - 1;
    if (keep == 0)
        return NULL;
    WCHAR *r = (WCHAR *)ks_alloc((keep + 1) * sizeof(WCHAR));
    if (r)
        memcpy(r, path, keep * sizeof(WCHAR));
    return r;
}

const WCHAR *w_basename(const WCHAR *path)
{
    const WCHAR *b = path;
    for (const WCHAR *p = path; *p; p++)
        if (*p == L'\\' || *p == L'/')
            b = p + 1;
    return b;
}

WCHAR *w_full_path(const WCHAR *path)
{
    DWORD n = GetFullPathNameW(path, 0, NULL, NULL);
    if (!n)
        return NULL;
    WCHAR *r = (WCHAR *)ks_alloc(((size_t)n + 1) * sizeof(WCHAR));
    if (!r || !GetFullPathNameW(path, n + 1, r, NULL)) {
        ks_free(r);
        return NULL;
    }
    size_t len = w_len(r);
    while (len > 3 && r[len - 1] == L'\\')
        r[--len] = 0;
    return r;
}

WCHAR *w_long_path(const WCHAR *full)
{
    if (w_starts_with_nocase(full, L"\\\\?\\"))
        return w_dup(full);
    if (full[0] == L'\\' && full[1] == L'\\')
        return w_cat(L"\\\\?\\UNC\\", full + 2);
    return w_cat(L"\\\\?\\", full);
}

/* ── Log ────────────────────────────────────────────────────────────── */

static HANDLE g_log = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION g_log_lock;
static WCHAR g_log_path[MAX_PATH + 64];

const WCHAR *log_path(void)
{
    return g_log_path;
}

void log_open(const WCHAR *name)
{
    InitializeCriticalSection(&g_log_lock);
    DWORD n = GetTempPathW(MAX_PATH, g_log_path);
    if (!n || n > MAX_PATH)
        return;
    size_t nn = w_len(name);
    if (n + nn + 1 < KS_ARRAY_LEN(g_log_path))
        memcpy(g_log_path + n, name, (nn + 1) * sizeof(WCHAR));
    g_log = CreateFileW(g_log_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                        FILE_ATTRIBUTE_NORMAL, NULL);
}

void log_line(const char *fmt, ...)
{
    char buf[1400];
    SYSTEMTIME t;
    GetLocalTime(&t);
    int n = ks_format(buf, sizeof buf, "%d-%d%d-%d%d %d%d:%d%d:%d%d.%d%d%d  ", t.wYear, t.wMonth / 10, t.wMonth % 10,
                      t.wDay / 10, t.wDay % 10, t.wHour / 10, t.wHour % 10, t.wMinute / 10, t.wMinute % 10,
                      t.wSecond / 10, t.wSecond % 10, t.wMilliseconds / 100, (t.wMilliseconds / 10) % 10,
                      t.wMilliseconds % 10);
    va_list ap;
    va_start(ap, fmt);
    n += ks_vformat(buf + n, sizeof buf - (size_t)n - 3, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof buf - 3)
        n = (int)sizeof buf - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';
    if (g_log == INVALID_HANDLE_VALUE)
        return;
    EnterCriticalSection(&g_log_lock);
    DWORD written;
    WriteFile(g_log, buf, (DWORD)n, &written, NULL);
    LeaveCriticalSection(&g_log_lock);
}

void log_last_error(const char *what)
{
    DWORD err = GetLastError();
    WCHAR *msg = NULL;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL,
                   err, 0, (LPWSTR)&msg, 0, NULL);
    char *m = msg ? w_to_utf8(msg) : ks_strdup("");
    size_t n = ks_strlen(m);
    while (n && (m[n - 1] == '\n' || m[n - 1] == '\r' || m[n - 1] == ' '))
        m[--n] = 0;
    log_line("%s failed: error %u (%s)", what, (unsigned)err, m);
    ks_free(m);
    if (msg)
        LocalFree(msg);
    SetLastError(err);
}

/* ── Files ──────────────────────────────────────────────────────────── */

static DWORD attrs_of(const WCHAR *path)
{
    WCHAR *lp = w_long_path(path);
    DWORD a = lp ? GetFileAttributesW(lp) : INVALID_FILE_ATTRIBUTES;
    ks_free(lp);
    return a;
}

bool file_exists(const WCHAR *path)
{
    DWORD a = attrs_of(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

bool dir_exists(const WCHAR *path)
{
    DWORD a = attrs_of(path);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static HANDLE open_for_identity(const WCHAR *path)
{
    WCHAR *lp = w_long_path(path);
    HANDLE h = lp ? CreateFileW(lp, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL)
                  : INVALID_HANDLE_VALUE;
    ks_free(lp);
    return h;
}

bool same_file_object(const WCHAR *a, const WCHAR *b)
{
    HANDLE ha = open_for_identity(a), hb = open_for_identity(b);
    BY_HANDLE_FILE_INFORMATION ia, ib;
    bool same = ha != INVALID_HANDLE_VALUE && hb != INVALID_HANDLE_VALUE && GetFileInformationByHandle(ha, &ia)
                && GetFileInformationByHandle(hb, &ib) && ia.dwVolumeSerialNumber == ib.dwVolumeSerialNumber
                && ia.nFileIndexHigh == ib.nFileIndexHigh && ia.nFileIndexLow == ib.nFileIndexLow;
    if (ha != INVALID_HANDLE_VALUE)
        CloseHandle(ha);
    if (hb != INVALID_HANDLE_VALUE)
        CloseHandle(hb);
    return same;
}

bool dir_is_empty(const WCHAR *path)
{
    WCHAR *lp = w_long_path(path);
    WCHAR *pattern = w_cat(lp, L"\\*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, 0);
    bool empty = true;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.cFileName[0] == L'.' && (fd.cFileName[1] == 0 || (fd.cFileName[1] == L'.' && fd.cFileName[2] == 0)))) {
                empty = false;
                break;
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    ks_free(pattern);
    ks_free(lp);
    return empty;
}

bool make_dirs(const WCHAR *path)
{
    if (dir_exists(path))
        return true;
    WCHAR *parent = w_parent(path);
    if (parent && !dir_exists(parent) && !make_dirs(parent)) {
        ks_free(parent);
        return false;
    }
    ks_free(parent);
    WCHAR *lp = w_long_path(path);
    BOOL ok = CreateDirectoryW(lp, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
    ks_free(lp);
    return ok;
}

static bool is_dot(const WCHAR *n)
{
    return n[0] == L'.' && (n[1] == 0 || (n[1] == L'.' && n[2] == 0));
}

/* Retry briefly around sharing violations: antivirus scanners and the
   search indexer hold freshly written files open for a moment. */
static bool retry_op(bool (*op)(const WCHAR *), const WCHAR *lp)
{
    for (int attempt = 0; attempt < 20; attempt++) {
        if (op(lp))
            return true;
        DWORD e = GetLastError();
        if (e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND)
            return true;
        if (e != ERROR_SHARING_VIOLATION && e != ERROR_ACCESS_DENIED && e != ERROR_DIR_NOT_EMPTY
            && e != ERROR_LOCK_VIOLATION)
            return false;
        Sleep(50 + attempt * 25);
    }
    return false;
}

static bool op_delete_file(const WCHAR *lp)
{
    return DeleteFileW(lp) != 0;
}

static bool op_remove_dir(const WCHAR *lp)
{
    return RemoveDirectoryW(lp) != 0;
}

static bool delete_tree_long(const WCHAR *lp, volatile int64_t *progress)
{
    DWORD a = GetFileAttributesW(lp);
    if (a == INVALID_FILE_ATTRIBUTES)
        return true;
    if (a & FILE_ATTRIBUTE_READONLY)
        SetFileAttributesW(lp, a & ~(DWORD)FILE_ATTRIBUTE_READONLY);
    if (!(a & FILE_ATTRIBUTE_DIRECTORY)) {
        bool ok = retry_op(op_delete_file, lp);
        if (progress)
            (*progress)++;
        return ok;
    }
    if (a & FILE_ATTRIBUTE_REPARSE_POINT)      /* a junction or link: remove it, not its target */
        return retry_op(op_remove_dir, lp);

    bool ok = true;
    WCHAR *pattern = w_cat(lp, L"\\*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, FIND_FIRST_EX_LARGE_FETCH);
    ks_free(pattern);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (is_dot(fd.cFileName))
                continue;
            WCHAR *child = w_join(lp, fd.cFileName);
            if (!child) {
                ok = false;
                continue;
            }
            if (!delete_tree_long(child, progress)) {
                ok = false;
                log_last_error("delete");
                char *u = w_to_utf8(child);
                log_line("  could not remove %s", u);
                ks_free(u);
            }
            ks_free(child);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    if (!retry_op(op_remove_dir, lp))
        ok = false;
    return ok;
}

bool delete_tree(const WCHAR *path, volatile int64_t *progress)
{
    WCHAR *lp = w_long_path(path);
    bool ok = lp && delete_tree_long(lp, progress);
    ks_free(lp);
    return ok;
}

static int64_t count_long(const WCHAR *lp)
{
    int64_t n = 0;
    WCHAR *pattern = w_cat(lp, L"\\*");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, FIND_FIRST_EX_LARGE_FETCH);
    ks_free(pattern);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    do {
        if (is_dot(fd.cFileName))
            continue;
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
            WCHAR *child = w_join(lp, fd.cFileName);
            n += count_long(child);
            ks_free(child);
        } else if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            n++;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return n;
}

int64_t count_tree_files(const WCHAR *path)
{
    WCHAR *lp = w_long_path(path);
    int64_t n = lp ? count_long(lp) : 0;
    ks_free(lp);
    return n;
}

bool move_with_retry(const WCHAR *from, const WCHAR *to)
{
    WCHAR *lf = w_long_path(from), *lt = w_long_path(to);
    bool ok = false;
    for (int attempt = 0; attempt < 40 && lf && lt; attempt++) {
        if (MoveFileExW(lf, lt, 0)) {
            ok = true;
            break;
        }
        DWORD e = GetLastError();
        if (e != ERROR_ACCESS_DENIED && e != ERROR_SHARING_VIOLATION && e != ERROR_LOCK_VIOLATION)
            break;
        Sleep(50 + attempt * 10);
    }
    ks_free(lf);
    ks_free(lt);
    return ok;
}

char *read_text_file(const WCHAR *path)
{
    WCHAR *lp = w_long_path(path);
    HANDLE h = CreateFileW(lp, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    ks_free(lp);
    if (h == INVALID_HANDLE_VALUE)
        return NULL;
    LARGE_INTEGER size;
    char *buf = NULL;
    if (GetFileSizeEx(h, &size) && size.QuadPart < 16 * 1024 * 1024) {
        buf = (char *)ks_alloc((size_t)size.QuadPart + 1);
        DWORD got = 0;
        if (buf && (!ReadFile(h, buf, (DWORD)size.QuadPart, &got, NULL) || got != (DWORD)size.QuadPart)) {
            ks_free(buf);
            buf = NULL;
        }
    }
    CloseHandle(h);
    return buf;
}

bool write_text_file(const WCHAR *path, const char *utf8)
{
    WCHAR *lp = w_long_path(path);
    HANDLE h = CreateFileW(lp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    ks_free(lp);
    if (h == INVALID_HANDLE_VALUE)
        return false;
    DWORD n = (DWORD)ks_strlen(utf8), written = 0;
    BOOL ok = WriteFile(h, utf8, n, &written, NULL) && written == n;
    CloseHandle(h);
    return ok;
}

uint64_t disk_free_bytes(const WCHAR *path)
{
    WCHAR *p = w_dup(path);
    while (p && !dir_exists(p)) {
        WCHAR *up = w_parent(p);
        ks_free(p);
        p = up;
    }
    ULARGE_INTEGER avail;
    uint64_t r = UINT64_MAX;
    if (p && GetDiskFreeSpaceExW(p, &avail, NULL, NULL))
        r = avail.QuadPart;
    ks_free(p);
    return r;
}

/* ── Known folders ──────────────────────────────────────────────────── */

WCHAR *known_folder(const GUID *id)
{
    PWSTR p = NULL;
    WCHAR *r = NULL;
    if (SUCCEEDED(SHGetKnownFolderPath(id, 0, NULL, &p)) && p)
        r = w_dup(p);
    if (p)
        CoTaskMemFree(p);
    return r;
}

/* ── Registry ───────────────────────────────────────────────────────── */

bool reg_set_string(HKEY root, const WCHAR *key, const WCHAR *name, const WCHAR *value)
{
    HKEY hk;
    if (RegCreateKeyExW(root, key, 0, NULL, 0, KEY_SET_VALUE, NULL, &hk, NULL) != ERROR_SUCCESS)
        return false;
    LONG r = RegSetValueExW(hk, name, 0, REG_SZ, (const BYTE *)value, (DWORD)((w_len(value) + 1) * sizeof(WCHAR)));
    RegCloseKey(hk);
    return r == ERROR_SUCCESS;
}

bool reg_set_dword(HKEY root, const WCHAR *key, const WCHAR *name, DWORD value)
{
    HKEY hk;
    if (RegCreateKeyExW(root, key, 0, NULL, 0, KEY_SET_VALUE, NULL, &hk, NULL) != ERROR_SUCCESS)
        return false;
    LONG r = RegSetValueExW(hk, name, 0, REG_DWORD, (const BYTE *)&value, sizeof value);
    RegCloseKey(hk);
    return r == ERROR_SUCCESS;
}

WCHAR *reg_get_string(HKEY root, const WCHAR *key, const WCHAR *name, REGSAM view)
{
    HKEY hk;
    if (RegOpenKeyExW(root, key, 0, KEY_QUERY_VALUE | view, &hk) != ERROR_SUCCESS)
        return NULL;
    DWORD type = 0, size = 0;
    WCHAR *r = NULL;
    if (RegQueryValueExW(hk, name, NULL, &type, NULL, &size) == ERROR_SUCCESS
        && (type == REG_SZ || type == REG_EXPAND_SZ) && size < 65536) {
        r = (WCHAR *)ks_alloc(size + 2 * sizeof(WCHAR));
        if (r && RegQueryValueExW(hk, name, NULL, &type, (BYTE *)r, &size) != ERROR_SUCCESS) {
            ks_free(r);
            r = NULL;
        }
    }
    RegCloseKey(hk);
    return r;
}

bool reg_delete_tree(HKEY root, const WCHAR *key)
{
    LONG r = RegDeleteTreeW(root, key);
    if (r != ERROR_SUCCESS && r != ERROR_FILE_NOT_FOUND)
        return false;
    r = RegDeleteKeyW(root, key);
    return r == ERROR_SUCCESS || r == ERROR_FILE_NOT_FOUND;
}

bool reg_key_exists(HKEY root, const WCHAR *key, REGSAM view)
{
    HKEY hk;
    if (RegOpenKeyExW(root, key, 0, KEY_QUERY_VALUE | view, &hk) != ERROR_SUCCESS)
        return false;
    RegCloseKey(hk);
    return true;
}

/* ── Shortcuts ──────────────────────────────────────────────────────── */

bool create_shortcut(const WCHAR *lnk, const WCHAR *target, const WCHAR *workdir, const WCHAR *description,
                     const WCHAR *aumid)
{
    IShellLinkW *link = NULL;
    HRESULT hr = CoCreateInstance(&KS_CLSID_ShellLink, NULL, CLSCTX_INPROC_SERVER, &KS_IID_IShellLinkW, (void **)&link);
    if (FAILED(hr) || !link) {
        log_line("CoCreateInstance(ShellLink) failed: 0x%x", (unsigned)hr);
        return false;
    }
    link->lpVtbl->SetPath(link, target);
    link->lpVtbl->SetWorkingDirectory(link, workdir);
    link->lpVtbl->SetDescription(link, description);
    link->lpVtbl->SetIconLocation(link, target, 0);

    /* The same AppUserModelID the app sets on itself (main.py), so the
       taskbar groups its windows under this shortcut and notifications
       are attributed to Kestrel rather than to "Python". */
    if (aumid && aumid[0]) {
        IPropertyStore *store = NULL;
        if (SUCCEEDED(link->lpVtbl->QueryInterface(link, &KS_IID_IPropertyStore, (void **)&store)) && store) {
            PROPVARIANT pv;
            memset(&pv, 0, sizeof pv);
            pv.vt = VT_LPWSTR;
            pv.pwszVal = (LPWSTR)aumid;
            store->lpVtbl->SetValue(store, &KS_PKEY_AppUserModel_ID, &pv);
            store->lpVtbl->Commit(store);
            store->lpVtbl->Release(store);
        }
    }

    bool ok = false;
    IPersistFile *file = NULL;
    if (SUCCEEDED(link->lpVtbl->QueryInterface(link, &KS_IID_IPersistFile, (void **)&file)) && file) {
        hr = file->lpVtbl->Save(file, lnk, TRUE);
        ok = SUCCEEDED(hr);
        if (!ok)
            log_line("IPersistFile::Save failed: 0x%x", (unsigned)hr);
        file->lpVtbl->Release(file);
    }
    link->lpVtbl->Release(link);
    return ok;
}

/* ── Processes ──────────────────────────────────────────────────────── */

void find_processes_in(const WCHAR *dir, ProcessSet *out)
{
    memset(out, 0, sizeof(*out));
    WCHAR *prefix = w_join(dir, L"");
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE || !prefix) {
        ks_free(prefix);
        return;
    }
    DWORD self = GetCurrentProcessId();
    DWORD cap = 32768;
    WCHAR *image = (WCHAR *)ks_alloc(cap * sizeof(WCHAR));
    PROCESSENTRY32W pe;
    pe.dwSize = sizeof pe;
    for (BOOL more = Process32FirstW(snap, &pe); more && image; more = Process32NextW(snap, &pe)) {
        if (pe.th32ProcessID == self || pe.th32ProcessID == 0 || out->count >= MAX_TRACKED)
            continue;
        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | PROCESS_TERMINATE, FALSE, pe.th32ProcessID);
        if (!h)
            h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pe.th32ProcessID);
        if (!h)
            continue;
        DWORD n = cap;
        if (QueryFullProcessImageNameW(h, 0, image, &n) && w_starts_with_nocase(image, prefix)) {
            out->pid[out->count] = pe.th32ProcessID;
            out->handle[out->count] = h;
            out->count++;
            char *u = w_to_utf8(image);
            log_line("running from the install folder: pid %u %s", (unsigned)pe.th32ProcessID, u);
            ks_free(u);
        } else {
            CloseHandle(h);
        }
    }
    ks_free(image);
    CloseHandle(snap);
    ks_free(prefix);
}

void close_process_set(ProcessSet *set)
{
    for (int i = 0; i < set->count; i++)
        CloseHandle(set->handle[i]);
    set->count = 0;
}

/* Local\<quit_name>.Busy, created the first time setup asks the app to
   close and left open until setup exits. A Kestrel launched meanwhile
   sees it and exits, instead of taking the closing copy's place and
   running from the files setup is about to replace (INSTALLER_BUSY_NAME
   in app/single_instance.py). */
static HANDLE g_busy_marker;

bool request_quit(ProcessSet *set, const WCHAR *quit_name)
{
    /* The app creates a named event called Local\<quit_name> when it
       starts and keeps a thread waiting on it (app/single_instance.py), so
       setting it reaches the app whether or not it has a window on screen.
       One Kestrel runs per session, so one event covers the whole set. If
       the event isn't there, nothing in the set is listening, and the
       caller doesn't wait for an answer that won't come. */
    bool asked = false;
    WCHAR *event_name = w_cat(L"Local\\", quit_name);
    if (event_name && !g_busy_marker) {
        WCHAR *busy_name = w_cat(event_name, L".Busy");
        if (busy_name)
            g_busy_marker = CreateEventW(NULL, TRUE, FALSE, busy_name);
        ks_free(busy_name);
    }
    HANDLE ev = event_name ? OpenEventW(EVENT_MODIFY_STATE, FALSE, event_name) : NULL;
    if (!ev) {
        log_line("%d process(es) running, none listening for a quit request", set->count);
    } else {
        asked = SetEvent(ev) != 0;
        if (asked)
            log_line("asked the app to quit (%d process(es) running)", set->count);
        else
            log_last_error("SetEvent");
        CloseHandle(ev);
    }
    ks_free(event_name);
    return asked;
}

bool wait_processes(ProcessSet *set, DWORD ms)
{
    if (set->count == 0)
        return true;
    DWORD r = WaitForMultipleObjects((DWORD)set->count, set->handle, TRUE, ms);
    return r >= WAIT_OBJECT_0 && r < WAIT_OBJECT_0 + (DWORD)set->count;
}

void terminate_processes(ProcessSet *set)
{
    for (int i = 0; i < set->count; i++) {
        if (WaitForSingleObject(set->handle[i], 0) == WAIT_TIMEOUT) {
            if (!TerminateProcess(set->handle[i], 1))
                log_last_error("TerminateProcess");
            else
                log_line("ended pid %u", (unsigned)set->pid[i]);
        }
    }
    wait_processes(set, 5000);
}
