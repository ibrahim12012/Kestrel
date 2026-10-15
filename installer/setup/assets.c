/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * assets.c - the images compiled into the setup program.
 */

#include "assets.h"
#include "logo_data.h"
#include "xz/xz_config.h"

bool xz_decode_buffer(const void *in, size_t in_size, void *out, size_t out_size)
{
    struct xz_dec *dec = xz_dec_init(XZ_DYNALLOC, 1u << 24);
    if (!dec)
        return false;
    struct xz_buf b;
    b.in = (const uint8_t *)in;
    b.in_pos = 0;
    b.in_size = in_size;
    b.out = (uint8_t *)out;
    b.out_pos = 0;
    b.out_size = out_size;
    enum xz_ret ret;
    for (;;) {
        size_t in_before = b.in_pos, out_before = b.out_pos;
        ret = xz_dec_run(dec, &b);
        if (ret != XZ_OK || (b.in_pos == in_before && b.out_pos == out_before))
            break;
    }
    xz_dec_end(dec);
    return ret == XZ_STREAM_END && b.out_pos == out_size;
}

bool assets_load_logo(Image *out)
{
    if (!image_alloc(out, KS_LOGO_SIZE, KS_LOGO_SIZE))
        return false;
    if (!xz_decode_buffer(k_logo_xz, sizeof(k_logo_xz), out->px, (size_t)KS_LOGO_SIZE * KS_LOGO_SIZE * 4)) {
        image_free(out);
        return false;
    }
    return true;
}
