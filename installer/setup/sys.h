/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * sys.h - threads and atomics, over Win32 or POSIX.
 */

#ifndef KS_SYS_H
#define KS_SYS_H

#include "common.h"

#ifdef KS_WINDOWS
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>

typedef HANDLE KsThread;
typedef DWORD(WINAPI *KsThreadProcRaw)(LPVOID);

static inline bool ks_thread_start(KsThread *t, DWORD(WINAPI *fn)(LPVOID), void *arg)
{
    *t = CreateThread(NULL, 256 * 1024, fn, arg, 0, NULL);
    return *t != NULL;
}

static inline void ks_thread_join(KsThread t)
{
    WaitForSingleObject(t, INFINITE);
    CloseHandle(t);
}

#  define KS_THREAD_FN(name) DWORD WINAPI name(LPVOID arg)
#  define KS_THREAD_RETURN return 0

static inline int64_t ks_atomic_add64(volatile int64_t *p, int64_t v)
{
    return InterlockedExchangeAdd64((volatile LONG64 *)p, v) + v;
}

static inline int32_t ks_atomic_inc32(volatile int32_t *p)
{
    return InterlockedIncrement((volatile LONG *)p);
}

static inline int32_t ks_atomic_cas32(volatile int32_t *p, int32_t expected, int32_t desired)
{
    return InterlockedCompareExchange((volatile LONG *)p, desired, expected);
}

static inline int32_t ks_atomic_load32(volatile int32_t *p)
{
    return InterlockedCompareExchange((volatile LONG *)p, 0, 0);
}

static inline int64_t ks_atomic_load64(volatile int64_t *p)
{
    return InterlockedCompareExchange64((volatile LONG64 *)p, 0, 0);
}

static inline int ks_cpu_count(void)
{
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return (int)si.dwNumberOfProcessors;
}

#else /* POSIX */
#  include <pthread.h>
#  include <unistd.h>

typedef pthread_t KsThread;

static inline bool ks_thread_start(KsThread *t, void *(*fn)(void *), void *arg)
{
    return pthread_create(t, NULL, fn, arg) == 0;
}

static inline void ks_thread_join(KsThread t)
{
    pthread_join(t, NULL);
}

#  define KS_THREAD_FN(name) void *name(void *arg)
#  define KS_THREAD_RETURN return NULL

static inline int64_t ks_atomic_add64(volatile int64_t *p, int64_t v)
{
    return __atomic_add_fetch(p, v, __ATOMIC_SEQ_CST);
}

static inline int32_t ks_atomic_inc32(volatile int32_t *p)
{
    return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST);
}

static inline int32_t ks_atomic_cas32(volatile int32_t *p, int32_t expected, int32_t desired)
{
    __atomic_compare_exchange_n(p, &expected, desired, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return expected;
}

static inline int32_t ks_atomic_load32(volatile int32_t *p)
{
    return __atomic_load_n(p, __ATOMIC_SEQ_CST);
}

static inline int64_t ks_atomic_load64(volatile int64_t *p)
{
    return __atomic_load_n(p, __ATOMIC_SEQ_CST);
}

static inline int ks_cpu_count(void)
{
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (int)n : 1;
}

#endif

#endif
