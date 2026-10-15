/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * ui.c - the installer's screens. See ui.h.
 *
 * The look follows the app itself (app/theme.py, dark palette): near-black
 * surfaces, the #7c6aff accent, Segoe UI. Everything is laid out in DIPs
 * and multiplied by the DPI scale at draw time, and boxes are snapped to
 * whole pixels so edges stay sharp at 100%, 125%, 150% and up.
 */

#include "ui.h"

/* ── Palette ────────────────────────────────────────────────────────── */

#define C_BG            HEXC(0x0B0B0E)
#define C_FOOTER        HEXC(0x101014)
#define C_HAIRLINE      HEXC(0x1D1D23)
#define C_FIELD         HEXC(0x141418)
#define C_FIELD_BORDER  HEXC(0x28282F)
#define C_TEXT          HEXC(0xF2F2F4)
#define C_TEXT_2        HEXC(0xC4C4CC)
#define C_TEXT_MUTED    HEXC(0x8C8C96)
#define C_TEXT_FAINT    HEXC(0x676771)
#define C_TEXT_OFF      HEXC(0x55555C)
#define C_ACCENT        HEXC(0x7C6AFF)
#define C_ACCENT_HOVER  HEXC(0x8F7DFF)
#define C_ACCENT_DOWN   HEXC(0x6A58E0)
#define C_ACCENT_SOFT   HEXC(0xB7ACFF)
#define C_BTN           HEXC(0x1B1B20)
#define C_BTN_HOVER     HEXC(0x25252C)
#define C_BTN_DOWN      HEXC(0x17171B)
#define C_BTN_BORDER    HEXC(0x2E2E36)
#define C_DANGER        HEXC(0xD9474B)
#define C_DANGER_HOVER  HEXC(0xE5585C)
#define C_DANGER_DOWN   HEXC(0xC23A3E)
#define C_WARN          HEXC(0xE0B35C)
#define C_CHECK_BORDER  HEXC(0x3C3C45)
#define C_TRACK         HEXC(0x1E1E25)
#define C_WHITE         HEXC(0xFFFFFF)

#define FOOTER_H 76.0f
#define PAD 36.0f
#define TRANSITION 0.30
#define TRANSITION_OUT 0.35f   /* share of it spent fading the old page out */
#define ELLIPSIS "\xE2\x80\xA6"   /* U+2026 */

/* ── Licence text ───────────────────────────────────────────────────── */

/*
 * The licence arrives as lines of "<kind>\t<text>", produced from EULA.md
 * by installer/build_installer.py:
 *   T  the document title        H  a section heading
 *   P  a paragraph               B  a bullet point
 */
typedef struct LicenseLine {
    FontId font;
    Color color;
    const char *text;       /* points into ui->license */
    int len;
    float x;                /* relative to the text viewport, pixels */
    float y;                /* baseline, relative to the content top */
    bool bullet;
} LicenseLine;

/* ── Small helpers ──────────────────────────────────────────────────── */

static float S(const Ui *ui, float dip)
{
    return dip * ui->scale;
}

/* Snap a DIP rectangle to whole device pixels. */
static RectF px_rect(const Ui *ui, float x, float y, float w, float h)
{
    RectF r;
    r.x = ks_roundf(S(ui, x));
    r.y = ks_roundf(S(ui, y));
    r.w = ks_roundf(S(ui, x + w)) - r.x;
    r.h = ks_roundf(S(ui, y + h)) - r.y;
    return r;
}

