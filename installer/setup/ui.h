/*
 * Kestrel Setup - native installer for Kestrel.
 * Copyright (C) 2026 the Kestrel authors
 * Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
 *
 * ui.h - the installer's screens: layout, drawing, animation and input.
 *
 * Platform-neutral. It draws into a Canvas and is told about input and
 * time; what the buttons actually do is up to the host (main_win32.c for
 * the real installer, preview.c for the Linux screen previews), through
 * host_ui_action().
 */

#ifndef KS_UI_H
#define KS_UI_H

#include "gfx.h"
#include "text.h"

/* Client area size in DIPs. */
#define UI_WIDTH 560
#define UI_HEIGHT 424

typedef enum {
    PAGE_NONE,
    PAGE_WELCOME,       /* install: location, options, accept */
    PAGE_LICENSE,       /* install: the licence text */
    PAGE_APP_RUNNING,   /* Kestrel has to close first */
    PAGE_PROGRESS,
    PAGE_DONE,          /* installed */
    PAGE_CANCELED,      /* install stopped, nothing changed */
    PAGE_ERROR,
    PAGE_UNINSTALL,     /* uninstall: confirm */
    PAGE_REMOVED,       /* uninstalled */
    PAGE_COUNT
} PageId;

typedef enum {
    W_NONE = 0,
    W_PRIMARY,          /* the page's main action */
    W_SECONDARY,        /* Cancel / Close / Back */
    W_LINK,             /* footer link: licence, open log */
    W_CHANGE_DIR,
    W_OPT_DESKTOP,
    W_OPT_STARTUP,
    W_OPT_DELETE_DATA,
    W_BACK,             /* licence page back arrow */
    W_SCROLL_THUMB,
    W_COUNT
} WidgetId;

typedef enum { CURSOR_ARROW, CURSOR_HAND } CursorKind;

typedef enum {
    KEY_OTHER,
    KEY_TAB,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_SPACE,
    KEY_UP,
    KEY_DOWN,
    KEY_PAGE_UP,
    KEY_PAGE_DOWN,
    KEY_HOME,
    KEY_END,
} KeyCode;

typedef struct {
    float x, y, w, h;
} RectF;

typedef struct {
    RectF r;
    bool visible;
    bool enabled;
} WidgetRect;

typedef struct {
    WidgetRect w[W_COUNT];
    RectF license_view;       /* licence page text viewport */
    RectF field;              /* welcome page install location field */
    float label_baseline;     /* welcome page "Install location" */
} Layout;

struct LicenseLine;

typedef struct {
    int size;                 /* pixel size of this cached copy */
    Image img;
} LogoCache;

typedef struct Ui {
    /* ── What is being installed ───────────────────────────────────── */
    bool uninstall_mode;
    char app_name[64];
    char app_version[32];
    char install_dir[1024];       /* shown on the welcome page */
    char data_dir[1024];          /* shown under "also delete my data" */
    const char *license;          /* licence markup, see ui.c; host-owned */
    Image logo;                   /* premultiplied master image; host-owned */

    /* ── Choices ───────────────────────────────────────────────────── */
    bool opt_desktop;
    bool opt_startup;
    bool opt_delete_data;

    /* ── Live state set by the host ────────────────────────────────── */
    char progress_title[96];
    char progress_status[128];
    char progress_detail[512];
    float progress;               /* 0..1; negative for "working, no figure yet" */
    bool progress_cancelable;
    char error_title[128];
    char error_text[1024];
    bool app_closing;             /* asked Kestrel to quit, waiting */
    bool app_unresponsive;        /* it did not; offer to end it */
    bool data_deleted;            /* for the "removed" page wording */

    /* ── View state (ui.c) ─────────────────────────────────────────── */
    float scale;
    int width, height;            /* canvas size in pixels */
    PageId page, prev_page;
    double page_started;
    double now;
    float progress_shown;
    float license_scroll, license_scroll_target, license_content_h;
    bool license_dragging;
    float license_drag_anchor;
    Layout layout;
    WidgetId hot, pressed, focused;
    bool focus_visible;
    float hover_t[W_COUNT];
    float check_t[W_COUNT];
    float mouse_x, mouse_y;
    char existing_version[32];    /* non-empty when updating an install */
    struct LicenseLine *license_lines;
    int license_line_count;
    float license_wrap_width;
    LogoCache logos[4];
    Image background;             /* cached backdrop at the current size */
    void *host;                   /* for the host's own use */
} Ui;

void ui_init(Ui *ui, bool uninstall_mode);
void ui_free(Ui *ui);

/* Call on creation and whenever the DPI or size changes. */
void ui_set_scale(Ui *ui, float scale, int width_px, int height_px);

void ui_set_page(Ui *ui, PageId page);

/* Advance animations to `now` (seconds). Returns true while anything is
   still moving, i.e. while the host should keep drawing frames. */
bool ui_tick(Ui *ui, double now);

/* scratch must be the same size as main; used for page transitions. */
void ui_render(Ui *ui, Canvas *main, Canvas *scratch);

/* Input, in pixels. */
void ui_mouse_move(Ui *ui, float x, float y);
void ui_mouse_down(Ui *ui, float x, float y);
void ui_mouse_up(Ui *ui, float x, float y);
void ui_mouse_leave(Ui *ui);
void ui_mouse_wheel(Ui *ui, float notches);
void ui_key_down(Ui *ui, KeyCode key, bool shift);

/* ── Implemented by the host ─────────────────────────────────────────── */

/* A button, link or option the UI does not handle itself was activated. */
void host_ui_action(Ui *ui, PageId page, WidgetId widget);
void host_ui_set_cursor(Ui *ui, CursorKind cursor);

#endif
