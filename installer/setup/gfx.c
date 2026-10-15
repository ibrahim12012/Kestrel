/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * gfx.c - the software rasterizer. See gfx.h.
 *
 * Coverage model: every shape has a signed distance function d(p) that is
 * negative inside and positive outside, in pixels. A pixel whose centre is
 * at distance d from the edge is covered by clamp(0.5 - d, 0, 1). For the
 * shapes used here that is a very close approximation of exact area
 * coverage, and it costs one distance evaluation per pixel.
 */

#include "gfx.h"

/* ── Colours ────────────────────────────────────────────────────────── */

Color color_with_alpha(Color c, float alpha)
{
    float a = (float)color_a(c) * alpha;
    a = KS_CLAMP(a, 0.0f, 255.0f);
    return (c & 0x00FFFFFFu) | ((uint32_t)(a + 0.5f) << 24);
}

static uint32_t lerp8(uint32_t a, uint32_t b, float t)
{
    float v = (float)a + ((float)b - (float)a) * t;
    return (uint32_t)KS_CLAMP(v + 0.5f, 0.0f, 255.0f);
}

Color color_lerp(Color a, Color b, float t)
{
    t = KS_CLAMP(t, 0.0f, 1.0f);
    return COLA(lerp8(color_r(a), color_r(b), t), lerp8(color_g(a), color_g(b), t),
                lerp8(color_b(a), color_b(b), t), lerp8(color_a(a), color_a(b), t));
}

/* ── Canvas ─────────────────────────────────────────────────────────── */

void gfx_canvas_init(Canvas *cv, uint32_t *px, int w, int h, int stride)
{
    cv->px = px;
    cv->w = w;
    cv->h = h;
    cv->stride = stride;
    cv->native = NULL;
    gfx_reset_clip(cv);
}

void gfx_reset_clip(Canvas *cv)
{
    cv->clip_x0 = 0;
    cv->clip_y0 = 0;
    cv->clip_x1 = cv->w;
    cv->clip_y1 = cv->h;
}

void gfx_set_clip(Canvas *cv, float x0, float y0, float x1, float y1)
{
    int ix0 = (int)ks_floorf(x0), iy0 = (int)ks_floorf(y0);
    int ix1 = (int)ks_ceilf(x1), iy1 = (int)ks_ceilf(y1);
    cv->clip_x0 = KS_CLAMP(ix0, 0, cv->w);
    cv->clip_y0 = KS_CLAMP(iy0, 0, cv->h);
    cv->clip_x1 = KS_CLAMP(ix1, cv->clip_x0, cv->w);
    cv->clip_y1 = KS_CLAMP(iy1, cv->clip_y0, cv->h);
}

/* Blend colour c over pixel *d with the given coverage (0..1). */
static inline void blend(uint32_t *d, Color c, float coverage)
{
    int a = (int)((float)color_a(c) * coverage * (256.0f / 255.0f) + 0.5f);
    if (a <= 0)
        return;
    if (a >= 256) {
        *d = c | 0xFF000000u;
        return;
    }
    uint32_t dp = *d;
    int dr = (int)((dp >> 16) & 0xFF), dg = (int)((dp >> 8) & 0xFF), db = (int)(dp & 0xFF);
    int sr = (int)color_r(c), sg = (int)color_g(c), sb = (int)color_b(c);
    dr += ((sr - dr) * a) >> 8;
    dg += ((sg - dg) * a) >> 8;
    db += ((sb - db) * a) >> 8;
    *d = 0xFF000000u | ((uint32_t)dr << 16) | ((uint32_t)dg << 8) | (uint32_t)db;
}

typedef struct {
    int x0, y0, x1, y1;
} Box;

/* Pixel bounds of [x0,x1) x [y0,y1), padded by one pixel for the
   anti-aliased fringe and intersected with the clip. */
static bool shape_box(const Canvas *cv, float x0, float y0, float x1, float y1, Box *b)
{
    b->x0 = KS_MAX((int)ks_floorf(x0) - 1, cv->clip_x0);
    b->y0 = KS_MAX((int)ks_floorf(y0) - 1, cv->clip_y0);
    b->x1 = KS_MIN((int)ks_ceilf(x1) + 1, cv->clip_x1);
    b->y1 = KS_MIN((int)ks_ceilf(y1) + 1, cv->clip_y1);
    return b->x0 < b->x1 && b->y0 < b->y1;
}