static bool rect_contains(RectF r, float x, float y)
{
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static float approach(float value, float target, float dt, float speed)
{
    float step = dt * speed;
    if (value < target)
        return value + step > target ? target : value + step;
    return value - step < target ? target : value - step;
}

static float line_height(FontId f)
{
    float a, d;
    text_metrics(f, &a, &d);
    return a + d;
}

static float ascent_of(FontId f)
{
    float a, d;
    text_metrics(f, &a, &d);
    return a;
}

/* Baseline that centres the font's cap/x-height area on `center_y`. */
static float centered_baseline(FontId f, float center_y)
{
    float a, d;
    text_metrics(f, &a, &d);
    return ks_roundf(center_y + (a - d) * 0.5f - (a + d) * 0.06f);
}

static void draw_text(Canvas *cv, FontId f, const char *s, float x, float baseline, Color c)
{
    text_draw(cv, f, s, -1, ks_roundf(x), ks_roundf(baseline), c);
}

static void draw_text_centered(Canvas *cv, FontId f, const char *s, float cx, float baseline, Color c)
{
    float w = text_width(f, s, -1);
    draw_text(cv, f, s, cx - w * 0.5f, baseline, c);
}

static void draw_text_right(Canvas *cv, FontId f, const char *s, float right, float baseline, Color c)
{
    float w = text_width(f, s, -1);
    draw_text(cv, f, s, right - w, baseline, c);
}

/*
 * Greedy word wrap. Writes up to max_lines (start, length) pairs and
 * returns the number of lines. A single word wider than the line (a long
 * path) is broken wherever it has to be.
 */
static int wrap_text(FontId f, const char *s, int len, float max_w, int *starts, int *lens, int max_lines)
{
    if (len < 0)
        len = (int)ks_strlen(s);
    int count = 0, pos = 0;
    float space = text_width(f, " ", 1);
    while (pos < len && count < max_lines) {
        while (pos < len && s[pos] == ' ')
            pos++;
        if (pos >= len)
            break;
        int line_start = pos, line_end = pos;
        float line_w = 0.0f;
        while (pos < len) {
            int word_end = pos;
            while (word_end < len && s[word_end] != ' ')
                word_end++;
            float ww = text_width(f, s + pos, word_end - pos);
            float needed = line_end > line_start ? line_w + space + ww : ww;
            if (needed <= max_w || line_end == line_start) {
                if (line_end == line_start && ww > max_w) {
                    /* Break inside the word, on a UTF-8 boundary. */
                    int cut = pos + 1;
                    while (cut < word_end && text_width(f, s + pos, cut - pos + 1) <= max_w)
                        cut++;
                    while (cut < word_end && ((unsigned char)s[cut] & 0xC0) == 0x80)
                        cut++;
                    line_end = cut;
                    pos = cut;
                    break;
                }
                line_w = needed;
                line_end = word_end;
                pos = word_end;
                while (pos < len && s[pos] == ' ')
                    pos++;
                if (pos > word_end && pos < len)
                    continue;
                if (pos >= len)
                    break;
            } else {
                break;
            }
        }
        starts[count] = line_start;
        lens[count] = line_end - line_start;
        count++;
    }
    return count;
}

/* Draws a wrapped paragraph and returns the y just below it. */
static float draw_paragraph(Canvas *cv, FontId f, const char *s, float x, float top, float max_w, Color c,
                            float line_gap, bool center)
{
    int starts[24], lens[24];
    int n = wrap_text(f, s, -1, max_w, starts, lens, 24);
    if (n >= 2 && n <= 3) {
        /* Balance short paragraphs: the narrowest width that still needs
           the same number of lines, so the last line is not one lonely
           word. Same line count, so the height is unchanged. */
        float lo = max_w * 0.5f, hi = max_w;
        for (int i = 0; i < 12; i++) {
            float mid = (lo + hi) * 0.5f;
            int s2[24], l2[24];
            if (wrap_text(f, s, -1, mid, s2, l2, 24) == n)
                hi = mid;
            else
                lo = mid;
        }
        n = wrap_text(f, s, -1, hi, starts, lens, 24);
    }
    float lh = ks_roundf(line_height(f) * line_gap);
    float baseline = top + ascent_of(f);
    for (int i = 0; i < n; i++) {
        float lx = x;
        if (center)
            lx = x + (max_w - text_width(f, s + starts[i], lens[i])) * 0.5f;
        text_draw(cv, f, s + starts[i], lens[i], ks_roundf(lx), ks_roundf(baseline), c);
        baseline += lh;
    }
    return top + lh * (float)n;
}

/*
 * Shortens a path to fit, keeping its start (the drive) and its end (the
 * folder that matters) and dropping the middle: C:\Users\…\Programs\Kestrel
 */
static void ellipsize_middle(FontId f, const char *s, float max_w, char *out, size_t cap)
{
    int len = (int)ks_strlen(s);
    if (text_width(f, s, len) <= max_w) {
        ks_strlcpy(out, s, cap);
        return;
    }
    float ell = text_width(f, ELLIPSIS, -1);
    int keep_head = len / 3, keep_tail = len - keep_head;
    /* Shrink from the middle outward until it fits. */
    for (int drop = 1; drop < len; drop++) {
        int head = KS_MAX(0, keep_head - drop / 2);
        int tail_start = KS_MIN(len, keep_head + (drop + 1) / 2 + (len - keep_head - keep_tail));
        while (head > 0 && ((unsigned char)s[head] & 0xC0) == 0x80)
            head--;
        while (tail_start < len && ((unsigned char)s[tail_start] & 0xC0) == 0x80)
            tail_start++;
        float w = text_width(f, s, head) + ell + text_width(f, s + tail_start, len - tail_start);
        if (w <= max_w || head == 0) {
            size_t n = 0;
            for (int i = 0; i < head && n + 1 < cap; i++)
                out[n++] = s[i];
            for (const char *e = ELLIPSIS; *e && n + 1 < cap; e++)
                out[n++] = *e;
            for (int i = tail_start; i < len && n + 1 < cap; i++)
                out[n++] = s[i];
            out[n] = 0;
            return;
        }
    }
    ks_strlcpy(out, ELLIPSIS, cap);
}

/* ── Logo ───────────────────────────────────────────────────────────── */

static const Image *logo_at(Ui *ui, int size)
{
    if (!ui->logo.px || size <= 0)
        return NULL;
    for (int i = 0; i < KS_ARRAY_LEN(ui->logos); i++)
        if (ui->logos[i].size == size && ui->logos[i].img.px)
            return &ui->logos[i].img;
    /* Replace the first free (or the oldest) slot. */
    int slot = 0;
    for (int i = 0; i < KS_ARRAY_LEN(ui->logos); i++)
        if (!ui->logos[i].img.px) {
            slot = i;
            break;
        }
    image_free(&ui->logos[slot].img);
    if (!image_resize(&ui->logo, &ui->logos[slot].img, size, size))
        return NULL;
    ui->logos[slot].size = size;
    return &ui->logos[slot].img;
}

static void draw_logo(Ui *ui, Canvas *cv, float x_dip, float y_dip, float size_dip, float alpha)
{
    int size = (int)ks_roundf(S(ui, size_dip));
    const Image *img = logo_at(ui, size);
    if (img)
        gfx_draw_image(cv, img, (int)ks_roundf(S(ui, x_dip)), (int)ks_roundf(S(ui, y_dip)), alpha);
}

/* ── Backdrop ───────────────────────────────────────────────────────── */

static void build_background(Ui *ui)
{
    image_free(&ui->background);
    if (!image_alloc(&ui->background, ui->width, ui->height))
        return;
    Canvas cv;
    gfx_canvas_init(&cv, ui->background.px, ui->width, ui->height, ui->width);
    gfx_clear(&cv, C_BG);
    float w = (float)ui->width, h = (float)ui->height;
    /* A faint wash of the accent from above, and a cooler one bottom
       right, so the surface reads as lit rather than flat black. */
    gfx_radial_glow(&cv, w * 0.30f, -h * 0.10f, w * 0.85f, h * 0.62f, color_with_alpha(C_ACCENT, 0.16f));
    gfx_radial_glow(&cv, w * 1.02f, h * 1.05f, w * 0.60f, h * 0.55f, color_with_alpha(HEXC(0x3A6BFF), 0.06f));
    /* Footer band and its hairline. */
    float fy = ks_roundf(S(ui, UI_HEIGHT - FOOTER_H));
    gfx_fill_rect(&cv, 0, fy, w, h - fy, color_with_alpha(C_FOOTER, 0.92f));
    gfx_fill_rect(&cv, 0, fy, w, ks_roundf(S(ui, 1.0f)), C_HAIRLINE);
}

/* ── Widgets ────────────────────────────────────────────────────────── */

typedef enum { BTN_PRIMARY, BTN_SECONDARY, BTN_DANGER } ButtonStyle;

static void focus_ring(Ui *ui, Canvas *cv, RectF r, float radius)
{
    float g = S(ui, 3.0f);
    gfx_stroke_rrect(cv, r.x - g, r.y - g, r.w + 2 * g, r.h + 2 * g, radius + g, ks_roundf(S(ui, 2.0f)),
                     color_with_alpha(C_ACCENT_SOFT, 0.85f));
}

static float button_width(Ui *ui, const char *label)
{
    float w = text_width(FONT_BODY_STRONG, label, -1) + S(ui, 44.0f);
    return KS_MAX(w, S(ui, 104.0f));
}

static void draw_button(Ui *ui, Canvas *cv, WidgetId id, const char *label, ButtonStyle style, float dy)
{
    WidgetRect *wr = &ui->layout.w[id];
    if (!wr->visible)
        return;
    RectF r = wr->r;
    r.y += dy;
    float radius = S(ui, 8.0f);
    float hover = ui->hover_t[id];
    bool down = ui->pressed == id && ui->hot == id;
    Color fill, text = C_WHITE, border = 0;

    if (!wr->enabled) {
        fill = C_BTN;
        text = C_TEXT_OFF;
        border = C_BTN_BORDER;
    } else if (style == BTN_PRIMARY) {
        fill = down ? C_ACCENT_DOWN : color_lerp(C_ACCENT, C_ACCENT_HOVER, hover);
    } else if (style == BTN_DANGER) {
        fill = down ? C_DANGER_DOWN : color_lerp(C_DANGER, C_DANGER_HOVER, hover);
    } else {
        fill = down ? C_BTN_DOWN : color_lerp(C_BTN, C_BTN_HOVER, hover);
        border = color_lerp(C_BTN_BORDER, HEXC(0x3A3A44), hover);
        text = C_TEXT;
    }

    if (wr->enabled && style != BTN_SECONDARY) {
        /* A 1px lighter top edge gives the filled buttons a little depth. */
        gfx_fill_rrect_vgradient(cv, r.x, r.y, r.w, r.h, radius, color_lerp(fill, C_WHITE, 0.07f), fill);
    } else {
        gfx_fill_rrect(cv, r.x, r.y, r.w, r.h, radius, fill);
    }
    if (border)
        gfx_stroke_rrect(cv, r.x, r.y, r.w, r.h, radius, ks_roundf(S(ui, 1.0f)), border);

    float tw = text_width(FONT_BODY_STRONG, label, -1);
    float baseline = centered_baseline(FONT_BODY_STRONG, r.y + r.h * 0.5f);
    draw_text(cv, FONT_BODY_STRONG, label, r.x + (r.w - tw) * 0.5f, baseline, text);

    if (ui->focused == id && ui->focus_visible)
        focus_ring(ui, cv, r, radius);
}

static void draw_check_glyph(Ui *ui, Canvas *cv, float x, float y, float size, float t, Color c, float width_dip)
{
    float pts[6] = {
        x + size * 0.24f, y + size * 0.52f,
        x + size * 0.43f, y + size * 0.70f,
        x + size * 0.77f, y + size * 0.32f,
    };
    gfx_polyline(cv, pts, 3, S(ui, width_dip), c, t);
}

static void draw_checkbox(Ui *ui, Canvas *cv, WidgetId id, const char *label, bool checked, float dy)
{
    WidgetRect *wr = &ui->layout.w[id];
    if (!wr->visible)
        return;
    RectF row = wr->r;
    row.y += dy;
    float box = ks_roundf(S(ui, 18.0f));
    float bx = row.x, by = ks_roundf(row.y + (row.h - box) * 0.5f);
    float radius = S(ui, 5.0f);
    float hover = ui->hover_t[id];
    float t = ui->check_t[id];

    Color border = color_lerp(C_CHECK_BORDER, HEXC(0x5A5A66), hover);
    gfx_fill_rrect(cv, bx, by, box, box, radius, C_FIELD);
    gfx_stroke_rrect(cv, bx, by, box, box, radius, ks_roundf(S(ui, 1.5f)), border);
    if (t > 0.0f) {
        Color fill = color_lerp(C_ACCENT, C_ACCENT_HOVER, hover);
        gfx_fill_rrect(cv, bx, by, box, box, radius, color_with_alpha(fill, KS_MIN(1.0f, t * 1.6f)));
        draw_check_glyph(ui, cv, bx, by, box, ks_ease_out_cubic(t), C_WHITE, 2.0f);
    }

    float baseline = centered_baseline(FONT_BODY, row.y + row.h * 0.5f);
    draw_text(cv, FONT_BODY, label, bx + box + S(ui, 11.0f), baseline, wr->enabled ? C_TEXT : C_TEXT_OFF);
    (void)checked;

    if (ui->focused == id && ui->focus_visible) {
        RectF fr = { bx, by, box, box };
        focus_ring(ui, cv, fr, radius);
    }
}

static void draw_link(Ui *ui, Canvas *cv, WidgetId id, const char *label, FontId font, float dy)
{
    WidgetRect *wr = &ui->layout.w[id];
    if (!wr->visible)
        return;
    RectF r = wr->r;
    r.y += dy;
    float hover = ui->hover_t[id];
    Color c = color_lerp(C_ACCENT_SOFT, HEXC(0xD9D2FF), hover);
    float baseline = centered_baseline(font, r.y + r.h * 0.5f);
    draw_text(cv, font, label, r.x, baseline, c);
    if (hover > 0.01f) {
        float uw = text_width(font, label, -1);
        gfx_fill_rect(cv, r.x, baseline + S(ui, 2.0f), uw, ks_roundf(S(ui, 1.0f)), color_with_alpha(c, hover * 0.8f));
    }
    if (ui->focused == id && ui->focus_visible)
        focus_ring(ui, cv, r, S(ui, 4.0f));
}

/* The folder glyph in the install location field. */
static void draw_folder_icon(Ui *ui, Canvas *cv, float x, float y, Color c)
{
    float s = ui->scale;
    gfx_fill_rrect(cv, x, y + 1.0f * s, 7.0f * s, 4.0f * s, 1.5f * s, c);
    gfx_fill_rrect(cv, x, y + 3.0f * s, 16.0f * s, 11.0f * s, 2.0f * s, c);
    gfx_fill_rect(cv, x + 1.5f * s, y + 5.5f * s, 13.0f * s, 1.0f * s, color_with_alpha(C_FIELD, 0.7f));
}

/* A round status badge: check (success), ! (warning), x (error). */
typedef enum { BADGE_CHECK, BADGE_WARN, BADGE_ERROR, BADGE_INFO } BadgeKind;

static void draw_badge(Ui *ui, Canvas *cv, float cx, float cy, float r, BadgeKind kind, float appear, float stroke)
{
    float grow = ks_ease_out_cubic(KS_MIN(1.0f, appear * 1.6f));
    float rr = r * (0.6f + 0.4f * grow);
    Color base = kind == BADGE_CHECK ? C_ACCENT : kind == BADGE_WARN ? C_WARN : kind == BADGE_ERROR ? C_DANGER : C_TEXT_MUTED;
    float a = KS_MIN(1.0f, appear * 2.0f);
    if (kind == BADGE_CHECK) {
        /* Soft light behind the success mark. */
        gfx_radial_glow(cv, cx, cy, rr * 2.6f, rr * 2.6f, color_with_alpha(base, 0.30f * a));
        gfx_stroke_circle(cv, cx, cy, rr * 1.42f, S(ui, 1.0f), color_with_alpha(base, 0.22f * a));
        gfx_fill_circle(cv, cx, cy, rr, color_with_alpha(base, a));
        float t = KS_CLAMP((appear - 0.25f) / 0.55f, 0.0f, 1.0f);
        draw_check_glyph(ui, cv, cx - rr, cy - rr, rr * 2.0f, ks_ease_out_cubic(t), color_with_alpha(C_WHITE, a), stroke);
        return;
    }
    gfx_fill_circle(cv, cx, cy, rr, color_with_alpha(base, 0.16f * a));
    gfx_stroke_circle(cv, cx, cy, rr, S(ui, 1.5f), color_with_alpha(base, 0.55f * a));
    Color fg = color_with_alpha(base, a);
    float w = S(ui, stroke);
    if (kind == BADGE_ERROR) {
        float k = rr * 0.34f;
        gfx_line(cv, cx - k, cy - k, cx + k, cy + k, w, fg);
        gfx_line(cv, cx - k, cy + k, cx + k, cy - k, w, fg);
    } else if (kind == BADGE_WARN) {
        gfx_line(cv, cx, cy - rr * 0.42f, cx, cy + rr * 0.08f, w, fg);
        gfx_fill_circle(cv, cx, cy + rr * 0.38f, w * 0.62f, fg);
    } else {
        gfx_fill_circle(cv, cx, cy - rr * 0.40f, w * 0.62f, fg);
        gfx_line(cv, cx, cy - rr * 0.08f, cx, cy + rr * 0.42f, w, fg);
    }
}

static void draw_progress_bar(Ui *ui, Canvas *cv, RectF r)
{
    float radius = r.h * 0.5f;
    gfx_fill_rrect(cv, r.x, r.y, r.w, r.h, radius, C_TRACK);
    if (ui->progress < 0.0f) {
        /* No figure yet: a segment gliding along the track. */
        float period = 1.4f;
        float phase = (float)(ui->now / period);
        phase -= ks_floorf(phase);
        float t = ks_ease_in_out_cubic(phase);
        float seg = r.w * 0.28f;
        float x = r.x - seg + (r.w + seg) * t;
        gfx_set_clip(cv, r.x, r.y, r.x + r.w, r.y + r.h);
        gfx_fill_rrect(cv, x, r.y, seg, r.h, radius, C_ACCENT);
        gfx_reset_clip(cv);
        return;
    }
    float fw = r.w * KS_CLAMP(ui->progress_shown, 0.0f, 1.0f);
    if (fw < r.h)
        fw = fw > 0.5f ? r.h : 0.0f;
    if (fw > 0.0f) {
        gfx_fill_rrect_vgradient(cv, r.x, r.y, fw, r.h, radius, C_ACCENT_HOVER, C_ACCENT);
        /* A sheen travelling along the filled part while work goes on. */
        if (ui->progress_shown < 0.999f) {
            float phase = (float)(ui->now / 1.6);
            phase -= ks_floorf(phase);
            float sw = S(ui, 90.0f);
            float sx = r.x - sw + (fw + sw) * phase;
            gfx_set_clip(cv, r.x, r.y, r.x + fw, r.y + r.h);
            gfx_radial_glow(cv, sx + sw * 0.5f, r.y + r.h * 0.5f, sw * 0.5f, r.h * 2.0f, color_with_alpha(C_WHITE, 0.35f));
            gfx_reset_clip(cv);
        }
    }
}

/* ── Strings ────────────────────────────────────────────────────────── */

static const char *primary_label(const Ui *ui, PageId page)
{
    bool update = ui->existing_version[0] != 0;
    bool same = update && ks_strcmp(ui->existing_version, ui->app_version) == 0;
    switch (page) {
    case PAGE_WELCOME:
    case PAGE_LICENSE:
        return same ? "Accept and reinstall" : update ? "Accept and update" : "Accept and install";
    case PAGE_APP_RUNNING:
        if (ui->app_unresponsive)
            return ui->uninstall_mode ? "End it and uninstall" : "End it and continue";
        return ui->app_closing ? "Closing" ELLIPSIS : "Close it and continue";
    case PAGE_DONE: {
        static char open_label[96];
        ks_format(open_label, sizeof open_label, "Open %s", ui->app_name);
        return open_label;
    }
    case PAGE_CANCELED:
    case PAGE_REMOVED:
        return "Close";
    case PAGE_ERROR:
        return "Try again";
    case PAGE_UNINSTALL:
        return "Uninstall";
    default:
        return "";
    }
}

static const char *secondary_label(const Ui *ui, PageId page)
{
    (void)ui;
    switch (page) {
    case PAGE_LICENSE:
        return "Back";
    case PAGE_DONE:
    case PAGE_ERROR:
        return "Close";
    default:
        return "Cancel";
    }
}

/* ── Layout ─────────────────────────────────────────────────────────── */

static void set_widget(Layout *l, WidgetId id, RectF r, bool enabled)
{
    l->w[id].r = r;
    l->w[id].visible = true;
    l->w[id].enabled = enabled;
}

/* Footer buttons, right-aligned: [secondary] [primary]. */
static void layout_footer(Ui *ui, Layout *l, PageId page, bool has_primary, bool has_secondary, bool primary_enabled,
                          bool secondary_enabled)
{
    float h = ks_roundf(S(ui, 36.0f));
    float y = ks_roundf(S(ui, UI_HEIGHT - FOOTER_H * 0.5f) - h * 0.5f);
    float right = ks_roundf(S(ui, UI_WIDTH - PAD + 4.0f));
    float gap = ks_roundf(S(ui, 10.0f));
    if (has_primary) {
        float w = ks_roundf(button_width(ui, primary_label(ui, page)));
        RectF r = { right - w, y, w, h };
        set_widget(l, W_PRIMARY, r, primary_enabled);
        right -= w + gap;
    }
    if (has_secondary) {
        float w = ks_roundf(button_width(ui, secondary_label(ui, page)));
        RectF r = { right - w, y, w, h };
        set_widget(l, W_SECONDARY, r, secondary_enabled);
    }
}

static void layout_footer_link(Ui *ui, Layout *l, const char *label)
{
    float w = text_width(FONT_SMALL, label, -1);
    float h = S(ui, 24.0f);
    RectF r = { ks_roundf(S(ui, PAD)), ks_roundf(S(ui, UI_HEIGHT - FOOTER_H * 0.5f) - h * 0.5f), ks_ceilf(w), h };
    set_widget(l, W_LINK, r, true);
}

static float checkbox_row_width(Ui *ui, const char *label)
{
    return S(ui, 18.0f + 11.0f) + text_width(FONT_BODY, label, -1) + S(ui, 4.0f);
}

static const char *const LBL_DESKTOP = "Create a desktop shortcut";
static const char *const LBL_STARTUP = "Start when I sign in to Windows";
static const char *const LBL_DELETE = "Also delete my chats, macros and settings";
static const char *const LBL_LICENSE_LINK = "Read the license agreement";
static const char *const LBL_OPEN_LOG = "Open the setup log";

static const char *const TXT_TAGLINE = "Talk to your computer like a person. It talks back, and acts.";

/* Vertical positions on the welcome page (DIP). Everything below the
   description flows from its height, which depends on the font and on
   how many lines it wraps to. */
#define W_DESC_TOP 110.0f
#define W_FIELD_H 44.0f

static float paragraph_height(Ui *ui, FontId f, const char *s, float width_dip, float line_gap)
{
    int starts[24], lens[24];
    int n = wrap_text(f, s, -1, S(ui, width_dip), starts, lens, 24);
    return ks_roundf(line_height(f) * line_gap) * (float)n / ui->scale;
}

static void compute_layout(Ui *ui, PageId page, Layout *l)
{
    memset(l, 0, sizeof(*l));
    switch (page) {
    case PAGE_WELCOME: {
        bool update = ui->existing_version[0] != 0;
        float y = W_DESC_TOP + paragraph_height(ui, FONT_BODY, TXT_TAGLINE, UI_WIDTH - 2 * PAD, 1.3f) + 22.0f;
        l->label_baseline = S(ui, y);
        y += 10.0f;
        RectF field = px_rect(ui, PAD, y, UI_WIDTH - 2 * PAD, W_FIELD_H);
        l->field = field;
        y += W_FIELD_H + 16.0f;
        if (!update) {
            float bw = ks_roundf(text_width(FONT_SMALL_STRONG, "Change", -1) + S(ui, 26.0f));
            float bh = ks_roundf(S(ui, 30.0f));
            RectF change = { field.x + field.w - bw - ks_roundf(S(ui, 7.0f)), field.y + ks_roundf((field.h - bh) * 0.5f), bw, bh };
            set_widget(l, W_CHANGE_DIR, change, true);
        }
        RectF o1 = { ks_roundf(S(ui, PAD)), ks_roundf(S(ui, y)), checkbox_row_width(ui, LBL_DESKTOP), ks_roundf(S(ui, 26.0f)) };
        RectF o2 = { ks_roundf(S(ui, PAD)), ks_roundf(S(ui, y + 32.0f)), checkbox_row_width(ui, LBL_STARTUP), ks_roundf(S(ui, 26.0f)) };
        set_widget(l, W_OPT_DESKTOP, o1, true);
        set_widget(l, W_OPT_STARTUP, o2, true);
        layout_footer_link(ui, l, LBL_LICENSE_LINK);
        layout_footer(ui, l, page, true, true, true, true);
        break;
    }
    case PAGE_LICENSE: {
        RectF back = px_rect(ui, PAD - 10.0f, 26.0f, 32.0f, 32.0f);
        set_widget(l, W_BACK, back, true);
        l->license_view = px_rect(ui, PAD, 76.0f, UI_WIDTH - 2 * PAD, UI_HEIGHT - FOOTER_H - 76.0f - 20.0f);
        layout_footer(ui, l, page, true, true, true, true);
        break;
    }
    case PAGE_APP_RUNNING:
        layout_footer(ui, l, page, true, true, !ui->app_closing || ui->app_unresponsive, true);
        break;
    case PAGE_PROGRESS:
        layout_footer(ui, l, page, false, true, false, ui->progress_cancelable);
        l->w[W_SECONDARY].visible = true;
        break;
    case PAGE_DONE:
        layout_footer(ui, l, page, true, true, true, true);
        break;
    case PAGE_CANCELED:
    case PAGE_REMOVED:
        layout_footer(ui, l, page, true, false, true, true);
        break;
    case PAGE_ERROR:
        layout_footer_link(ui, l, LBL_OPEN_LOG);
        layout_footer(ui, l, page, true, true, true, true);
        break;
    case PAGE_UNINSTALL: {
        RectF o = { ks_roundf(S(ui, PAD)), ks_roundf(S(ui, 150.0f)), checkbox_row_width(ui, LBL_DELETE), ks_roundf(S(ui, 26.0f)) };
        set_widget(l, W_OPT_DELETE_DATA, o, true);
        layout_footer(ui, l, page, true, true, true, true);
        break;
    }
    default:
        break;
    }
}

/* ── Licence layout ─────────────────────────────────────────────────── */

static void license_relayout(Ui *ui, float width)
{
    if (!ui->license)
        return;
    if (ui->license_lines && ui->license_wrap_width == width)
        return;
    ks_free(ui->license_lines);
    ui->license_lines = NULL;
    ui->license_line_count = 0;
    ui->license_wrap_width = width;

    int cap = 0;
    float y = 0.0f;
    const char *p = ui->license;
    while (*p) {
        const char *eol = p;
        while (*eol && *eol != '\n')
            eol++;
        char kind = *p;
        const char *text = p;
        if (eol - p >= 2 && p[1] == '\t')
            text = p + 2;
        else
            kind = 'P';
        int tlen = (int)(eol - text);

        FontId font = FONT_SMALL;
        Color color = C_TEXT_2;
        float indent = 0.0f, before = 0.0f, after = S(ui, 9.0f);
        bool bullet = false;
        if (kind == 'T') {
            font = FONT_BODY_STRONG;
            color = C_TEXT;
            after = S(ui, 12.0f);
        } else if (kind == 'H') {
            font = FONT_SMALL_STRONG;
            color = C_TEXT;
            before = S(ui, 8.0f);
            after = S(ui, 6.0f);
        } else if (kind == 'B') {
            indent = S(ui, 16.0f);
            bullet = true;
            after = S(ui, 5.0f);
        }

        int starts[64], lens[64];
        int n = tlen > 0 ? wrap_text(font, text, tlen, width - indent, starts, lens, 64) : 0;
        float lh = ks_roundf(line_height(font) * 1.18f);
        y += before;
        for (int i = 0; i < n; i++) {
            if (ui->license_line_count == cap) {
                cap = cap ? cap * 2 : 128;
                LicenseLine *grown = (LicenseLine *)ks_realloc(ui->license_lines, sizeof(LicenseLine) * (size_t)cap);
                if (!grown)
                    return;
                ui->license_lines = grown;
            }
            LicenseLine *ln = &ui->license_lines[ui->license_line_count++];
            ln->font = font;
            ln->color = color;
            ln->text = text + starts[i];
            ln->len = lens[i];
            ln->x = indent;
            ln->y = y + ascent_of(font);
            ln->bullet = bullet && i == 0;
            y += lh;
        }
        y += after;
        p = *eol ? eol + 1 : eol;
    }
    ui->license_content_h = y;
}

static float license_max_scroll(Ui *ui)
{
    float view = ui->layout.license_view.h - S(ui, 32.0f);
    return KS_MAX(0.0f, ui->license_content_h - view);
}

static void license_scroll_by(Ui *ui, float delta)
{
    ui->license_scroll_target = KS_CLAMP(ui->license_scroll_target + delta, 0.0f, license_max_scroll(ui));
}

/* Scrollbar geometry for the licence viewport. */
static bool license_thumb(Ui *ui, RectF *track, RectF *thumb)
{
    RectF v = ui->layout.license_view;
    float max = license_max_scroll(ui);
    if (max <= 0.0f)
        return false;
    float inset = S(ui, 6.0f);
    track->x = v.x + v.w - S(ui, 10.0f);
    track->y = v.y + inset;
    track->w = S(ui, 4.0f);
    track->h = v.h - 2 * inset;
    float visible = v.h - S(ui, 32.0f);
    float th = KS_MAX(S(ui, 28.0f), track->h * visible / (visible + max));
    *thumb = *track;
    thumb->h = th;
    thumb->y = track->y + (track->h - th) * (ui->license_scroll / max);
    return true;
}

/* ── Page painting ──────────────────────────────────────────────────── */

static void paint_header(Ui *ui, Canvas *cv, float dy, float logo_dip, const char *title, FontId title_font,
                         const char *subtitle)
{
    float top = 34.0f;
    draw_logo(ui, cv, PAD, top + dy / ui->scale, logo_dip, 1.0f);
    float tx = S(ui, PAD + logo_dip + 16.0f);
    float mid = S(ui, top + logo_dip * 0.5f) + dy;
    float ta = ascent_of(title_font);
    float sa = ascent_of(FONT_SMALL);
    float block = ta + S(ui, 6.0f) + sa;
    float title_base = mid - block * 0.5f + ta - S(ui, 1.0f);
    draw_text(cv, title_font, title, tx, title_base, C_TEXT);
    if (subtitle && *subtitle)
        draw_text(cv, FONT_SMALL, subtitle, tx, title_base + S(ui, 6.0f) + sa + S(ui, 1.0f), C_TEXT_MUTED);
}

static void paint_status_header(Ui *ui, Canvas *cv, float dy, BadgeKind kind, const char *title)
{
    float cx = S(ui, PAD + 20.0f), cy = S(ui, 56.0f) + dy;
    draw_badge(ui, cv, cx, cy, S(ui, 20.0f), kind, 1.0f, 2.2f);
    draw_text(cv, FONT_TITLE, title, S(ui, PAD + 56.0f), centered_baseline(FONT_TITLE, cy), C_TEXT);
}

static void paint_welcome(Ui *ui, Canvas *cv, float dy)
{
    char subtitle[96];
    if (ui->existing_version[0] && ks_strcmp(ui->existing_version, ui->app_version) != 0)
        ks_format(subtitle, sizeof subtitle, "Update from %s to %s", ui->existing_version, ui->app_version);
    else if (ui->existing_version[0])
        ks_format(subtitle, sizeof subtitle, "Version %s, already installed", ui->app_version);
    else
        ks_format(subtitle, sizeof subtitle, "Version %s", ui->app_version);
    paint_header(ui, cv, dy, 52.0f, ui->app_name, FONT_DISPLAY, subtitle);

    draw_paragraph(cv, FONT_BODY, TXT_TAGLINE, S(ui, PAD), S(ui, W_DESC_TOP) + dy, S(ui, UI_WIDTH - 2 * PAD), C_TEXT_2,
                   1.3f, false);

    draw_text(cv, FONT_SMALL, ui->existing_version[0] ? "Installed in" : "Install location", S(ui, PAD),
              ui->layout.label_baseline + dy, C_TEXT_MUTED);

    RectF field = ui->layout.field;
    field.y += dy;
    float radius = S(ui, 9.0f);
    gfx_fill_rrect(cv, field.x, field.y, field.w, field.h, radius, C_FIELD);
    gfx_stroke_rrect(cv, field.x, field.y, field.w, field.h, radius, ks_roundf(S(ui, 1.0f)), C_FIELD_BORDER);
    draw_folder_icon(ui, cv, field.x + S(ui, 14.0f), ks_roundf(field.y + field.h * 0.5f - S(ui, 8.0f)), C_TEXT_MUTED);

    WidgetRect *change = &ui->layout.w[W_CHANGE_DIR];
    float text_x = field.x + S(ui, 42.0f);
    float text_right = change->visible ? change->r.x - S(ui, 10.0f) : field.x + field.w - S(ui, 14.0f);
    char shown[1024];
    ellipsize_middle(FONT_BODY, ui->install_dir, text_right - text_x, shown, sizeof shown);
    draw_text(cv, FONT_BODY, shown, text_x, centered_baseline(FONT_BODY, field.y + field.h * 0.5f), C_TEXT);

    if (change->visible) {
        RectF r = change->r;
        r.y += dy;
        float hover = ui->hover_t[W_CHANGE_DIR];
        bool down = ui->pressed == W_CHANGE_DIR && ui->hot == W_CHANGE_DIR;
        Color fill = down ? C_BTN_DOWN : color_lerp(C_BTN, C_BTN_HOVER, hover);
        gfx_fill_rrect(cv, r.x, r.y, r.w, r.h, S(ui, 6.0f), fill);
        gfx_stroke_rrect(cv, r.x, r.y, r.w, r.h, S(ui, 6.0f), ks_roundf(S(ui, 1.0f)), color_lerp(C_BTN_BORDER, HEXC(0x3A3A44), hover));
        float tw = text_width(FONT_SMALL_STRONG, "Change", -1);
        draw_text(cv, FONT_SMALL_STRONG, "Change", r.x + (r.w - tw) * 0.5f, centered_baseline(FONT_SMALL_STRONG, r.y + r.h * 0.5f), C_TEXT);
        if (ui->focused == W_CHANGE_DIR && ui->focus_visible)
            focus_ring(ui, cv, r, S(ui, 6.0f));
    }

    draw_checkbox(ui, cv, W_OPT_DESKTOP, LBL_DESKTOP, ui->opt_desktop, dy);
    draw_checkbox(ui, cv, W_OPT_STARTUP, LBL_STARTUP, ui->opt_startup, dy);
}

static void paint_license(Ui *ui, Canvas *cv, float dy)
{
    /* Back arrow. */
    WidgetRect *back = &ui->layout.w[W_BACK];
    RectF br = back->r;
    br.y += dy;
    float hover = ui->hover_t[W_BACK];
    if (hover > 0.0f)
        gfx_fill_rrect(cv, br.x, br.y, br.w, br.h, S(ui, 8.0f), color_with_alpha(C_BTN_HOVER, hover));
    float cx = br.x + br.w * 0.5f, cy = br.y + br.h * 0.5f, k = S(ui, 5.5f), w = S(ui, 1.8f);
    float arrow[6] = { cx + k * 0.1f, cy - k, cx - k + S(ui, 0.8f), cy, cx + k * 0.1f, cy + k };
    gfx_polyline(cv, arrow, 3, w, C_TEXT, 1.0f);
    gfx_line(cv, cx - k + S(ui, 1.2f), cy, cx + k, cy, w, C_TEXT);
    if (ui->focused == W_BACK && ui->focus_visible)
        focus_ring(ui, cv, br, S(ui, 8.0f));

    draw_text(cv, FONT_TITLE, "License agreement", br.x + br.w + S(ui, 10.0f), centered_baseline(FONT_TITLE, cy), C_TEXT);

    RectF v = ui->layout.license_view;
    v.y += dy;
    float radius = S(ui, 10.0f);
    gfx_fill_rrect(cv, v.x, v.y, v.w, v.h, radius, C_FIELD);
    gfx_stroke_rrect(cv, v.x, v.y, v.w, v.h, radius, ks_roundf(S(ui, 1.0f)), C_FIELD_BORDER);

    float inner_x = v.x + S(ui, 18.0f), inner_y = v.y + S(ui, 16.0f);
    float inner_w = v.w - S(ui, 18.0f + 26.0f);
    license_relayout(ui, inner_w);

    gfx_set_clip(cv, v.x + 1, v.y + S(ui, 2.0f), v.x + v.w - 1, v.y + v.h - S(ui, 2.0f));
    float top = inner_y - ks_roundf(ui->license_scroll);
    for (int i = 0; i < ui->license_line_count; i++) {
        LicenseLine *ln = &ui->license_lines[i];
        float baseline = top + ln->y;
        if (baseline < v.y - S(ui, 40.0f))
            continue;
        if (baseline > v.y + v.h + S(ui, 40.0f))
            break;
        if (ln->bullet)
            gfx_fill_circle(cv, inner_x + S(ui, 5.0f), baseline - S(ui, 4.0f), S(ui, 2.2f), C_TEXT_MUTED);
        text_draw(cv, ln->font, ln->text, ln->len, ks_roundf(inner_x + ln->x), ks_roundf(baseline), ln->color);
    }
    /* Fade the text out under the top and bottom edges. */
    float fade = S(ui, 14.0f), b1 = ks_roundf(S(ui, 1.0f));
    gfx_fill_rrect_vgradient(cv, v.x + b1, v.y + b1, v.w - 2 * b1, fade, radius - b1, C_FIELD, color_with_alpha(C_FIELD, 0.0f));
    gfx_fill_rrect_vgradient(cv, v.x + b1, v.y + v.h - b1 - fade, v.w - 2 * b1, fade, radius - b1, color_with_alpha(C_FIELD, 0.0f), C_FIELD);
    gfx_reset_clip(cv);

    RectF track, thumb;
    if (license_thumb(ui, &track, &thumb)) {
        thumb.y += dy;
        float active = KS_MAX(ui->hover_t[W_SCROLL_THUMB], ui->license_dragging ? 1.0f : 0.0f);
        Color tc = color_lerp(HEXC(0x3A3A44), HEXC(0x5E5E6A), active);
        gfx_fill_rrect(cv, thumb.x, thumb.y, thumb.w, thumb.h, thumb.w * 0.5f, tc);
    }
}

static void paint_app_running(Ui *ui, Canvas *cv, float dy)
{
    char title[96];
    ks_format(title, sizeof title, "%s is running", ui->app_name);
    paint_status_header(ui, cv, dy, BADGE_WARN, title);
    char body[512];
    if (ui->app_unresponsive)
        ks_format(body, sizeof body,
                  "%s didn't close when asked. You can end it now; anything it was in the middle of will stop. "
                  "Your chats, macros and settings are saved on disk and won't be affected.",
                  ui->app_name);
    else if (ui->uninstall_mode)
        ks_format(body, sizeof body, "%s has to close before it can be removed.", ui->app_name);
    else
        ks_format(body, sizeof body,
                  "%s has to close before it can be updated. Your chats, macros and settings are kept.",
                  ui->app_name);
    draw_paragraph(cv, FONT_BODY, body, S(ui, PAD), S(ui, 104.0f) + dy, S(ui, UI_WIDTH - 2 * PAD), C_TEXT_2, 1.3f, false);
}

static void paint_progress(Ui *ui, Canvas *cv, float dy)
{
    paint_header(ui, cv, dy, 44.0f, ui->progress_title, FONT_TITLE,
                 ui->uninstall_mode ? "This only takes a moment." : "This only takes a few seconds.");

    RectF bar = px_rect(ui, PAD, 204.0f, UI_WIDTH - 2 * PAD, 6.0f);
    bar.y += dy;
    float status_base = bar.y - S(ui, 14.0f);
    draw_text(cv, FONT_BODY, ui->progress_status, bar.x, status_base, C_TEXT);
    if (ui->progress >= 0.0f) {
        char pct[16];
        int v = (int)(KS_CLAMP(ui->progress_shown, 0.0f, 1.0f) * 100.0f + 0.5f);
        ks_format(pct, sizeof pct, "%d%%", v);
        draw_text_right(cv, FONT_BODY_STRONG, pct, bar.x + bar.w, status_base, C_TEXT);
    }
    draw_progress_bar(ui, cv, bar);
    if (ui->progress_detail[0]) {
        char shown[512];
        ellipsize_middle(FONT_CAPTION, ui->progress_detail, bar.w, shown, sizeof shown);
        draw_text(cv, FONT_CAPTION, shown, bar.x, bar.y + bar.h + S(ui, 22.0f), C_TEXT_FAINT);
    }
}

static void paint_done(Ui *ui, Canvas *cv, float dy, bool removed)
{
    float t = (float)(ui->now - ui->page_started);
    float appear = KS_CLAMP(t / 0.9f, 0.0f, 1.0f);
    float cx = S(ui, UI_WIDTH * 0.5f), cy = S(ui, 116.0f) + dy;
    draw_badge(ui, cv, cx, cy, S(ui, 30.0f), BADGE_CHECK, appear, 3.2f);

    char title[96], body[256];
    if (removed) {
        ks_format(title, sizeof title, "%s was removed", ui->app_name);
        if (ui->data_deleted)
            ks_strlcpy(body, "Your chats, macros and settings were deleted too.", sizeof body);
        else
            ks_strlcpy(body, "Your chats, macros and settings are still here, in case you come back.", sizeof body);
    } else {
        ks_format(title, sizeof title, "%s is ready", ui->app_name);
        ks_format(body, sizeof body, "You'll find it in the Start menu whenever you need it.");
    }
    draw_text_centered(cv, FONT_TITLE, title, cx, S(ui, 190.0f) + dy, C_TEXT);
    draw_paragraph(cv, FONT_BODY, body, S(ui, PAD + 20.0f), S(ui, 204.0f) + dy, S(ui, UI_WIDTH - 2 * PAD - 40.0f), C_TEXT_2, 1.3f, true);
}

static void paint_canceled(Ui *ui, Canvas *cv, float dy)
{
    bool update = ui->existing_version[0] != 0;
    paint_status_header(ui, cv, dy, BADGE_INFO, update ? "Update canceled" : "Installation canceled");
    draw_paragraph(cv, FONT_BODY,
                   update ? "Nothing was changed. The version you had is still installed."
                          : "Nothing was changed. You can run setup again any time.",
                   S(ui, PAD), S(ui, 104.0f) + dy, S(ui, UI_WIDTH - 2 * PAD), C_TEXT_2, 1.3f, false);
}

static void paint_error(Ui *ui, Canvas *cv, float dy)
{
    paint_status_header(ui, cv, dy, BADGE_ERROR, ui->error_title[0] ? ui->error_title : "Something went wrong");
    draw_paragraph(cv, FONT_BODY, ui->error_text, S(ui, PAD), S(ui, 104.0f) + dy, S(ui, UI_WIDTH - 2 * PAD), C_TEXT_2, 1.3f, false);
}

static void paint_uninstall(Ui *ui, Canvas *cv, float dy)
{
    char title[96], subtitle[64];
    ks_format(title, sizeof title, "Uninstall %s", ui->app_name);
    ks_format(subtitle, sizeof subtitle, "Version %s", ui->app_version);
    paint_header(ui, cv, dy, 52.0f, title, FONT_DISPLAY, subtitle);

    char body[256];
    ks_format(body, sizeof body, "This removes %s and its shortcuts from this PC.", ui->app_name);
    draw_paragraph(cv, FONT_BODY, body, S(ui, PAD), S(ui, W_DESC_TOP) + dy, S(ui, UI_WIDTH - 2 * PAD), C_TEXT_2, 1.3f, false);

    draw_checkbox(ui, cv, W_OPT_DELETE_DATA, LBL_DELETE, ui->opt_delete_data, dy);
    if (ui->data_dir[0]) {
        char shown[1024];
        float x = S(ui, PAD + 18.0f + 11.0f);
        ellipsize_middle(FONT_CAPTION, ui->data_dir, S(ui, UI_WIDTH - PAD) - x, shown, sizeof shown);
        draw_text(cv, FONT_CAPTION, shown, x, S(ui, 192.0f) + dy, C_TEXT_FAINT);
    }
}

static void paint_page(Ui *ui, Canvas *cv, PageId page, float dy)
{
    if (ui->background.px) {
        Canvas bg;
        gfx_canvas_init(&bg, ui->background.px, ui->background.w, ui->background.h, ui->background.w);
        gfx_copy(cv, &bg);
    } else {
        gfx_clear(cv, C_BG);
    }

    switch (page) {
    case PAGE_WELCOME:
        paint_welcome(ui, cv, dy);
        draw_link(ui, cv, W_LINK, LBL_LICENSE_LINK, FONT_SMALL, 0);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_PRIMARY, 0);
        break;
    case PAGE_LICENSE:
        paint_license(ui, cv, dy);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_PRIMARY, 0);
        break;
    case PAGE_APP_RUNNING:
        paint_app_running(ui, cv, dy);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), ui->app_unresponsive ? BTN_DANGER : BTN_PRIMARY, 0);
        break;
    case PAGE_PROGRESS:
        paint_progress(ui, cv, dy);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        break;
    case PAGE_DONE:
        paint_done(ui, cv, dy, false);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_PRIMARY, 0);
        break;
    case PAGE_CANCELED:
        paint_canceled(ui, cv, dy);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_SECONDARY, 0);
        break;
    case PAGE_ERROR:
        paint_error(ui, cv, dy);
        draw_link(ui, cv, W_LINK, LBL_OPEN_LOG, FONT_SMALL, 0);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_PRIMARY, 0);
        break;
    case PAGE_UNINSTALL:
        paint_uninstall(ui, cv, dy);
        draw_button(ui, cv, W_SECONDARY, secondary_label(ui, page), BTN_SECONDARY, 0);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_DANGER, 0);
        break;
    case PAGE_REMOVED:
        paint_done(ui, cv, dy, true);
        draw_button(ui, cv, W_PRIMARY, primary_label(ui, page), BTN_SECONDARY, 0);
        break;
    default:
        break;
    }
}

