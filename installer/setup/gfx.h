/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * gfx.h - a small anti-aliased software rasterizer.
 *
 * Everything on screen except text is drawn here, into a 32-bit BGRA
 * buffer: rounded rectangles, circles, strokes and images, all with
 * analytic coverage (signed distance to the shape's edge), so edges are
 * smooth at any DPI without supersampling. The same code draws the real
 * window on Windows and the PNG previews on Linux, which is what lets the
 * screens be checked pixel for pixel without a Windows machine.
 */

#ifndef KS_GFX_H
#define KS_GFX_H

#include "common.h"

/* 0xAARRGGBB, straight (not premultiplied) alpha. */
typedef uint32_t Color;

#define COLA(r, g, b, a) ((Color)(((uint32_t)(a) << 24) | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b)))
#define COL(r, g, b) COLA(r, g, b, 255)
#define HEXC(h) ((Color)(0xFF000000u | (uint32_t)(h)))

static inline uint32_t color_a(Color c) { return c >> 24; }
static inline uint32_t color_r(Color c) { return (c >> 16) & 0xFF; }
static inline uint32_t color_g(Color c) { return (c >> 8) & 0xFF; }
static inline uint32_t color_b(Color c) { return c & 0xFF; }

Color color_with_alpha(Color c, float alpha);   /* multiplies existing alpha */
Color color_lerp(Color a, Color b, float t);

typedef struct {
    uint32_t *px;        /* top-down rows, BGRA in memory */
    int w, h, stride;    /* stride in pixels */
    int clip_x0, clip_y0, clip_x1, clip_y1;
    void *native;        /* HDC on Windows, for GDI text; unused elsewhere */
} Canvas;

/* Premultiplied BGRA image. */
typedef struct {
    uint32_t *px;
    int w, h;
} Image;

void gfx_canvas_init(Canvas *cv, uint32_t *px, int w, int h, int stride);
void gfx_set_clip(Canvas *cv, float x0, float y0, float x1, float y1);
void gfx_reset_clip(Canvas *cv);

void gfx_clear(Canvas *cv, Color c);
void gfx_fill_rect(Canvas *cv, float x, float y, float w, float h, Color c);
void gfx_fill_rrect(Canvas *cv, float x, float y, float w, float h, float r, Color c);
void gfx_stroke_rrect(Canvas *cv, float x, float y, float w, float h, float r, float thickness, Color c);
void gfx_fill_circle(Canvas *cv, float cx, float cy, float r, Color c);
void gfx_stroke_circle(Canvas *cv, float cx, float cy, float r, float thickness, Color c);
void gfx_line(Canvas *cv, float x0, float y0, float x1, float y1, float width, Color c);

/*
 * A polyline with round joins and caps, drawn up to `fraction` of its
 * total length. Used for the check mark, which draws itself in.
 */
void gfx_polyline(Canvas *cv, const float *xy, int points, float width, Color c, float fraction);

/* A soft radial light, added over what is already there. */
void gfx_radial_glow(Canvas *cv, float cx, float cy, float rx, float ry, Color c);

/* Vertical gradient inside a rounded rectangle. */
void gfx_fill_rrect_vgradient(Canvas *cv, float x, float y, float w, float h, float r, Color top, Color bottom);

/* Images */
bool image_alloc(Image *img, int w, int h);
void image_free(Image *img);
/* High-quality resize: area averaging when shrinking, bilinear when growing. */
bool image_resize(const Image *src, Image *dst, int w, int h);
void gfx_draw_image(Canvas *cv, const Image *img, int x, int y, float alpha);

/* dst = dst * (1 - t) + src * t, over the whole canvas. Sizes must match. */
void gfx_crossfade(Canvas *dst, const Canvas *src, float t);
void gfx_copy(Canvas *dst, const Canvas *src);

#endif
