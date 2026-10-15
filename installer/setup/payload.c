/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * payload.c - reading and extracting the payload. See payload.h.
 *
 * Everything read from the file is bounds-checked before use, and every
 * path is checked to stay inside the destination: a truncated download
 * fails with a message, and a tampered payload cannot write outside the
 * install folder.
 */

#include "payload.h"
#include "sys.h"
#include "xz/xz_config.h"

#define IN_BUF_SIZE (256 * 1024)
#define OUT_BUF_SIZE (1024 * 1024)
#define DICT_MAX (1u << 27)          /* 128 MB, far above what the packer uses */
#define MAX_ENTRIES 200000

static uint16_t rd16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t rd64(const uint8_t *p)
{
    return (uint64_t)rd32(p) | ((uint64_t)rd32(p + 4) << 32);
}

uint64_t pe_image_end(const uint8_t *head, size_t len)
{
    if (len < 0x40 || head[0] != 'M' || head[1] != 'Z')
        return 0;
    uint32_t pe = rd32(head + 0x3C);
    if ((uint64_t)pe + 24 > len || memcmp(head + pe, "PE\0\0", 4) != 0)
        return 0;
    const uint8_t *coff = head + pe + 4;
    uint16_t sections = rd16(coff + 2);
    uint16_t opt_size = rd16(coff + 16);
    uint64_t table = (uint64_t)(coff - head) + 20 + opt_size;
    uint64_t end = 0;
    for (uint16_t i = 0; i < sections; i++) {
        uint64_t s = table + (uint64_t)i * 40;
        if (s + 40 > len)
            return 0;
        uint64_t raw_size = rd32(head + s + 16), raw_ptr = rd32(head + s + 20);
        if (raw_size && raw_ptr + raw_size > end)
            end = raw_ptr + raw_size;
    }
    return end;
}

/* A path from the index is acceptable only if it is relative, uses '/'
   separators, and has no empty, "." or ".." components, no drive or
   stream colon, and no control characters. */
static bool safe_rel_path(const char *s)
{
    if (!s[0] || s[0] == '/')
        return false;
    const char *comp = s;
    for (const char *p = s;; p++) {
        char c = *p;
        if (c == '/' || c == 0) {
            size_t n = (size_t)(p - comp);
            if (n == 0 || (n == 1 && comp[0] == '.') || (n == 2 && comp[0] == '.' && comp[1] == '.'))
                return false;
            if (c == 0)
                return true;
            comp = p + 1;
            continue;
        }
        if ((unsigned char)c < 0x20 || c == '\\' || c == ':' || c == '*' || c == '?' || c == '"' || c == '<'
            || c == '>' || c == '|')
            return false;
    }
}

typedef struct {
    const uint8_t *p;
    size_t left;
    bool ok;
} Reader;

static const uint8_t *take(Reader *r, size_t n)
{
    if (!r->ok || r->left < n) {
        r->ok = false;
        return NULL;
    }
    const uint8_t *at = r->p;
    r->p += n;
    r->left -= n;
    return at;
}

static uint16_t take16(Reader *r)
{
    const uint8_t *p = take(r, 2);
    return p ? rd16(p) : 0;
}

static uint32_t take32(Reader *r)
{
    const uint8_t *p = take(r, 4);
    return p ? rd32(p) : 0;
}

static uint64_t take64(Reader *r)
{
    const uint8_t *p = take(r, 8);
    return p ? rd64(p) : 0;
}

static char *take_str(Reader *r, size_t n)
{
    const uint8_t *p = take(r, n);
    if (!p)
        return NULL;
    for (size_t i = 0; i < n; i++)
        if (!p[i]) {
            r->ok = false;       /* embedded NUL */
            return NULL;
        }
    return ks_strndup((const char *)p, n);
}

static uint8_t *read_block(const PayloadSource *src, uint64_t offset, uint32_t size, uint32_t crc)
{
    if (offset + size > src->size || size > 256u * 1024 * 1024)
        return NULL;
    uint8_t *buf = (uint8_t *)ks_alloc(size ? size : 1);
    if (!buf)
        return NULL;
    if (!src->read_at(src->ctx, offset, buf, size) || ks_crc32(buf, size, 0) != crc) {
        ks_free(buf);
        return NULL;
    }
    return buf;
}