static inline float cov_from_distance(float d)
{
    float c = 0.5f - d;
    return c <= 0.0f ? 0.0f : (c >= 1.0f ? 1.0f : c);
}

void gfx_clear(Canvas *cv, Color c)
{
    uint32_t v = c | 0xFF000000u;
    for (int y = 0; y < cv->h; y++) {
        uint32_t *row = cv->px + (size_t)y * cv->stride;
        for (int x = 0; x < cv->w; x++)
            row[x] = v;
    }
}

static float span_overlap(float a0, float a1, float b0, float b1)
{
    float lo = a0 > b0 ? a0 : b0;
    float hi = a1 < b1 ? a1 : b1;
    return hi > lo ? hi - lo : 0.0f;
}

void gfx_fill_rect(Canvas *cv, float x, float y, float w, float h, Color c)
{
    Box b;
    if (w <= 0 || h <= 0 || !shape_box(cv, x, y, x + w, y + h, &b))
        return;
    for (int py = b.y0; py < b.y1; py++) {
        float cy = span_overlap((float)py, (float)py + 1.0f, y, y + h);
        if (cy <= 0.0f)
            continue;
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        for (int px = b.x0; px < b.x1; px++) {
            float cx = span_overlap((float)px, (float)px + 1.0f, x, x + w);
            if (cx > 0.0f)
                blend(&row[px], c, cx * cy);
        }
    }
}

static inline float sd_rrect(float px, float py, float cx, float cy, float hw, float hh, float r)
{
    float qx = ks_fabsf(px - cx) - (hw - r);
    float qy = ks_fabsf(py - cy) - (hh - r);
    float ox = qx > 0.0f ? qx : 0.0f;
    float oy = qy > 0.0f ? qy : 0.0f;
    float inside = qx > qy ? qx : qy;
    if (inside > 0.0f)
        inside = 0.0f;
    return ks_sqrtf(ox * ox + oy * oy) + inside - r;
}

void gfx_fill_rrect(Canvas *cv, float x, float y, float w, float h, float r, Color c)
{
    Box b;
    if (w <= 0 || h <= 0 || !shape_box(cv, x, y, x + w, y + h, &b))
        return;
    float hw = w * 0.5f, hh = h * 0.5f;
    r = KS_CLAMP(r, 0.0f, KS_MIN(hw, hh));
    float cx = x + hw, cy = y + hh;
    for (int py = b.y0; py < b.y1; py++) {
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        float fy = (float)py + 0.5f;
        for (int px = b.x0; px < b.x1; px++) {
            float d = sd_rrect((float)px + 0.5f, fy, cx, cy, hw, hh, r);
            float cov = cov_from_distance(d);
            if (cov > 0.0f)
                blend(&row[px], c, cov);
        }
    }
}

void gfx_fill_rrect_vgradient(Canvas *cv, float x, float y, float w, float h, float r, Color top, Color bottom)
{
    Box b;
    if (w <= 0 || h <= 0 || !shape_box(cv, x, y, x + w, y + h, &b))
        return;
    float hw = w * 0.5f, hh = h * 0.5f;
    r = KS_CLAMP(r, 0.0f, KS_MIN(hw, hh));
    float cx = x + hw, cy = y + hh;
    for (int py = b.y0; py < b.y1; py++) {
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        float fy = (float)py + 0.5f;
        Color c = color_lerp(top, bottom, (fy - y) / h);
        for (int px = b.x0; px < b.x1; px++) {
            float d = sd_rrect((float)px + 0.5f, fy, cx, cy, hw, hh, r);
            float cov = cov_from_distance(d);
            if (cov > 0.0f)
                blend(&row[px], c, cov);
        }
    }
}