/* ── Public API ─────────────────────────────────────────────────────── */

void ui_init(Ui *ui, bool uninstall_mode)
{
    memset(ui, 0, sizeof(*ui));
    ui->uninstall_mode = uninstall_mode;
    ui->opt_desktop = true;
    ui->progress = -1.0f;
    ui->scale = 1.0f;
    ks_strlcpy(ui->app_name, "Kestrel", sizeof ui->app_name);
    ui->page = uninstall_mode ? PAGE_UNINSTALL : PAGE_WELCOME;
    ui->prev_page = PAGE_NONE;
    ui->check_t[W_OPT_DESKTOP] = 1.0f;
}

void ui_free(Ui *ui)
{
    for (int i = 0; i < KS_ARRAY_LEN(ui->logos); i++)
        image_free(&ui->logos[i].img);
    image_free(&ui->background);
    ks_free(ui->license_lines);
    ui->license_lines = NULL;
}

void ui_set_scale(Ui *ui, float scale, int width_px, int height_px)
{
    ui->scale = scale;
    ui->width = width_px;
    ui->height = height_px;
    text_set_scale(scale);
    for (int i = 0; i < KS_ARRAY_LEN(ui->logos); i++) {
        image_free(&ui->logos[i].img);
        ui->logos[i].size = 0;
    }
    ks_free(ui->license_lines);
    ui->license_lines = NULL;
    ui->license_wrap_width = 0;
    ui->license_scroll = ui->license_scroll_target = 0;
    build_background(ui);
    compute_layout(ui, ui->page, &ui->layout);
}