bool payload_open(const PayloadSource *src, Payload *out, char *err, size_t err_cap)
{
    memset(out, 0, sizeof(*out));
    uint8_t head[2048];      /* the PE headers; kept under a page of stack */
    size_t head_len = src->size < sizeof head ? (size_t)src->size : sizeof head;
    if (!src->read_at(src->ctx, 0, head, head_len)) {
        ks_strlcpy(err, "Couldn't read the setup file.", err_cap);
        return false;
    }
    uint64_t base = pe_image_end(head, head_len);
    if (base == 0 || base + PAYLOAD_HEADER_SIZE > src->size) {
        ks_strlcpy(err, "This setup file has no program in it.", err_cap);
        return false;
    }
    uint8_t h[PAYLOAD_HEADER_SIZE];
    if (!src->read_at(src->ctx, base, h, sizeof h) || memcmp(h, PAYLOAD_MAGIC, 8) != 0) {
        ks_strlcpy(err, "This setup file has no program in it.", err_cap);
        return false;
    }
    if (rd32(h + 60) != ks_crc32(h, 60, 0) || rd32(h + 8) != PAYLOAD_VERSION || rd32(h + 12) != PAYLOAD_HEADER_SIZE) {
        ks_strlcpy(err, "This setup file is damaged or incomplete. Download it again.", err_cap);
        return false;
    }
    out->base = base;
    out->total_unpacked = rd64(h + 48);

    uint8_t *meta = read_block(src, base + rd64(h + 16), rd32(h + 24), rd32(h + 28));
    uint8_t *index = read_block(src, base + rd64(h + 32), rd32(h + 40), rd32(h + 44));
    bool ok = meta && index;

    if (ok) {
        Reader r = { meta, rd32(h + 24), true };
        int cap = 0;
        while (r.ok && r.left > 0) {
            uint16_t kl = take16(&r);
            char *k = take_str(&r, kl);
            uint32_t vl = take32(&r);
            char *v = take_str(&r, vl);
            if (!k || !v) {
                ks_free(k);
                ks_free(v);
                ok = false;
                break;
            }
            if (out->meta_count == cap) {
                cap = cap ? cap * 2 : 32;
                PayloadMeta *m = (PayloadMeta *)ks_realloc(out->meta, sizeof(PayloadMeta) * (size_t)cap);
                if (!m) {
                    ks_free(k);
                    ks_free(v);
                    ok = false;
                    break;
                }
                out->meta = m;
            }
            out->meta[out->meta_count].key = k;
            out->meta[out->meta_count].value = v;
            out->meta_count++;
        }
        ok = ok && r.ok;
    }

    if (ok) {
        Reader r = { index, rd32(h + 40), true };
        uint32_t nd = take32(&r);
        ok = r.ok && nd < MAX_ENTRIES;
        if (ok)
            out->dirs = (char **)ks_alloc(sizeof(char *) * (nd + 1));
        for (uint32_t i = 0; ok && i < nd; i++) {
            char *d = take_str(&r, take16(&r));
            if (!d || !safe_rel_path(d)) {
                ks_free(d);
                ok = false;
                break;
            }
            out->dirs[out->dir_count++] = d;
        }
        uint32_t nf = ok ? take32(&r) : 0;
        ok = ok && r.ok && nf < MAX_ENTRIES;
        if (ok)
            out->files = (PayloadFile *)ks_alloc(sizeof(PayloadFile) * (nf + 1));
        uint64_t total = 0;
        for (uint32_t i = 0; ok && i < nf; i++) {
            PayloadFile *f = &out->files[i];
            f->path = take_str(&r, take16(&r));
            f->size = take64(&r);
            f->crc32 = take32(&r);
            f->chunk = take32(&r);
            out->file_count = (int)i + 1;
            if (!r.ok || !f->path || !safe_rel_path(f->path)) {
                ok = false;
                break;
            }
            total += f->size;
        }
        uint32_t nc = ok ? take32(&r) : 0;
        ok = ok && r.ok && nc < MAX_ENTRIES;
        if (ok)
            out->chunks = (PayloadChunk *)ks_alloc(sizeof(PayloadChunk) * (nc + 1));
        uint32_t expected_first = 0;
        for (uint32_t i = 0; ok && i < nc; i++) {
            PayloadChunk *c = &out->chunks[i];
            c->offset = base + take64(&r);
            c->packed_size = take64(&r);
            c->unpacked_size = take64(&r);
            c->first_file = take32(&r);
            c->file_count = take32(&r);
            out->chunk_count = (int)i + 1;
            /* Chunks must tile the file list in order, and lie within the file. */
            uint64_t sum = 0;
            if (!r.ok || c->first_file != expected_first || (uint64_t)c->first_file + c->file_count > nf
                || c->offset + c->packed_size > src->size) {
                ok = false;
                break;
            }
            for (uint32_t k = 0; k < c->file_count; k++) {
                if (out->files[c->first_file + k].chunk != i)
                    ok = false;
                sum += out->files[c->first_file + k].size;
            }
            if (sum != c->unpacked_size)
                ok = false;
            expected_first += c->file_count;
        }
        ok = ok && expected_first == nf && total == out->total_unpacked && r.left == 0;
    }

    ks_free(meta);
    ks_free(index);
    if (!ok) {
        payload_free(out);
        ks_strlcpy(err, "This setup file is damaged or incomplete. Download it again.", err_cap);
        return false;
    }
    return true;
}