void gfx_stroke_rrect(Canvas *cv, float x, float y, float w, float h, float r, float t, Color c)
{
    Box b;
    if (w <= 0 || h <= 0 || t <= 0 || !shape_box(cv, x, y, x + w, y + h, &b))
        return;
    float hw = w * 0.5f, hh = h * 0.5f;
    r = KS_CLAMP(r, 0.0f, KS_MIN(hw, hh));
    float cx = x + hw, cy = y + hh;
    for (int py = b.y0; py < b.y1; py++) {
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        float fy = (float)py + 0.5f;
        for (int px = b.x0; px < b.x1; px++) {
            float d = sd_rrect((float)px + 0.5f, fy, cx, cy, hw, hh, r);
            float cov = cov_from_distance(d) - cov_from_distance(d + t);
            if (cov > 0.0f)
                blend(&row[px], c, cov);
        }
    }
}

void gfx_fill_circle(Canvas *cv, float cx, float cy, float r, Color c)
{
    gfx_fill_rrect(cv, cx - r, cy - r, 2 * r, 2 * r, r, c);
}

void gfx_stroke_circle(Canvas *cv, float cx, float cy, float r, float t, Color c)
{
    gfx_stroke_rrect(cv, cx - r, cy - r, 2 * r, 2 * r, r, t, c);
}

static inline float dist_to_segment(float px, float py, float ax, float ay, float bx, float by)
{
    float vx = bx - ax, vy = by - ay;
    float wx = px - ax, wy = py - ay;
    float len2 = vx * vx + vy * vy;
    float t = len2 > 0.0f ? (wx * vx + wy * vy) / len2 : 0.0f;
    t = KS_CLAMP(t, 0.0f, 1.0f);
    float dx = wx - t * vx, dy = wy - t * vy;
    return ks_sqrtf(dx * dx + dy * dy);
}

void gfx_line(Canvas *cv, float x0, float y0, float x1, float y1, float width, Color c)
{
    float xy[4] = { x0, y0, x1, y1 };
    gfx_polyline(cv, xy, 2, width, c, 1.0f);
}

void gfx_polyline(Canvas *cv, const float *xy, int points, float width, Color c, float fraction)
{
    if (points < 2 || fraction <= 0.0f)
        return;
    fraction = KS_MIN(fraction, 1.0f);

    /* Truncate the path to `fraction` of its length. */
    float seg[32][4];
    int nseg = 0;
    float total = 0.0f;
    for (int i = 0; i + 1 < points; i++) {
        float dx = xy[2 * i + 2] - xy[2 * i], dy = xy[2 * i + 3] - xy[2 * i + 1];
        total += ks_sqrtf(dx * dx + dy * dy);
    }
    float remaining = total * fraction;
    for (int i = 0; i + 1 < points && nseg < 32 && remaining > 0.0f; i++) {
        float ax = xy[2 * i], ay = xy[2 * i + 1], bx = xy[2 * i + 2], by = xy[2 * i + 3];
        float len = ks_sqrtf((bx - ax) * (bx - ax) + (by - ay) * (by - ay));
        if (len > remaining && len > 0.0f) {
            float k = remaining / len;
            bx = ax + (bx - ax) * k;
            by = ay + (by - ay) * k;
            len = remaining;
        }
        seg[nseg][0] = ax;
        seg[nseg][1] = ay;
        seg[nseg][2] = bx;
        seg[nseg][3] = by;
        nseg++;
        remaining -= len;
    }

    float half = width * 0.5f;
    float minx = 1e9f, miny = 1e9f, maxx = -1e9f, maxy = -1e9f;
    for (int i = 0; i < nseg; i++) {
        minx = KS_MIN(minx, KS_MIN(seg[i][0], seg[i][2]));
        maxx = KS_MAX(maxx, KS_MAX(seg[i][0], seg[i][2]));
        miny = KS_MIN(miny, KS_MIN(seg[i][1], seg[i][3]));
        maxy = KS_MAX(maxy, KS_MAX(seg[i][1], seg[i][3]));
    }
    Box b;
    if (!shape_box(cv, minx - half, miny - half, maxx + half, maxy + half, &b))
        return;
    for (int py = b.y0; py < b.y1; py++) {
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        float fy = (float)py + 0.5f;
        for (int px = b.x0; px < b.x1; px++) {
            float fx = (float)px + 0.5f;
            float d = 1e9f;
            for (int i = 0; i < nseg; i++)
                d = KS_MIN(d, dist_to_segment(fx, fy, seg[i][0], seg[i][1], seg[i][2], seg[i][3]));
            float cov = cov_from_distance(d - half);
            if (cov > 0.0f)
                blend(&row[px], c, cov);
        }
    }
}

