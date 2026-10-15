/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * text_ft.c - FreeType text backend, used only by the Linux screen
 * preview (preview.c). The real installer uses text_gdi.c.
 *
 * Fonts come from KS_FONT_REGULAR / KS_FONT_SEMIBOLD. For previews that
 * track the Windows layout closely, point them at a humanist sans with
 * metrics close to Segoe UI (Noto Sans works well).
 */

#include "../text.h"

#include <ft2build.h>
#include FT_FREETYPE_H

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static FT_Library g_ft;
static FT_Face g_regular, g_semibold;
static float g_scale = 1.0f;
static int g_px[FONT_COUNT];

static FT_Face face_for(FontId f)
{
    return k_font_spec[f].semibold ? g_semibold : g_regular;
}

bool text_set_scale(float scale)
{
    if (!g_ft) {
        const char *reg = getenv("KS_FONT_REGULAR");
        const char *sb = getenv("KS_FONT_SEMIBOLD");
        if (!reg || !sb) {
            fprintf(stderr, "Set KS_FONT_REGULAR and KS_FONT_SEMIBOLD to TTF paths.\n");
            return false;
        }
        if (FT_Init_FreeType(&g_ft) || FT_New_Face(g_ft, reg, 0, &g_regular) || FT_New_Face(g_ft, sb, 0, &g_semibold)) {
            fprintf(stderr, "Could not load fonts.\n");
            return false;
        }
    }
    g_scale = scale;
    for (int i = 0; i < FONT_COUNT; i++)
        g_px[i] = (int)(k_font_spec[i].size * scale + 0.5f);
    return true;
}

void text_shutdown(void)
{
    if (g_ft) {
        FT_Done_Face(g_regular);
        FT_Done_Face(g_semibold);
        FT_Done_FreeType(g_ft);
        g_ft = NULL;
    }
}

static FT_Face select_font(FontId f)
{
    FT_Face face = face_for(f);
    FT_Set_Pixel_Sizes(face, 0, (FT_UInt)g_px[f]);
    return face;
}

float text_width(FontId font, const char *s, int len)
{
    if (len < 0)
        len = (int)strlen(s);
    FT_Face face = select_font(font);
    const char *p = s, *end = s + len;
    long pen = 0;
    while (p < end) {
        uint32_t cp = ks_utf8_next(&p, end);
        if (FT_Load_Char(face, cp, FT_LOAD_DEFAULT | FT_LOAD_TARGET_LIGHT))
            continue;
        pen += face->glyph->advance.x;
    }
    return (float)pen / 64.0f;
}

void text_metrics(FontId font, float *ascent, float *descent)
{
    FT_Face face = select_font(font);
    *ascent = (float)face->size->metrics.ascender / 64.0f;
    *descent = (float)-face->size->metrics.descender / 64.0f;
}

void text_draw(Canvas *cv, FontId font, const char *s, int len, float x, float baseline, Color color)
{
    if (len < 0)
        len = (int)strlen(s);
    FT_Face face = select_font(font);
    const char *p = s, *end = s + len;
    long pen = (long)(x * 64.0f + 0.5f);
    int by = (int)floorf(baseline + 0.5f);
    int cr = (int)color_r(color), cg = (int)color_g(color), cb = (int)color_b(color);
    float ca = (float)color_a(color) / 255.0f;
    while (p < end) {
        uint32_t cp = ks_utf8_next(&p, end);
        if (FT_Load_Char(face, cp, FT_LOAD_RENDER | FT_LOAD_TARGET_LIGHT))
            continue;
        FT_GlyphSlot g = face->glyph;
        int gx = (int)((pen + 32) >> 6) + g->bitmap_left;
        int gy = by - g->bitmap_top;
        for (unsigned row = 0; row < g->bitmap.rows; row++) {
            int py = gy + (int)row;
            if (py < cv->clip_y0 || py >= cv->clip_y1)
                continue;
            uint32_t *dst = cv->px + (size_t)py * cv->stride;
            const unsigned char *src = g->bitmap.buffer + row * g->bitmap.pitch;
            for (unsigned col = 0; col < g->bitmap.width; col++) {
                int px = gx + (int)col;
                if (px < cv->clip_x0 || px >= cv->clip_x1 || !src[col])
                    continue;
                /* A little gamma so light-on-dark text keeps its weight,
                   roughly as Windows' own text rendering does. */
                float cov = powf(src[col] / 255.0f, 1.0f / 1.3f) * ca;
                uint32_t d = dst[px];
                int dr = (int)((d >> 16) & 0xFF), dg = (int)((d >> 8) & 0xFF), db = (int)(d & 0xFF);
                dr += (int)((cr - dr) * cov);
                dg += (int)((cg - dg) * cov);
                db += (int)((cb - db) * cov);
                dst[px] = 0xFF000000u | ((uint32_t)dr << 16) | ((uint32_t)dg << 8) | (uint32_t)db;
            }
        }
        pen += g->advance.x;
    }
}