void ui_set_page(Ui *ui, PageId page)
{
    if (page == ui->page)
        return;
    ui->prev_page = ui->page;
    ui->page = page;
    ui->page_started = ui->now;
    ui->hot = ui->pressed = W_NONE;
    ui->focused = W_NONE;
    for (int i = 0; i < W_COUNT; i++)
        ui->hover_t[i] = 0.0f;
    if (page == PAGE_PROGRESS)
        ui->progress_shown = KS_MAX(ui->progress, 0.0f);
    if (page == PAGE_LICENSE)
        ui->license_scroll = ui->license_scroll_target = 0.0f;
    compute_layout(ui, page, &ui->layout);
    /* Re-evaluate what is under the pointer on the new page. */
    ui_mouse_move(ui, ui->mouse_x, ui->mouse_y);
}

bool ui_tick(Ui *ui, double now)
{
    float dt = (float)(now - ui->now);
    if (dt < 0.0f || dt > 0.25f)
        dt = 0.016f;
    ui->now = now;

    bool moving = false;
    for (int i = 0; i < W_COUNT; i++) {
        bool live = ui->layout.w[i].enabled || i == W_SCROLL_THUMB;
        float target = (ui->hot == (WidgetId)i && live) ? 1.0f : 0.0f;
        float v = approach(ui->hover_t[i], target, dt, 1.0f / 0.12f);
        if (v != ui->hover_t[i])
            moving = true;
        ui->hover_t[i] = v;
    }
    bool checks[W_COUNT] = { 0 };
    checks[W_OPT_DESKTOP] = ui->opt_desktop;
    checks[W_OPT_STARTUP] = ui->opt_startup;
    checks[W_OPT_DELETE_DATA] = ui->opt_delete_data;
    for (int i = 0; i < W_COUNT; i++) {
        float v = approach(ui->check_t[i], checks[i] ? 1.0f : 0.0f, dt, 1.0f / 0.16f);
        if (v != ui->check_t[i])
            moving = true;
        ui->check_t[i] = v;
    }

    /* Keep the layout current: labels (and so button widths) change. */
    compute_layout(ui, ui->page, &ui->layout);

    if (ui->page == PAGE_PROGRESS) {
        if (ui->progress >= 0.0f) {
            /* Ease towards the real figure, never backwards. */
            float target = KS_MAX(ui->progress, ui->progress_shown);
            /* Once the work is done, fill the rest of the bar quickly. */
            float k = KS_MIN(1.0f, dt * (ui->progress >= 1.0f ? 14.0f : 7.0f));
            ui->progress_shown += (target - ui->progress_shown) * k;
            if (target - ui->progress_shown < 0.0005f)
                ui->progress_shown = target;
        }
        moving = true;   /* bar animation */
    }

    if (ui->page == PAGE_LICENSE) {
        float diff = ui->license_scroll_target - ui->license_scroll;
        if (ks_fabsf(diff) > 0.5f) {
            ui->license_scroll += diff * KS_MIN(1.0f, dt * 16.0f);
            moving = true;
        } else {
            ui->license_scroll = ui->license_scroll_target;
        }
    }

    double since = now - ui->page_started;
    if (since < TRANSITION + 0.02)
        moving = true;
    if ((ui->page == PAGE_DONE || ui->page == PAGE_REMOVED) && since < 1.0)
        moving = true;
    if (ui->page == PAGE_APP_RUNNING && ui->app_closing && !ui->app_unresponsive)
        moving = true;
    return moving;
}