void gfx_radial_glow(Canvas *cv, float cx, float cy, float rx, float ry, Color c)
{
    Box b;
    if (rx <= 0 || ry <= 0 || !shape_box(cv, cx - rx, cy - ry, cx + rx, cy + ry, &b))
        return;
    for (int py = b.y0; py < b.y1; py++) {
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        float ny = ((float)py + 0.5f - cy) / ry;
        for (int px = b.x0; px < b.x1; px++) {
            float nx = ((float)px + 0.5f - cx) / rx;
            float e = 1.0f - (nx * nx + ny * ny);
            if (e > 0.0f)
                blend(&row[px], c, e * e * e);   /* smooth falloff to zero */
        }
    }
}

/* ── Images ─────────────────────────────────────────────────────────── */

bool image_alloc(Image *img, int w, int h)
{
    img->w = w;
    img->h = h;
    img->px = (uint32_t *)ks_alloc((size_t)w * (size_t)h * 4);
    return img->px != NULL;
}

void image_free(Image *img)
{
    ks_free(img->px);
    img->px = NULL;
    img->w = img->h = 0;
}

/*
 * One pass of a separable resample along a line of `n_src` samples with
 * `stride_src` spacing into `n_dst` samples. Channels are handled as four
 * floats. Shrinking uses exact area coverage (box filter), growing uses
 * linear interpolation.
 */
static void resample_line(const float *src, int n_src, int ss, float *dst, int n_dst, int ds)
{
    float scale = (float)n_src / (float)n_dst;
    for (int i = 0; i < n_dst; i++) {
        float acc[4] = { 0, 0, 0, 0 };
        if (scale >= 1.0f) {
            float s0 = (float)i * scale, s1 = s0 + scale;
            int j0 = (int)s0, j1 = (int)ks_ceilf(s1);
            float wsum = 0.0f;
            for (int j = j0; j < j1 && j < n_src; j++) {
                float w = span_overlap(s0, s1, (float)j, (float)j + 1.0f);
                const float *p = src + (size_t)j * ss;
                acc[0] += p[0] * w;
                acc[1] += p[1] * w;
                acc[2] += p[2] * w;
                acc[3] += p[3] * w;
                wsum += w;
            }
            if (wsum > 0.0f)
                for (int k = 0; k < 4; k++)
                    acc[k] /= wsum;
        } else {
            float s = ((float)i + 0.5f) * scale - 0.5f;
            int j = (int)ks_floorf(s);
            float f = s - (float)j;
            int ja = KS_CLAMP(j, 0, n_src - 1), jb = KS_CLAMP(j + 1, 0, n_src - 1);
            const float *pa = src + (size_t)ja * ss, *pb = src + (size_t)jb * ss;
            for (int k = 0; k < 4; k++)
                acc[k] = pa[k] + (pb[k] - pa[k]) * f;
        }
        float *o = dst + (size_t)i * ds;
        o[0] = acc[0];
        o[1] = acc[1];
        o[2] = acc[2];
        o[3] = acc[3];
    }
}

