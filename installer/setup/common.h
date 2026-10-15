/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * common.h - basic types and the small runtime the installer uses in place
 * of a C library.
 *
 * The Windows build links no C runtime at all (no msvcrt, no ucrt, no
 * vcruntime): everything it needs from one is in util.c, on top of
 * kernel32. That keeps the executable small, starting instantly, and free
 * of any "the Visual C++ Redistributable is missing" failure on a clean
 * machine. The Linux build (only used for tests and screen previews) uses
 * the normal libc.
 */

#ifndef KS_COMMON_H
#define KS_COMMON_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  define KS_WINDOWS 1
#endif

#define KS_ARRAY_LEN(a) ((int)(sizeof(a) / sizeof((a)[0])))
#define KS_MIN(a, b) ((a) < (b) ? (a) : (b))
#define KS_MAX(a, b) ((a) > (b) ? (a) : (b))
#define KS_CLAMP(v, lo, hi) ((v) < (lo) ? (lo) : ((v) > (hi) ? (hi) : (v)))

/* ── Memory ─────────────────────────────────────────────────────────── */

void *ks_alloc(size_t size);            /* zero-filled; NULL on failure */
void *ks_realloc(void *ptr, size_t size);
void ks_free(void *ptr);

/* Provided by util.c on the CRT-free build, by libc elsewhere. */
void *memcpy(void *dst, const void *src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);

/* ── UTF-8 strings ──────────────────────────────────────────────────── */

size_t ks_strlen(const char *s);
int ks_strcmp(const char *a, const char *b);
bool ks_streq_nocase(const char *a, const char *b);   /* ASCII case only */
bool ks_starts_with(const char *s, const char *prefix);
size_t ks_strlcpy(char *dst, const char *src, size_t cap);
size_t ks_strlcat(char *dst, const char *src, size_t cap);
char *ks_strdup(const char *s);
char *ks_strndup(const char *s, size_t n);

/*
 * snprintf, for the handful of conversions the installer uses:
 *   %s  %d  %u  %lld  %llu  %x  %c  %%
 * Always NUL-terminates when cap > 0. Returns the length written.
 */
int ks_format(char *buf, size_t cap, const char *fmt, ...);
int ks_vformat(char *buf, size_t cap, const char *fmt, va_list ap);

/* "1.4 MB", "212 MB", "1.2 GB". */
void ks_format_size(char *buf, size_t cap, uint64_t bytes);

/* Decode one UTF-8 code point; advances *s. Invalid bytes decode as U+FFFD. */
uint32_t ks_utf8_next(const char **s, const char *end);

/* ── Math (no libm on the Windows build) ────────────────────────────── */

float ks_sqrtf(float x);
float ks_floorf(float x);
float ks_ceilf(float x);
float ks_fabsf(float x);
float ks_roundf(float x);

/* Easing curves over t in [0, 1]. */
float ks_ease_out_cubic(float t);
float ks_ease_in_out_cubic(float t);

/* ── Checksums ──────────────────────────────────────────────────────── */

void ks_crc_init(void);                 /* call once before any thread starts */
uint32_t ks_crc32(const void *data, size_t size, uint32_t crc);

#endif