void ui_render(Ui *ui, Canvas *main, Canvas *scratch)
{
    double since = ui->now - ui->page_started;
    float t = (float)(since / TRANSITION);
    if (ui->prev_page != PAGE_NONE && t < 1.0f && scratch && ui->background.px) {
        /* Fade the old page out to the bare backdrop, then the new one in
           from it, rising slightly. Cleaner than overlapping the two. */
        float alpha;
        if (t < TRANSITION_OUT) {
            float k = t / TRANSITION_OUT;
            Layout saved = ui->layout;
            compute_layout(ui, ui->prev_page, &ui->layout);
            paint_page(ui, scratch, ui->prev_page, -S(ui, 4.0f) * k);
            ui->layout = saved;
            alpha = 1.0f - ks_ease_in_out_cubic(k);
        } else {
            float k = (t - TRANSITION_OUT) / (1.0f - TRANSITION_OUT);
            float e = ks_ease_out_cubic(k);
            paint_page(ui, scratch, ui->page, S(ui, 10.0f) * (1.0f - e));
            alpha = e;
        }
        Canvas bg;
        gfx_canvas_init(&bg, ui->background.px, ui->background.w, ui->background.h, ui->background.w);
        gfx_copy(main, &bg);
        gfx_crossfade(main, scratch, alpha);
    } else {
        paint_page(ui, main, ui->page, 0.0f);
    }
}

