/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * text_gdi.c - text through GDI, in Segoe UI, straight into the canvas's
 * DIB section. Using the system's own text renderer is what makes the
 * installer's text indistinguishable from the rest of Windows: same
 * hinting, same ClearType or grayscale smoothing as the user has chosen.
 */

#include "text.h"

#ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

static HFONT g_fonts[FONT_COUNT];
static TEXTMETRICW g_metrics[FONT_COUNT];
static HDC g_measure_dc;

#define WBUF 512

/* UTF-8 to UTF-16 into buf (or a heap buffer when it does not fit). */
static WCHAR *widen(const char *s, int len, WCHAR *buf, int *out_len)
{
    if (len < 0)
        len = (int)ks_strlen(s);
    if (len == 0) {
        *out_len = 0;
        return buf;
    }
    int n = MultiByteToWideChar(CP_UTF8, 0, s, len, NULL, 0);
    WCHAR *w = buf;
    if (n > WBUF)
        w = (WCHAR *)ks_alloc(sizeof(WCHAR) * (size_t)n);
    if (!w) {
        *out_len = 0;
        return buf;
    }
    *out_len = MultiByteToWideChar(CP_UTF8, 0, s, len, w, n);
    return w;
}

static HFONT make_font(int px, bool semibold)
{
    HFONT f = CreateFontW(-px, 0, 0, 0, semibold ? FW_SEMIBOLD : FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                          OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, VARIABLE_PITCH | FF_SWISS,
                          semibold ? L"Segoe UI Semibold" : L"Segoe UI");
    if (!f && semibold)
        f = CreateFontW(-px, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_TT_PRECIS,
                        CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, VARIABLE_PITCH | FF_SWISS, L"Segoe UI");
    return f;
}

bool text_set_scale(float scale)
{
    if (!g_measure_dc) {
        g_measure_dc = CreateCompatibleDC(NULL);
        if (!g_measure_dc)
            return false;
    }
    for (int i = 0; i < FONT_COUNT; i++) {
        if (g_fonts[i])
            DeleteObject(g_fonts[i]);
        int px = (int)(k_font_spec[i].size * scale + 0.5f);
        g_fonts[i] = make_font(px, k_font_spec[i].semibold);
        SelectObject(g_measure_dc, g_fonts[i]);
        GetTextMetricsW(g_measure_dc, &g_metrics[i]);
    }
    return true;
}

void text_shutdown(void)
{
    if (g_measure_dc) {
        SelectObject(g_measure_dc, GetStockObject(SYSTEM_FONT));
        DeleteDC(g_measure_dc);
        g_measure_dc = NULL;
    }
    for (int i = 0; i < FONT_COUNT; i++) {
        if (g_fonts[i])
            DeleteObject(g_fonts[i]);
        g_fonts[i] = NULL;
    }
}

float text_width(FontId font, const char *s, int len)
{
    WCHAR buf[WBUF];
    int n;
    WCHAR *w = widen(s, len, buf, &n);
    SIZE size = { 0, 0 };
    if (n > 0) {
        SelectObject(g_measure_dc, g_fonts[font]);
        GetTextExtentPoint32W(g_measure_dc, w, n, &size);
    }
    if (w != buf)
        ks_free(w);
    return (float)size.cx;
}

void text_metrics(FontId font, float *ascent, float *descent)
{
    *ascent = (float)g_metrics[font].tmAscent;
    *descent = (float)g_metrics[font].tmDescent;
}

void text_draw(Canvas *cv, FontId font, const char *s, int len, float x, float baseline, Color color)
{
    HDC dc = (HDC)cv->native;
    if (!dc)
        return;
    WCHAR buf[WBUF];
    int n;
    WCHAR *w = widen(s, len, buf, &n);
    if (n > 0) {
        SelectObject(dc, g_fonts[font]);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(color_r(color), color_g(color), color_b(color)));
        SetTextAlign(dc, TA_BASELINE | TA_LEFT | TA_NOUPDATECP);
        RECT clip = { cv->clip_x0, cv->clip_y0, cv->clip_x1, cv->clip_y1 };
        ExtTextOutW(dc, (int)x, (int)baseline, ETO_CLIPPED, &clip, w, (UINT)n, NULL);
        /* The canvas is also written directly; GDI must finish first. */
        GdiFlush();
    }
    if (w != buf)
        ks_free(w);
}
