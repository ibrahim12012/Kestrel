/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * text.h - text measurement and drawing.
 *
 * Two implementations behind one interface: text_gdi.c draws with the
 * system's own Segoe UI through GDI, so text looks exactly like the rest
 * of Windows (hinting, ClearType, the user's font smoothing settings);
 * text_ft.c draws with FreeType for the Linux screen previews.
 *
 * All sizes are in physical pixels; the UI passes its DPI scale to
 * text_set_scale() whenever it changes. Strings are UTF-8.
 */

#ifndef KS_TEXT_H
#define KS_TEXT_H

#include "gfx.h"

typedef enum {
    FONT_DISPLAY,      /* the app name on the first page */
    FONT_TITLE,        /* page titles */
    FONT_BODY,
    FONT_BODY_STRONG,  /* buttons, emphasis */
    FONT_SMALL,
    FONT_SMALL_STRONG,
    FONT_CAPTION,
    FONT_COUNT
} FontId;

/* Size in DIPs (1/96 inch) and weight of each font. */
static const struct {
    float size;
    bool semibold;
} k_font_spec[FONT_COUNT] = {
    [FONT_DISPLAY] = { 26.0f, true },
    [FONT_TITLE] = { 19.0f, true },
    [FONT_BODY] = { 14.0f, false },
    [FONT_BODY_STRONG] = { 14.0f, true },
    [FONT_SMALL] = { 12.5f, false },
    [FONT_SMALL_STRONG] = { 12.5f, true },
    [FONT_CAPTION] = { 11.5f, false },
};

bool text_set_scale(float scale);
void text_shutdown(void);

/* len < 0 means NUL-terminated. */
float text_width(FontId font, const char *s, int len);
void text_metrics(FontId font, float *ascent, float *descent);
void text_draw(Canvas *cv, FontId font, const char *s, int len, float x, float baseline, Color color);

#endif