void payload_free(Payload *p)
{
    for (int i = 0; i < p->meta_count; i++) {
        ks_free(p->meta[i].key);
        ks_free(p->meta[i].value);
    }
    for (int i = 0; i < p->dir_count; i++)
        ks_free(p->dirs[i]);
    for (int i = 0; i < p->file_count; i++)
        ks_free(p->files[i].path);
    ks_free(p->meta);
    ks_free(p->dirs);
    ks_free(p->files);
    ks_free(p->chunks);
    memset(p, 0, sizeof(*p));
}

const char *payload_meta(const Payload *p, const char *key)
{
    for (int i = 0; i < p->meta_count; i++)
        if (ks_strcmp(p->meta[i].key, key) == 0)
            return p->meta[i].value;
    return NULL;
}

/* ── Extraction ─────────────────────────────────────────────────────── */

typedef struct {
    const Payload *p;
    const PayloadSource *src;
    const PayloadSink *sink;
    ExtractState *st;
    volatile int32_t next_chunk;
} Job;

static bool fail(ExtractState *st, const char *fmt, ...)
{
    if (ks_atomic_cas32(&st->failed, 0, 1) == 0) {
        va_list ap;
        va_start(ap, fmt);
        ks_vformat(st->error, sizeof st->error, fmt, ap);
        va_end(ap);
    }
    st->cancel = 1;
    return false;
}

typedef struct {
    Job *job;
    const PayloadChunk *chunk;
    uint32_t file;            /* index of the file being written */
    uint32_t end;             /* one past the chunk's last file */
    void *handle;
    uint64_t left;            /* bytes still to write to it */
    uint32_t crc;
} Cursor;

static bool close_current(Cursor *c)
{
    const PayloadFile *f = &c->job->p->files[c->file];
    bool crc_ok = c->crc == f->crc32;
    bool closed = c->job->sink->close_file(c->job->sink->ctx, c->handle, crc_ok);
    c->handle = NULL;
    if (!crc_ok)
        return fail(c->job->st, "This setup file is damaged (%s does not match). Download it again.", f->path);
    if (!closed)
        return fail(c->job->st, "Couldn't finish writing %s.", f->path);
    c->file++;
    return true;
}

/* Open the next file with content, creating any empty ones on the way. */
static bool open_next(Cursor *c)
{
    const PayloadSink *sink = c->job->sink;
    while (c->file < c->end) {
        const PayloadFile *f = &c->job->p->files[c->file];
        c->job->st->current_file = (int32_t)c->file;
        c->handle = sink->open_file(sink->ctx, f->path, f->size);
        if (!c->handle)
            return fail(c->job->st, "Couldn't create %s.", f->path);
        c->left = f->size;
        c->crc = 0;
        if (f->size > 0)
            return true;
        if (!close_current(c))
            return false;
    }
    return true;
}

static bool consume(Cursor *c, const uint8_t *data, size_t n)
{
    const PayloadSink *sink = c->job->sink;
    while (n > 0) {
        if (!c->handle)
            return fail(c->job->st, "This setup file is damaged (more data than expected). Download it again.");
        size_t k = c->left < n ? (size_t)c->left : n;
        if (!sink->write_file(sink->ctx, c->handle, data, k)) {
            const PayloadFile *f = &c->job->p->files[c->file];
            sink->close_file(sink->ctx, c->handle, false);
            c->handle = NULL;
            return fail(c->job->st, "Couldn't write %s.", f->path);
        }
        c->crc = ks_crc32(data, k, c->crc);
        ks_atomic_add64(&c->job->st->bytes_done, (int64_t)k);
        c->left -= k;
        data += k;
        n -= k;
        if (c->left == 0) {
            if (!close_current(c) || !open_next(c))
                return false;
        }
    }
    return true;
}

