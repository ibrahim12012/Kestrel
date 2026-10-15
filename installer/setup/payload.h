/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * payload.h - the app, packed onto the end of the setup program.
 *
 * Layout (all integers little-endian), starting right where the PE image
 * ends, i.e. after the last section's raw data. Code signing appends its
 * certificate after the payload, so the end of the file is not a reliable
 * anchor and the start of the payload is found from the PE headers:
 *
 *   header   64 bytes, see PAYLOAD_HEADER_SIZE below
 *   meta     key/value records: u16 key length, key, u32 value length, value
 *   index    u32 n, n x (u16 len, dir path)
 *            u32 n, n x (u16 len, file path, u64 size, u32 crc32, u32 chunk)
 *            u32 n, n x (u64 offset, u64 packed, u64 unpacked,
 *                        u32 first file, u32 file count)
 *   data     one .xz stream per chunk
 *
 * Paths are UTF-8, relative, '/'-separated. A chunk's stream decompresses
 * to its files' contents back to back, in index order. Chunks are
 * independent, which is what lets them be decompressed in parallel.
 *
 * installer/build_installer.py writes this; payload.c reads it.
 */

#ifndef KS_PAYLOAD_H
#define KS_PAYLOAD_H

#include "common.h"

#define PAYLOAD_MAGIC "KSTPAYLD"
#define PAYLOAD_VERSION 1
#define PAYLOAD_HEADER_SIZE 64

typedef struct {
    char *path;
    uint64_t size;
    uint32_t crc32;
    uint32_t chunk;
} PayloadFile;

typedef struct {
    uint64_t offset;          /* absolute, in the setup file */
    uint64_t packed_size;
    uint64_t unpacked_size;
    uint32_t first_file;
    uint32_t file_count;
} PayloadChunk;

typedef struct {
    char *key;
    char *value;
} PayloadMeta;

typedef struct {
    uint64_t base;            /* where the payload starts in the file */
    uint64_t total_unpacked;
    PayloadMeta *meta;
    int meta_count;
    char **dirs;
    int dir_count;
    PayloadFile *files;
    int file_count;
    PayloadChunk *chunks;
    int chunk_count;
} Payload;

/* Positional reads from the setup file. Must be safe to call from several
   threads at once. */
typedef struct {
    void *ctx;
    bool (*read_at)(void *ctx, uint64_t offset, void *buf, size_t len);
    uint64_t size;
} PayloadSource;

/* Where extracted files go. Handles are whatever open_file returns. */
typedef struct {
    void *ctx;
    bool (*make_dir)(void *ctx, const char *rel_path);
    void *(*open_file)(void *ctx, const char *rel_path, uint64_t size);
    bool (*write_file)(void *ctx, void *handle, const void *data, size_t len);
    bool (*close_file)(void *ctx, void *handle, bool ok);
} PayloadSink;

typedef struct {
    volatile int64_t bytes_done;     /* uncompressed bytes written so far */
    volatile int32_t cancel;         /* set to 1 to stop */
    volatile int32_t failed;         /* set once, by the first error */
    volatile int32_t current_file;   /* for display */
    char error[512];                 /* valid once failed != 0 */
} ExtractState;

/*
 * Finds and parses the payload. Returns false with a message in err when
 * there is none (the uninstaller copy, for one) or it is damaged.
 */
bool payload_open(const PayloadSource *src, Payload *out, char *err, size_t err_cap);
void payload_free(Payload *p);

/* NULL when absent. */
const char *payload_meta(const Payload *p, const char *key);

/* Directories first, then all chunks on up to `threads` threads. */
bool payload_extract(const Payload *p, const PayloadSource *src, const PayloadSink *sink, int threads,
                     ExtractState *st);

/* The PE image's end in a buffer holding the start of the file, or 0. */
uint64_t pe_image_end(const uint8_t *head, size_t head_len);

#endif
