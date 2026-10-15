/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * util.c - memory, strings, formatting and the few libc/libm pieces the
 * CRT-free Windows build needs. See common.h.
 */

#include "common.h"
#include "xz/xz_config.h"

#ifdef KS_WINDOWS
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#  include <intrin.h>
#else
#  include <stdlib.h>
#endif

/* ── Memory ─────────────────────────────────────────────────────────── */

#ifdef KS_WINDOWS

void *ks_alloc(size_t size)
{
    return HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, size ? size : 1);
}

void *ks_realloc(void *ptr, size_t size)
{
    if (!ptr)
        return ks_alloc(size);
    return HeapReAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, ptr, size ? size : 1);
}

void ks_free(void *ptr)
{
    if (ptr)
        HeapFree(GetProcessHeap(), 0, ptr);
}

/*
 * The four mem* functions the compiler and xz-embedded call. Written so
 * the optimizer cannot turn their loops back into calls to themselves.
 */
#if defined(__clang__)
#  define KS_NO_BUILTINS __attribute__((no_builtin))
#elif defined(__GNUC__)
#  define KS_NO_BUILTINS __attribute__((optimize("no-tree-loop-distribute-patterns")))
#else
#  define KS_NO_BUILTINS
#  pragma function(memcpy, memmove, memset, memcmp)
#endif

KS_NO_BUILTINS void *memcpy(void *dst, const void *src, size_t n)
{
    __movsb((unsigned char *)dst, (const unsigned char *)src, n);
    return dst;
}

KS_NO_BUILTINS void *memset(void *dst, int c, size_t n)
{
    __stosb((unsigned char *)dst, (unsigned char)c, n);
    return dst;
}

KS_NO_BUILTINS void *memmove(void *dst, const void *src, size_t n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    if (d == s || n == 0)
        return dst;
    if (d < s || d >= s + n) {
        __movsb(d, s, n);
    } else {
        /* Overlapping with the destination after the source: copy
           backwards, one byte at a time. Only xz's dictionary uses this,
           for short runs. */
        volatile unsigned char *vd = d;
        while (n--)
            vd[n] = s[n];
    }
    return dst;
}

KS_NO_BUILTINS int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    for (size_t i = 0; i < n; i++) {
        if (x[i] != y[i])
            return x[i] < y[i] ? -1 : 1;
    }
    return 0;
}

#if defined(_MSC_VER) && !defined(__clang__)
/* Referenced by the compiler whenever floating point is used. */
int _fltused = 0;
#endif

#if defined(__MINGW32__) && defined(__x86_64__)
/*
 * Stack probe for frames larger than a page, normally supplied by libgcc.
 * Touches each 4 KB page between the current stack pointer and the new
 * one, in order, so the guard page grows the stack as Windows expects.
 * RAX holds the frame size; RSP is left for the caller to adjust.
 */
__asm__(
    ".text\n"
    ".globl ___chkstk_ms\n"
    "___chkstk_ms:\n"
    "    push %rcx\n"
    "    push %rax\n"
    "    cmp $0x1000, %rax\n"
    "    lea 24(%rsp), %rcx\n"
    "    jb 2f\n"
    "1:\n"
    "    sub $0x1000, %rcx\n"
    "    orq $0, (%rcx)\n"
    "    sub $0x1000, %rax\n"
    "    cmp $0x1000, %rax\n"
    "    ja 1b\n"
    "2:\n"
    "    sub %rax, %rcx\n"
    "    orq $0, (%rcx)\n"
    "    pop %rax\n"
    "    pop %rcx\n"
    "    ret\n");
#endif

#else /* !KS_WINDOWS */

void *ks_alloc(size_t size)
{
    return calloc(1, size ? size : 1);
}

void *ks_realloc(void *ptr, size_t size)
{
    return realloc(ptr, size ? size : 1);
}

void ks_free(void *ptr)
{
    free(ptr);
}

#endif