/* ── Input ──────────────────────────────────────────────────────────── */

static WidgetId hit_test(Ui *ui, float x, float y)
{
    static const WidgetId order[] = { W_PRIMARY, W_SECONDARY, W_LINK, W_CHANGE_DIR, W_OPT_DESKTOP,
                                      W_OPT_STARTUP, W_OPT_DELETE_DATA, W_BACK };
    for (int i = 0; i < KS_ARRAY_LEN(order); i++) {
        WidgetRect *wr = &ui->layout.w[order[i]];
        if (wr->visible && wr->enabled && rect_contains(wr->r, x, y))
            return order[i];
    }
    if (ui->page == PAGE_LICENSE) {
        RectF track, thumb;
        if (license_thumb(ui, &track, &thumb)) {
            RectF grab = { track.x - S(ui, 6.0f), track.y, track.w + S(ui, 12.0f), track.h };
            if (rect_contains(grab, x, y))
                return W_SCROLL_THUMB;
        }
    }
    return W_NONE;
}

static bool in_transition(Ui *ui)
{
    return ui->now - ui->page_started < TRANSITION * 0.6;
}

static void activate(Ui *ui, WidgetId id)
{
    if (id == W_NONE || !ui->layout.w[id].visible || !ui->layout.w[id].enabled)
        return;
    PageId page = ui->page;
    switch (id) {
    case W_OPT_DESKTOP:
        ui->opt_desktop = !ui->opt_desktop;
        return;
    case W_OPT_STARTUP:
        ui->opt_startup = !ui->opt_startup;
        return;
    case W_OPT_DELETE_DATA:
        ui->opt_delete_data = !ui->opt_delete_data;
        return;
    case W_BACK:
        ui_set_page(ui, PAGE_WELCOME);
        return;
    case W_LINK:
        if (page == PAGE_WELCOME) {
            ui_set_page(ui, PAGE_LICENSE);
            return;
        }
        break;
    case W_SECONDARY:
        if (page == PAGE_LICENSE) {
            ui_set_page(ui, PAGE_WELCOME);
            return;
        }
        break;
    default:
        break;
    }
    host_ui_action(ui, page, id);
}

