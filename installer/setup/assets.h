/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * assets.h - the images compiled into the setup program.
 */

#ifndef KS_ASSETS_H
#define KS_ASSETS_H

#include "gfx.h"

/* The app's mark, premultiplied BGRA, square. */
bool assets_load_logo(Image *out);

/* Decompress a complete .xz stream of known output size in one call. */
bool xz_decode_buffer(const void *in, size_t in_size, void *out, size_t out_size);

#endif