/* ── Strings ────────────────────────────────────────────────────────── */

size_t ks_strlen(const char *s)
{
    size_t n = 0;
    if (!s)
        return 0;
    while (s[n])
        n++;
    return n;
}

int ks_strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return (unsigned char)*a - (unsigned char)*b;
}

static char ascii_lower(char c)
{
    return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
}

bool ks_streq_nocase(const char *a, const char *b)
{
    while (*a && *b) {
        if (ascii_lower(*a) != ascii_lower(*b))
            return false;
        a++;
        b++;
    }
    return *a == *b;
}

bool ks_starts_with(const char *s, const char *prefix)
{
    while (*prefix) {
        if (*s++ != *prefix++)
            return false;
    }
    return true;
}

size_t ks_strlcpy(char *dst, const char *src, size_t cap)
{
    size_t n = ks_strlen(src);
    if (cap) {
        size_t c = n < cap - 1 ? n : cap - 1;
        memcpy(dst, src, c);
        dst[c] = 0;
    }
    return n;
}

size_t ks_strlcat(char *dst, const char *src, size_t cap)
{
    size_t d = 0;
    while (d < cap && dst[d])
        d++;
    if (d == cap)
        return d + ks_strlen(src);
    return d + ks_strlcpy(dst + d, src, cap - d);
}

char *ks_strndup(const char *s, size_t n)
{
    char *r = (char *)ks_alloc(n + 1);
    if (r) {
        memcpy(r, s, n);
        r[n] = 0;
    }
    return r;
}

char *ks_strdup(const char *s)
{
    return ks_strndup(s ? s : "", ks_strlen(s));
}

/* ── Formatting ─────────────────────────────────────────────────────── */

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
} Out;

static void out_char(Out *o, char c)
{
    if (o->len + 1 < o->cap)
        o->buf[o->len] = c;
    o->len++;
}

static void out_str(Out *o, const char *s)
{
    while (*s)
        out_char(o, *s++);
}

static void out_u64(Out *o, uint64_t v, unsigned base)
{
    char tmp[24];
    int n = 0;
    do {
        unsigned digit = (unsigned)(v % base);
        tmp[n++] = (char)(digit < 10 ? '0' + digit : 'a' + digit - 10);
        v /= base;
    } while (v);
    while (n)
        out_char(o, tmp[--n]);
}

int ks_vformat(char *buf, size_t cap, const char *fmt, va_list ap)
{
    Out o = { buf, cap, 0 };
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') {
            out_char(&o, *p);
            continue;
        }
        p++;
        int longs = 0;
        while (*p == 'l') {
            longs++;
            p++;
        }
        switch (*p) {
        case 's': {
            const char *s = va_arg(ap, const char *);
            out_str(&o, s ? s : "(null)");
            break;
        }
        case 'c':
            out_char(&o, (char)va_arg(ap, int));
            break;
        case 'd': {
            int64_t v = longs >= 2 ? va_arg(ap, long long) : (longs ? va_arg(ap, long) : va_arg(ap, int));
            if (v < 0) {
                out_char(&o, '-');
                out_u64(&o, (uint64_t)(-(v + 1)) + 1, 10);
            } else {
                out_u64(&o, (uint64_t)v, 10);
            }
            break;
        }
        case 'u': {
            uint64_t v = longs >= 2 ? va_arg(ap, unsigned long long)
                                    : (longs ? va_arg(ap, unsigned long) : va_arg(ap, unsigned));
            out_u64(&o, v, 10);
            break;
        }
        case 'x': {
            uint64_t v = longs >= 2 ? va_arg(ap, unsigned long long)
                                    : (longs ? va_arg(ap, unsigned long) : va_arg(ap, unsigned));
            out_u64(&o, v, 16);
            break;
        }
        case '%':
            out_char(&o, '%');
            break;
        case 0:
            p--;
            break;
        default:
            out_char(&o, '%');
            out_char(&o, *p);
            break;
        }
    }
    if (cap)
        buf[o.len < cap ? o.len : cap - 1] = 0;
    return (int)o.len;
}

