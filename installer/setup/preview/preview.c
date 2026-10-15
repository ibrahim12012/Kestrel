/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * preview.c - renders every screen of the installer to PNG files, using
 * the same ui.c and gfx.c as the real program, with FreeType standing in
 * for GDI text. For checking the design (and changes to it) without a
 * Windows machine. Linux only.
 *
 *   make -C installer/setup/preview
 *   KS_FONT_REGULAR=... KS_FONT_SEMIBOLD=... ./preview out_dir license.txt
 */

#include "../assets.h"
#include "../ui.h"

#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void host_ui_action(Ui *ui, PageId page, WidgetId widget)
{
    (void)ui;
    printf("  action: page %d widget %d\n", page, widget);
}

void host_ui_set_cursor(Ui *ui, CursorKind cursor)
{
    (void)ui;
    (void)cursor;
}

static void write_png(const char *path, const Canvas *cv)
{
    FILE *f = fopen(path, "wb");
    if (!f) {
        perror(path);
        exit(1);
    }
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    png_infop info = png_create_info_struct(png);
    png_init_io(png, f);
    png_set_IHDR(png, info, cv->w, cv->h, 8, PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT,
                 PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);
    unsigned char *row = malloc((size_t)cv->w * 3);
    for (int y = 0; y < cv->h; y++) {
        const uint32_t *src = cv->px + (size_t)y * cv->stride;
        for (int x = 0; x < cv->w; x++) {
            row[x * 3 + 0] = (src[x] >> 16) & 0xFF;
            row[x * 3 + 1] = (src[x] >> 8) & 0xFF;
            row[x * 3 + 2] = src[x] & 0xFF;
        }
        png_write_row(png, row);
    }
    png_write_end(png, NULL);
    png_destroy_write_struct(&png, &info);
    free(row);
    fclose(f);
}

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) {
        fclose(f);
        free(buf);
        return NULL;
    }
    buf[n] = 0;
    fclose(f);
    return buf;
}

typedef struct {
    Canvas main, scratch;
    uint32_t *px_main, *px_scratch;
} Frame;

static void frame_init(Frame *fr, Ui *ui, float scale)
{
    int w = (int)(UI_WIDTH * scale + 0.5f), h = (int)(UI_HEIGHT * scale + 0.5f);
    fr->px_main = calloc((size_t)w * h, 4);
    fr->px_scratch = calloc((size_t)w * h, 4);
    gfx_canvas_init(&fr->main, fr->px_main, w, h, w);
    gfx_canvas_init(&fr->scratch, fr->px_scratch, w, h, w);
    ui_set_scale(ui, scale, w, h);
}

static void frame_free(Frame *fr)
{
    free(fr->px_main);
    free(fr->px_scratch);
}

static double g_clock = 100.0;

/* Go to a page and let every animation settle. */
static void settle(Ui *ui, PageId page)
{
    ui_set_page(ui, page);
    for (int i = 0; i < 180; i++) {
        g_clock += 1.0 / 60.0;
        ui_tick(ui, g_clock);
    }
}

static void shot(Ui *ui, Frame *fr, const char *dir, const char *name, float scale)
{
    char path[512];
    snprintf(path, sizeof path, "%s/%s@%gx.png", dir, name, scale);
    ui_render(ui, &fr->main, &fr->scratch);
    write_png(path, &fr->main);
    printf("wrote %s\n", path);
}

static void hover(Ui *ui, WidgetId id)
{
    WidgetRect *wr = &ui->layout.w[id];
    ui_mouse_move(ui, wr->r.x + wr->r.w * 0.5f, wr->r.y + wr->r.h * 0.5f);
    for (int i = 0; i < 20; i++) {
        g_clock += 1.0 / 60.0;
        ui_tick(ui, g_clock);
    }
}