void ui_mouse_move(Ui *ui, float x, float y)
{
    ui->mouse_x = x;
    ui->mouse_y = y;
    if (ui->license_dragging) {
        RectF track, thumb;
        if (license_thumb(ui, &track, &thumb)) {
            float usable = track.h - thumb.h;
            float pos = KS_CLAMP(y - ui->license_drag_anchor - track.y, 0.0f, usable);
            float v = usable > 0.0f ? pos / usable * license_max_scroll(ui) : 0.0f;
            ui->license_scroll = ui->license_scroll_target = v;
        }
        return;
    }
    WidgetId hot = hit_test(ui, x, y);
    ui->hot = hot;
    host_ui_set_cursor(ui, (hot != W_NONE && hot != W_SCROLL_THUMB) ? CURSOR_HAND : CURSOR_ARROW);
}

void ui_mouse_down(Ui *ui, float x, float y)
{
    ui_mouse_move(ui, x, y);
    ui->focus_visible = false;
    if (in_transition(ui))
        return;
    ui->pressed = ui->hot;
    if (ui->hot == W_SCROLL_THUMB) {
        RectF track, thumb;
        if (license_thumb(ui, &track, &thumb)) {
            if (y < thumb.y || y > thumb.y + thumb.h) {
                /* Jump so the thumb centres on the click, then drag. */
                ui->license_drag_anchor = thumb.h * 0.5f;
                ui->license_dragging = true;
                ui_mouse_move(ui, x, y);
            } else {
                ui->license_drag_anchor = y - thumb.y;
                ui->license_dragging = true;
            }
        }
    }
}

