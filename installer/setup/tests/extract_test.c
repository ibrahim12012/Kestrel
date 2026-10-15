/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * extract_test.c - runs the installer's payload reader and extractor on
 * Linux, against a setup file built by build_installer.py, so the
 * packer/unpacker pair can be tested without Windows.
 *
 *   extract_test SETUP.exe OUT_DIR [threads]
 *
 * Prints the metadata, extracts everything to OUT_DIR, and exits 0 on
 * success. test_payload.py compares OUT_DIR with the source tree.
 */

#include "../payload.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    int fd;
} Src;

static bool read_at(void *ctx, uint64_t off, void *buf, size_t len)
{
    Src *s = ctx;
    size_t done = 0;
    while (done < len) {
        ssize_t n = pread(s->fd, (char *)buf + done, len - done, (off_t)(off + done));
        if (n <= 0)
            return false;
        done += (size_t)n;
    }
    return true;
}

typedef struct {
    char root[4096];
} Dst;

static void join(const Dst *d, const char *rel, char *out, size_t cap)
{
    snprintf(out, cap, "%s/%s", d->root, rel);
}

static bool make_dir(void *ctx, const char *rel)
{
    char p[8192];
    join(ctx, rel, p, sizeof p);
    return mkdir(p, 0755) == 0 || errno == EEXIST;
}

static void *open_file(void *ctx, const char *rel, uint64_t size)
{
    (void)size;
    char p[8192];
    join(ctx, rel, p, sizeof p);
    int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0)
        return NULL;
    return (void *)(intptr_t)(fd + 1);
}

static bool write_file(void *ctx, void *h, const void *data, size_t len)
{
    (void)ctx;
    int fd = (int)(intptr_t)h - 1;
    size_t done = 0;
    while (done < len) {
        ssize_t n = write(fd, (const char *)data + done, len - done);
        if (n <= 0)
            return false;
        done += (size_t)n;
    }
    return true;
}

static bool close_file(void *ctx, void *h, bool ok)
{
    (void)ctx;
    (void)ok;
    return close((int)(intptr_t)h - 1) == 0;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s SETUP.exe OUT_DIR [threads]\n", argv[0]);
        return 2;
    }
    ks_crc_init();
    Src src = { open(argv[1], O_RDONLY) };
    if (src.fd < 0) {
        perror(argv[1]);
        return 1;
    }
    struct stat st;
    fstat(src.fd, &st);
    PayloadSource ps = { &src, read_at, (uint64_t)st.st_size };
    Payload p;
    char err[256];
    if (!payload_open(&ps, &p, err, sizeof err)) {
        printf("open failed: %s\n", err);
        return 3;
    }
    printf("base=%llu files=%d dirs=%d chunks=%d total=%llu\n", (unsigned long long)p.base, p.file_count,
           p.dir_count, p.chunk_count, (unsigned long long)p.total_unpacked);
    for (int i = 0; i < p.meta_count; i++) {
        if (strcmp(p.meta[i].key, "license") == 0)
            printf("meta license=<%zu bytes>\n", strlen(p.meta[i].value));
        else
            printf("meta %s=%s\n", p.meta[i].key, p.meta[i].value);
    }

    Dst dst;
    snprintf(dst.root, sizeof dst.root, "%s", argv[2]);
    mkdir(dst.root, 0755);
    PayloadSink sink = { &dst, make_dir, open_file, write_file, close_file };
    ExtractState es;
    memset(&es, 0, sizeof es);
    int threads = argc > 3 ? atoi(argv[3]) : 4;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    bool ok = payload_extract(&p, &ps, &sink, threads, &es);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double secs = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;
    printf("extract %s in %.2f s with %d threads, %lld bytes\n", ok ? "OK" : "FAILED", secs, threads,
           (long long)es.bytes_done);
    if (!ok)
        printf("error: %s\n", es.error);
    payload_free(&p);
    close(src.fd);
    return ok ? 0 : 4;
}
