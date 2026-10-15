/* SPDX-License-Identifier: 0BSD */

/*
 * XZ Embedded configuration for the Kestrel installer.
 *
 * Based on xz-embedded's userspace/xz_config.h. The difference is that it
 * does not include <stdlib.h> or <string.h>: the Windows build of the
 * installer links no C runtime, so allocation goes through the
 * installer's own allocator (ks_alloc/ks_free in ../common.h) and the four
 * mem* functions are the ones util.c provides on that build (and libc's
 * everywhere else).
 */

#ifndef XZ_CONFIG_H
#define XZ_CONFIG_H

/* The payload is compressed with the x86 BCJ filter and a CRC64 check. */
#define XZ_USE_CRC64
#define XZ_DEC_X86
/* Only multi-call mode with a dynamically allocated dictionary is used. */
#define XZ_DEC_DYNALLOC

#if defined(_MSC_VER) && _MSC_VER < 1900 && !defined(inline)
#	define inline __inline
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "xz.h"

void *ks_alloc(size_t size);
void ks_free(void *ptr);
void *memcpy(void *dst, const void *src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);

#define kmalloc(size, flags) ks_alloc(size)
#define kfree(ptr) ks_free(ptr)
#define vmalloc(size) ks_alloc(size)
#define vfree(ptr) ks_free(ptr)

#define memeq(a, b, size) (memcmp(a, b, size) == 0)
#define memzero(buf, size) memset(buf, 0, size)

#ifndef min
#	define min(x, y) ((x) < (y) ? (x) : (y))
#endif
#define min_t(type, x, y) min(x, y)

#ifndef fallthrough
#	if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 202311
#		define fallthrough [[fallthrough]]
#	elif (defined(__GNUC__) && __GNUC__ >= 7) \
			|| (defined(__clang_major__) && __clang_major__ >= 10)
#		define fallthrough __attribute__((__fallthrough__))
#	else
#		define fallthrough do {} while (0)
#	endif
#endif

#ifndef __always_inline
#	ifdef __GNUC__
#		define __always_inline \
			inline __attribute__((__always_inline__))
#	else
#		define __always_inline inline
#	endif
#endif

#ifndef get_unaligned_le32
static inline uint32_t get_unaligned_le32(const uint8_t *buf)
{
	return (uint32_t)buf[0]
			| ((uint32_t)buf[1] << 8)
			| ((uint32_t)buf[2] << 16)
			| ((uint32_t)buf[3] << 24);
}
#endif

#ifndef get_unaligned_be32
static inline uint32_t get_unaligned_be32(const uint8_t *buf)
{
	return (uint32_t)((uint32_t)buf[0] << 24)
			| ((uint32_t)buf[1] << 16)
			| ((uint32_t)buf[2] << 8)
			| (uint32_t)buf[3];
}
#endif

#ifndef put_unaligned_le32
static inline void put_unaligned_le32(uint32_t val, uint8_t *buf)
{
	buf[0] = (uint8_t)val;
	buf[1] = (uint8_t)(val >> 8);
	buf[2] = (uint8_t)(val >> 16);
	buf[3] = (uint8_t)(val >> 24);
}
#endif

#ifndef put_unaligned_be32
static inline void put_unaligned_be32(uint32_t val, uint8_t *buf)
{
	buf[0] = (uint8_t)(val >> 24);
	buf[1] = (uint8_t)(val >> 16);
	buf[2] = (uint8_t)(val >> 8);
	buf[3] = (uint8_t)val;
}
#endif

#ifndef get_le32
#	define get_le32 get_unaligned_le32
#endif
#ifndef get_be32
#	define get_be32 get_unaligned_be32
#endif

#endif