int ks_format(char *buf, size_t cap, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = ks_vformat(buf, cap, fmt, ap);
    va_end(ap);
    return n;
}

void ks_format_size(char *buf, size_t cap, uint64_t bytes)
{
    static const char *units[] = { "bytes", "KB", "MB", "GB", "TB" };
    int unit = 0;
    uint64_t whole = bytes, tenths = 0;
    while (whole >= 1024 && unit < 4) {
        tenths = (whole % 1024) * 10 / 1024;
        whole /= 1024;
        unit++;
    }
    /* A no-break space between number and unit, so "180 MB" never
       wraps across two lines. */
    if (unit == 0 || whole >= 100)
        ks_format(buf, cap, "%llu\xC2\xA0%s", (unsigned long long)whole, units[unit]);
    else
        ks_format(buf, cap, "%llu.%llu\xC2\xA0%s", (unsigned long long)whole, (unsigned long long)tenths, units[unit]);
}

uint32_t ks_utf8_next(const char **ps, const char *end)
{
    const unsigned char *s = (const unsigned char *)*ps;
    const unsigned char *e = (const unsigned char *)end;
    uint32_t c = *s++;
    int extra = 0;
    if (c < 0x80) {
        *ps = (const char *)s;
        return c;
    } else if ((c & 0xE0) == 0xC0) {
        c &= 0x1F;
        extra = 1;
    } else if ((c & 0xF0) == 0xE0) {
        c &= 0x0F;
        extra = 2;
    } else if ((c & 0xF8) == 0xF0) {
        c &= 0x07;
        extra = 3;
    } else {
        *ps = (const char *)s;
        return 0xFFFD;
    }
    while (extra--) {
        if (s >= e || (*s & 0xC0) != 0x80) {
            *ps = (const char *)s;
            return 0xFFFD;
        }
        c = (c << 6) | (*s++ & 0x3F);
    }
    *ps = (const char *)s;
    return c;
}

/* ── Math ───────────────────────────────────────────────────────────── */

#if defined(_M_X64) || defined(__x86_64__) || defined(_M_IX86) || defined(__i386__)
/* The SSE instruction directly: sqrtf() itself would be a C library call
   (it may set errno), and there is no C library on the Windows build. */
#  include <xmmintrin.h>
float ks_sqrtf(float x)
{
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x)));
}
#else
float ks_sqrtf(float x)
{
    return __builtin_sqrtf(x);
}
#endif

float ks_floorf(float x)
{
    if (x >= 8388608.0f || x <= -8388608.0f)
        return x;                       /* already integral */
    float t = (float)(int32_t)x;
    return t > x ? t - 1.0f : t;
}

float ks_ceilf(float x)
{
    float f = ks_floorf(x);
    return f < x ? f + 1.0f : f;
}

float ks_fabsf(float x)
{
    return x < 0 ? -x : x;
}

float ks_roundf(float x)
{
    return ks_floorf(x + 0.5f);
}

float ks_ease_out_cubic(float t)
{
    t = KS_CLAMP(t, 0.0f, 1.0f);
    float u = 1.0f - t;
    return 1.0f - u * u * u;
}

float ks_ease_in_out_cubic(float t)
{
    t = KS_CLAMP(t, 0.0f, 1.0f);
    if (t < 0.5f)
        return 4.0f * t * t * t;
    float u = -2.0f * t + 2.0f;
    return 1.0f - u * u * u / 2.0f;
}

/* ── Checksums ──────────────────────────────────────────────────────── */

void ks_crc_init(void)
{
    xz_crc32_init();
    xz_crc64_init();
}

uint32_t ks_crc32(const void *data, size_t size, uint32_t crc)
{
    return xz_crc32((const uint8_t *)data, size, crc);
}