static void base_ui(Ui *ui, bool uninstall, const char *license, const Image *logo)
{
    ui_init(ui, uninstall);
    ks_strlcpy(ui->app_version, "0.2.1", sizeof ui->app_version);
    ks_strlcpy(ui->install_dir, "C:\\Users\\Chef\\AppData\\Local\\Programs\\Kestrel", sizeof ui->install_dir);
    ks_strlcpy(ui->data_dir, "C:\\Users\\Chef\\AppData\\Local\\Kestrel", sizeof ui->data_dir);
    ui->license = license;
    ui->logo = *logo;
    ui->now = g_clock;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s out_dir license.txt [scale...]\n", argv[0]);
        return 2;
    }
    const char *out = argv[1];
    char *license = read_file(argv[2]);
    if (!license) {
        perror(argv[2]);
        return 1;
    }
    ks_crc_init();
    Image logo;
    if (!assets_load_logo(&logo)) {
        fprintf(stderr, "logo decode failed\n");
        return 1;
    }

    float scales[8] = { 1.0f };
    int nscales = 1;
    if (argc > 3) {
        nscales = 0;
        for (int i = 3; i < argc && nscales < 8; i++)
            scales[nscales++] = (float)atof(argv[i]);
    }

    for (int si = 0; si < nscales; si++) {
        float sc = scales[si];
        Ui ui;
        Frame fr;

        /* Install flow */
        base_ui(&ui, false, license, &logo);
        frame_init(&fr, &ui, sc);
        settle(&ui, PAGE_WELCOME);
        g_clock += 1.0;
        ui_tick(&ui, g_clock);
        shot(&ui, &fr, out, "01-welcome", sc);

        hover(&ui, W_PRIMARY);
        shot(&ui, &fr, out, "02-welcome-hover-install", sc);

        ui_mouse_leave(&ui);
        ui_key_down(&ui, KEY_TAB, false);
        ui_key_down(&ui, KEY_TAB, false);
        ui_key_down(&ui, KEY_TAB, false);
        ui_key_down(&ui, KEY_SPACE, false);   /* toggles "start when I sign in" */
        for (int i = 0; i < 30; i++) {
            g_clock += 1.0 / 60.0;
            ui_tick(&ui, g_clock);
        }
        shot(&ui, &fr, out, "03-welcome-keyboard", sc);

        settle(&ui, PAGE_LICENSE);
        shot(&ui, &fr, out, "04-license", sc);
        ui_mouse_wheel(&ui, -6.0f);
        settle(&ui, PAGE_LICENSE);
        for (int i = 0; i < 60; i++) {
            g_clock += 1.0 / 60.0;
            ui_tick(&ui, g_clock);
        }
        shot(&ui, &fr, out, "05-license-scrolled", sc);

        ui.app_closing = false;
        settle(&ui, PAGE_APP_RUNNING);
        shot(&ui, &fr, out, "06-app-running", sc);
        ui.app_unresponsive = true;
        settle(&ui, PAGE_APP_RUNNING);
        g_clock += 0.1;
        ui_tick(&ui, g_clock);
        shot(&ui, &fr, out, "07-app-unresponsive", sc);
        ui.app_unresponsive = false;

        ks_strlcpy(ui.progress_title, "Installing Kestrel", sizeof ui.progress_title);
        ks_strlcpy(ui.progress_status, "Preparing", sizeof ui.progress_status);
        ui.progress = -1.0f;
        ui.progress_cancelable = true;
        settle(&ui, PAGE_PROGRESS);
        shot(&ui, &fr, out, "08-progress-start", sc);
        ks_strlcpy(ui.progress_status, "Copying files", sizeof ui.progress_status);
        ks_strlcpy(ui.progress_detail, "_internal\\PyQt6\\Qt6\\bin\\Qt6Gui.dll", sizeof ui.progress_detail);
        ui.progress = 0.42f;
        settle(&ui, PAGE_PROGRESS);
        for (int i = 0; i < 120; i++) {
            g_clock += 1.0 / 60.0;
            ui_tick(&ui, g_clock);
        }
        shot(&ui, &fr, out, "09-progress", sc);

        settle(&ui, PAGE_DONE);
        shot(&ui, &fr, out, "10-done", sc);

        settle(&ui, PAGE_CANCELED);
        shot(&ui, &fr, out, "11-canceled", sc);

        ks_strlcpy(ui.error_title, "Couldn't install Kestrel", sizeof ui.error_title);
        ks_strlcpy(ui.error_text,
                   "There isn't enough free space on drive C: (Kestrel needs 412 MB, 180 MB is free). "
                   "Free up some space, or choose another location, and try again.",
                   sizeof ui.error_text);
        settle(&ui, PAGE_ERROR);
        shot(&ui, &fr, out, "12-error", sc);

        /* Transition frame, part way from welcome to progress. */
        settle(&ui, PAGE_WELCOME);
        ui_set_page(&ui, PAGE_PROGRESS);
        g_clock += 0.09;
        ui_tick(&ui, g_clock);
        shot(&ui, &fr, out, "13-transition", sc);

        /* Update mode */
        ks_strlcpy(ui.existing_version, "0.2.0", sizeof ui.existing_version);
        settle(&ui, PAGE_WELCOME);
        g_clock += 1.0;
        ui_tick(&ui, g_clock);
        shot(&ui, &fr, out, "14-welcome-update", sc);
        ui_free(&ui);
        frame_free(&fr);

        /* Uninstall flow */
        base_ui(&ui, true, license, &logo);
        frame_init(&fr, &ui, sc);
        settle(&ui, PAGE_UNINSTALL);
        g_clock += 1.0;
        ui_tick(&ui, g_clock);
        shot(&ui, &fr, out, "20-uninstall", sc);
        ui.opt_delete_data = true;
        settle(&ui, PAGE_UNINSTALL);
        g_clock += 1.0;
        ui_tick(&ui, g_clock);
        hover(&ui, W_PRIMARY);
        shot(&ui, &fr, out, "21-uninstall-delete-data", sc);
        ks_strlcpy(ui.progress_title, "Removing Kestrel", sizeof ui.progress_title);
        ks_strlcpy(ui.progress_status, "Removing files", sizeof ui.progress_status);
        ui.progress = 0.66f;
        ui.progress_cancelable = false;
        settle(&ui, PAGE_PROGRESS);
        shot(&ui, &fr, out, "22-uninstall-progress", sc);
        ui.data_deleted = false;
        settle(&ui, PAGE_REMOVED);
        shot(&ui, &fr, out, "23-removed", sc);
        ui_free(&ui);
        frame_free(&fr);
    }
    text_shutdown();
    image_free(&logo);
    free(license);
    return 0;
}