bool image_resize(const Image *src, Image *dst, int w, int h)
{
    if (!image_alloc(dst, w, h))
        return false;
    float *a = (float *)ks_alloc((size_t)src->w * src->h * 4 * sizeof(float));
    float *m = (float *)ks_alloc((size_t)w * src->h * 4 * sizeof(float));
    float *o = (float *)ks_alloc((size_t)w * h * 4 * sizeof(float));
    if (!a || !m || !o) {
        ks_free(a);
        ks_free(m);
        ks_free(o);
        image_free(dst);
        return false;
    }
    for (int i = 0; i < src->w * src->h; i++) {
        uint32_t p = src->px[i];
        a[i * 4 + 0] = (float)(p & 0xFF);
        a[i * 4 + 1] = (float)((p >> 8) & 0xFF);
        a[i * 4 + 2] = (float)((p >> 16) & 0xFF);
        a[i * 4 + 3] = (float)(p >> 24);
    }
    for (int y = 0; y < src->h; y++)
        resample_line(a + (size_t)y * src->w * 4, src->w, 4, m + (size_t)y * w * 4, w, 4);
    for (int x = 0; x < w; x++)
        resample_line(m + (size_t)x * 4, src->h, w * 4, o + (size_t)x * 4, h, w * 4);
    for (int i = 0; i < w * h; i++) {
        uint32_t ch[4];
        for (int k = 0; k < 4; k++)
            ch[k] = (uint32_t)KS_CLAMP(o[i * 4 + k] + 0.5f, 0.0f, 255.0f);
        /* Premultiplied: colour can never exceed alpha. */
        for (int k = 0; k < 3; k++)
            if (ch[k] > ch[3])
                ch[k] = ch[3];
        dst->px[i] = ch[0] | (ch[1] << 8) | (ch[2] << 16) | (ch[3] << 24);
    }
    ks_free(a);
    ks_free(m);
    ks_free(o);
    return true;
}

void gfx_draw_image(Canvas *cv, const Image *img, int x, int y, float alpha)
{
    int ga = (int)(KS_CLAMP(alpha, 0.0f, 1.0f) * 256.0f + 0.5f);
    if (ga <= 0)
        return;
    for (int iy = 0; iy < img->h; iy++) {
        int py = y + iy;
        if (py < cv->clip_y0 || py >= cv->clip_y1)
            continue;
        uint32_t *row = cv->px + (size_t)py * cv->stride;
        const uint32_t *srow = img->px + (size_t)iy * img->w;
        for (int ix = 0; ix < img->w; ix++) {
            int px = x + ix;
            if (px < cv->clip_x0 || px >= cv->clip_x1)
                continue;
            uint32_t s = srow[ix];
            int sa = (int)(s >> 24) * ga >> 8;
            if (sa == 0)
                continue;
            int sr = (int)((s >> 16) & 0xFF) * ga >> 8;
            int sg = (int)((s >> 8) & 0xFF) * ga >> 8;
            int sb = (int)(s & 0xFF) * ga >> 8;
            uint32_t d = row[px];
            int inv = 255 - sa;
            int dr = sr + ((int)((d >> 16) & 0xFF) * inv + 127) / 255;
            int dg = sg + ((int)((d >> 8) & 0xFF) * inv + 127) / 255;
            int db = sb + ((int)(d & 0xFF) * inv + 127) / 255;
            row[px] = 0xFF000000u | ((uint32_t)KS_MIN(dr, 255) << 16) | ((uint32_t)KS_MIN(dg, 255) << 8)
                      | (uint32_t)KS_MIN(db, 255);
        }
    }
}

void gfx_crossfade(Canvas *dst, const Canvas *src, float t)
{
    int a = (int)(KS_CLAMP(t, 0.0f, 1.0f) * 256.0f + 0.5f);
    int w = KS_MIN(dst->w, src->w), h = KS_MIN(dst->h, src->h);
    for (int y = 0; y < h; y++) {
        uint32_t *d = dst->px + (size_t)y * dst->stride;
        const uint32_t *s = src->px + (size_t)y * src->stride;
        for (int x = 0; x < w; x++) {
            uint32_t dp = d[x], sp = s[x];
            int dr = (int)((dp >> 16) & 0xFF), dg = (int)((dp >> 8) & 0xFF), db = (int)(dp & 0xFF);
            dr += (((int)((sp >> 16) & 0xFF) - dr) * a) >> 8;
            dg += (((int)((sp >> 8) & 0xFF) - dg) * a) >> 8;
            db += (((int)(sp & 0xFF) - db) * a) >> 8;
            d[x] = 0xFF000000u | ((uint32_t)dr << 16) | ((uint32_t)dg << 8) | (uint32_t)db;
        }
    }
}

void gfx_copy(Canvas *dst, const Canvas *src)
{
    int w = KS_MIN(dst->w, src->w), h = KS_MIN(dst->h, src->h);
    for (int y = 0; y < h; y++)
        memcpy(dst->px + (size_t)y * dst->stride, src->px + (size_t)y * src->stride, (size_t)w * 4);
}