void ui_mouse_up(Ui *ui, float x, float y)
{
    if (ui->license_dragging) {
        ui->license_dragging = false;
        ui->pressed = W_NONE;
        ui_mouse_move(ui, x, y);
        return;
    }
    WidgetId was = ui->pressed;
    ui->pressed = W_NONE;
    ui_mouse_move(ui, x, y);
    if (was != W_NONE && was == ui->hot && !in_transition(ui))
        activate(ui, was);
}

void ui_mouse_leave(Ui *ui)
{
    if (!ui->license_dragging)
        ui->hot = W_NONE;
}

void ui_mouse_wheel(Ui *ui, float notches)
{
    if (ui->page == PAGE_LICENSE)
        license_scroll_by(ui, -notches * S(ui, 64.0f));
}

void ui_key_down(Ui *ui, KeyCode key, bool shift)
{
    static const WidgetId focus_order[] = { W_BACK, W_CHANGE_DIR, W_OPT_DESKTOP, W_OPT_STARTUP,
                                            W_OPT_DELETE_DATA, W_LINK, W_SECONDARY, W_PRIMARY };
    int n = KS_ARRAY_LEN(focus_order);

    if (in_transition(ui))
        return;

    switch (key) {
    case KEY_TAB: {
        ui->focus_visible = true;
        int cur = -1;
        for (int i = 0; i < n; i++)
            if (focus_order[i] == ui->focused)
                cur = i;
        for (int step = 1; step <= n; step++) {
            int idx = shift ? (cur - step + n * 2) % n : (cur + step + n) % n;
            if (cur < 0)
                idx = shift ? n - step : step - 1;
            WidgetRect *wr = &ui->layout.w[focus_order[idx]];
            if (wr->visible && wr->enabled) {
                ui->focused = focus_order[idx];
                return;
            }
        }
        return;
    }
    case KEY_SPACE:
        if (ui->focused != W_NONE)
            activate(ui, ui->focused);
        return;
    case KEY_ENTER:
        if (ui->focused != W_NONE)
            activate(ui, ui->focused);
        else if (ui->layout.w[W_PRIMARY].visible && ui->layout.w[W_PRIMARY].enabled)
            activate(ui, W_PRIMARY);
        return;
    case KEY_ESCAPE:
        if (ui->page == PAGE_LICENSE) {
            ui_set_page(ui, PAGE_WELCOME);
        } else if (ui->layout.w[W_SECONDARY].visible) {
            if (ui->layout.w[W_SECONDARY].enabled)
                activate(ui, W_SECONDARY);
        } else if (ui->page == PAGE_CANCELED || ui->page == PAGE_REMOVED) {
            activate(ui, W_PRIMARY);
        }
        return;
    case KEY_UP:
    case KEY_DOWN:
    case KEY_PAGE_UP:
    case KEY_PAGE_DOWN:
    case KEY_HOME:
    case KEY_END:
        if (ui->page == PAGE_LICENSE) {
            float page = ui->layout.license_view.h * 0.85f;
            float line = S(ui, 40.0f);
            if (key == KEY_UP)
                license_scroll_by(ui, -line);
            else if (key == KEY_DOWN)
                license_scroll_by(ui, line);
            else if (key == KEY_PAGE_UP)
                license_scroll_by(ui, -page);
            else if (key == KEY_PAGE_DOWN)
                license_scroll_by(ui, page);
            else if (key == KEY_HOME)
                ui->license_scroll_target = 0.0f;
            else
                ui->license_scroll_target = license_max_scroll(ui);
        }
        return;
    default:
        return;
    }
}