static bool extract_chunk(Job *job, int index, uint8_t *in, uint8_t *out)
{
    const PayloadChunk *chunk = &job->p->chunks[index];
    Cursor c;
    memset(&c, 0, sizeof c);
    c.job = job;
    c.chunk = chunk;
    c.file = chunk->first_file;
    c.end = chunk->first_file + chunk->file_count;
    if (!open_next(&c))
        return false;

    struct xz_dec *dec = xz_dec_init(XZ_DYNALLOC, DICT_MAX);
    if (!dec)
        return fail(job->st, "Not enough memory to unpack the program.");

    struct xz_buf b;
    b.in = in;
    b.in_pos = 0;
    b.in_size = 0;
    b.out = out;
    b.out_pos = 0;
    b.out_size = OUT_BUF_SIZE;
    uint64_t in_offset = chunk->offset, in_left = chunk->packed_size;
    bool ok = true;

    for (;;) {
        if (job->st->cancel) {
            ok = false;
            break;
        }
        if (b.in_pos == b.in_size && in_left > 0) {
            size_t n = in_left < IN_BUF_SIZE ? (size_t)in_left : IN_BUF_SIZE;
            if (!job->src->read_at(job->src->ctx, in_offset, in, n)) {
                ok = fail(job->st, "Couldn't read the setup file.");
                break;
            }
            in_offset += n;
            in_left -= n;
            b.in_pos = 0;
            b.in_size = n;
        }
        enum xz_ret ret = xz_dec_run(dec, &b);
        if (b.out_pos > 0) {
            if (!consume(&c, out, b.out_pos)) {
                ok = false;
                break;
            }
            b.out_pos = 0;
        }
        if (ret == XZ_STREAM_END)
            break;
        if (ret == XZ_MEM_ERROR || ret == XZ_MEMLIMIT_ERROR) {
            ok = fail(job->st, "Not enough memory to unpack the program.");
            break;
        }
        if (ret != XZ_OK) {
            ok = fail(job->st, "This setup file is damaged or incomplete. Download it again.");
            break;
        }
    }
    xz_dec_end(dec);

    if (ok && (c.handle || c.file != c.end || in_left != 0 || b.in_pos != b.in_size))
        ok = fail(job->st, "This setup file is damaged or incomplete. Download it again.");
    if (c.handle)
        job->sink->close_file(job->sink->ctx, c.handle, false);
    return ok;
}

static KS_THREAD_FN(extract_worker)
{
    Job *job = (Job *)arg;
    uint8_t *in = (uint8_t *)ks_alloc(IN_BUF_SIZE);
    uint8_t *out = (uint8_t *)ks_alloc(OUT_BUF_SIZE);
    if (!in || !out) {
        fail(job->st, "Not enough memory to unpack the program.");
    } else {
        for (;;) {
            int index = ks_atomic_inc32(&job->next_chunk) - 1;
            if (index >= job->p->chunk_count || job->st->cancel)
                break;
            if (!extract_chunk(job, index, in, out))
                break;
        }
    }
    ks_free(in);
    ks_free(out);
    KS_THREAD_RETURN;
}

bool payload_extract(const Payload *p, const PayloadSource *src, const PayloadSink *sink, int threads,
                     ExtractState *st)
{
    for (int i = 0; i < p->dir_count; i++) {
        if (!sink->make_dir(sink->ctx, p->dirs[i]))
            return fail(st, "Couldn't create the folder %s.", p->dirs[i]);
    }

    Job job;
    job.p = p;
    job.src = src;
    job.sink = sink;
    job.st = st;
    job.next_chunk = 0;

    if (threads > p->chunk_count)
        threads = p->chunk_count;
    if (threads < 1)
        threads = 1;
    if (threads > 16)
        threads = 16;

    KsThread handles[16];
    int started = 0;
    for (int i = 1; i < threads; i++) {
        if (ks_thread_start(&handles[started], extract_worker, &job))
            started++;
    }
    extract_worker(&job);          /* this thread works too */
    for (int i = 0; i < started; i++)
        ks_thread_join(handles[i]);

    if (st->failed)
        return false;
    if (st->cancel)
        return false;
    return true;
}
