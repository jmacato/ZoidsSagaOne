#include "text_engine.h"

typedef struct {
    s16 pen;
    s16 previous;
    u8 cursor_x;
    u8 cursor_y;
    u8 cleared_to;
    u8 valid;
} TextPen;

typedef struct {
    TextProfile profile;
    TextDecoder decoder;
    TextLayoutState state;
    TextLayoutCursor cursor;
    TextLayoutResult result;
    TextEvent event;
    u8 player[18];
} LayoutWork;

typedef struct {
    s16 left;
    s16 right;
    u8 alignment;
} Field;

typedef struct {
    int metric;
    u16 code;
    u8 left;
    u8 top;
    u8 width;
    u8 height;
    s8 bearing_x;
    s8 bearing_y;
    u8 advance;
} Shape;

/* The pen and message log sit at the end of the native tile slot array, outside cleanup. */
#define PEN_OFFSET (sizeof(((TextWindow *)0)->glyph_slots) - sizeof(TextPen))
#define LOG_OFFSET (PEN_OFFSET - sizeof(MessageLog))
#define CELL_CAPACITY LOG_OFFSET
#define BASELINE 14
/* Unused IWRAM between the system stack top and the IRQ stack, after the core scratch. */
#define WORK ((LayoutWork *)0x03007C90)

typedef char LayoutWorkSizeCheck[sizeof(LayoutWork) <= 0x100 ? 1 : -1];

static void window_command(u8 window, u8 command)
{
    GAME_CALL(0x080416C9, window, command, 0, 0);
}

static TextPen *pen_for(TextWindow *window)
{
    return (TextPen *)(window->glyph_slots + PEN_OFFSET);
}

MessageLog *message_log(u8 id)
{
    return (MessageLog *)(text_windows[id].glyph_slots + LOG_OFFSET);
}

/* Dialogue windows keep one pixel clear on both edges. */
static int inset(TextWindow *window)
{
    return (window->flags & 1) && !(window->style & 1);
}

static TextPen *prepare(TextWindow *window)
{
    TextPen *pen = pen_for(window);
    if (pen->valid != 0xA1 || pen->cursor_x != window->cursor_x || pen->cursor_y != window->cursor_y) {
        pen->pen = window->cursor_x * 8;
        pen->previous = -1;
        pen->cleared_to = window->cursor_x;
        pen->valid = 0xA1;
    }
    return pen;
}

static void finish(TextWindow *window, TextPen *pen)
{
    pen->valid = 0xA1;
    window->cursor_x = (pen->pen + 7) / 8;
    pen->cursor_x = window->cursor_x;
    pen->cursor_y = window->cursor_y;
}

static void shape_for_metric(int metric, u16 code, Shape *shape)
{
    shape->metric = metric;
    if (metric < 0) {
        shape->code = code;
        shape->left = shape->top = 0;
        shape->width = 8;
        shape->height = 16;
        shape->bearing_x = 0;
        shape->bearing_y = -BASELINE;
        shape->advance = 8;
        return;
    }
    const TextMetric *data = &text_font.metrics[metric];
    shape->code = data->code;
    shape->left = text_glyph_origins[metric * 2];
    shape->top = text_glyph_origins[metric * 2 + 1];
    shape->width = data->width;
    shape->height = data->height;
    shape->bearing_x = data->bearing_x;
    shape->bearing_y = data->bearing_y;
    shape->advance = data->advance;
}

static void shape_for(u16 code, Shape *shape)
{
    shape_for_metric(text_font_find(&text_font, TEXT_FACE_TALL, code), code, shape);
}

/* Matches the core: a pair adjustment never leaves a step below one pixel. */
static int step(int previous, int metric)
{
    if (previous < 0 || metric < 0)
        return 0;
    int adjust = text_font_pair(&text_font, previous, metric);
    int advance = text_font.metrics[previous].advance;
    return advance + adjust < 1 ? 1 - advance : adjust;
}

static u16 *cell(TextWindow *window, TextPen *pen, unsigned column)
{
    unsigned index = window->cursor_y * window->columns + column;
    unsigned slot;
    u16 *pixels;
    unsigned i;
    if (column >= window->columns || index >= CELL_CAPACITY)
        return 0;
    slot = window->glyph_slots[index];
    if (slot >= 192 || text_tile_usage[slot] == 0xFF) {
        for (slot = 0; slot < 192 && text_tile_usage[slot] != 0xFF; ++slot) {}
        if (slot == 192)
            return 0;
        text_tile_usage[slot] = 0;
        window->glyph_slots[index] = slot;
        pixels = (u16 *)(text_tile_buffer + 0x800 + slot * 64);
        for (i = 0; i < 32; ++i)
            pixels[i] = 0x1111;
    } else {
        pixels = (u16 *)(text_tile_buffer + 0x800 + slot * 64);
    }
    if (column >= pen->cleared_to) {
        for (i = 0; i < 32; ++i)
            pixels[i] = 0x1111;
        pen->cleared_to = column + 1;
    }
    return pixels;
}

static u8 digit_advance(void)
{
    static const u16 first = 0x824F;
    u8 widest = 1;
    unsigned digit;
    for (digit = 0; digit < 10; ++digit) {
        int metric = text_font_find(&text_font, TEXT_FACE_TALL, first + digit);
        if (metric >= 0 && text_font.metrics[metric].advance > widest)
            widest = text_font.metrics[metric].advance;
    }
    return widest;
}

static int visible(TextWindow *window)
{
    unsigned rows = window->height >= 2 ? (window->height - 2) / 2 : 0;
    /* Selection lists keep offscreen rows for the native scrolling code. */
    return window->columns && !((window->flags & 1) && window->cursor_y >= rows);
}

static void draw(TextWindow *window, TextPen *pen, const Shape *shape, int ink_x)
{
    const u8 *glyph;
    int column;
    if (!shape->width || ink_x < 0 || !visible(window))
        return;
    glyph = (const u8 *)GAME_CALL(0x080416F9, shape->code);
    for (column = ink_x / 8; column * 8 < ink_x + shape->width && column < window->columns; ++column) {
        u16 *pixels = cell(window, pen, column);
        unsigned y;
        if (!pixels)
            continue;
        for (y = 0; y < shape->height; ++y) {
            int target_y = BASELINE + shape->bearing_y + (int)y;
            unsigned source_y = shape->top + y;
            unsigned x;
            if (target_y < 0 || target_y >= 16)
                continue;
            for (x = 0; x < shape->width; ++x) {
                unsigned target_x = ink_x + x;
                unsigned source_x = shape->left + x;
                unsigned source = (source_y < 8 ? 0 : 0x400) + (source_y % 8) * 4 + source_x / 2;
                unsigned color = (glyph[source] >> ((source_x & 1) * 4)) & 15;
                unsigned index;
                unsigned bits;
                unsigned old;
                if ((int)(target_x / 8) != column || color <= 1)
                    continue;
                index = target_y * 2 + (target_x % 8) / 4;
                bits = (target_x & 3) * 4;
                old = (pixels[index] >> bits) & 15;
                if (color > old)
                    pixels[index] = (pixels[index] & ~(15U << bits)) | (color << bits);
            }
        }
    }
}

void text_engine_newline(u8 id)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = pen_for(window);
    window->cursor_x = 0;
    window->cursor_y++;
    if ((window->flags & 1) && window->cursor_y >= (window->height - 2) / 2) {
        window_command(id, 5);
        window->cursor_y--;
    }
    pen->pen = 0;
    pen->previous = -1;
    pen->cleared_to = 0;
    pen->cursor_x = 0;
    pen->cursor_y = window->cursor_y;
    pen->valid = 0xA1;
}

void text_engine_emit(u8 id, u16 code)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = prepare(window);
    Shape shape;
    int origin;
    int end;
    shape_for(code, &shape);
    origin = pen->pen + step(pen->previous, shape.metric);
    if (origin < inset(window))
        origin = inset(window);
    end = origin + shape.bearing_x + shape.width;
    if (end < origin + shape.advance)
        end = origin + shape.advance;
    if (end > window->columns * 8 - inset(window) && origin > inset(window)) {
        text_engine_newline(id);
        origin = inset(window);
    }
    draw(window, pen, &shape, origin + shape.bearing_x);
    pen->pen = origin + shape.advance;
    pen->previous = shape.metric;
    finish(window, pen);
}

void text_engine_pad(u8 id, u8 pixels)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = prepare(window);
    unsigned end = pen->pen + pixels;
    unsigned column;
    for (column = pen->pen / 8; column * 8 < end; ++column)
        cell(window, pen, column);
    pen->pen = end;
    pen->previous = -1;
    finish(window, pen);
}

void text_engine_keyboard_glyph(u8 id, u16 code)
{
    Shape shape;
    shape_for(code, &shape);
    text_engine_pad(id, 0);
    text_engine_emit(id, code);
    text_engine_pad(id, shape.advance < 8 ? 8 - shape.advance : 0);
}

int text_engine_fits(u8 id, const u16 *codes, unsigned count, unsigned reserve)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = prepare(window);
    int origin = pen->pen > inset(window) ? pen->pen : inset(window);
    int previous = pen->previous;
    unsigned i;
    for (i = 0; i < count; ++i) {
        Shape shape;
        int end;
        shape_for(codes[i], &shape);
        origin += step(previous, shape.metric);
        end = shape.bearing_x + shape.width > shape.advance ? shape.bearing_x + shape.width : shape.advance;
        if (origin + end + (int)reserve > window->columns * 8 - inset(window))
            return 0;
        origin += shape.advance;
        previous = shape.metric;
    }
    return 1;
}

unsigned text_unit_size(const u8 *text)
{
    u8 byte = *text;
    if (byte >= 0x80 && byte <= 0x9F)
        return 2;
    switch (byte) {
    case 0x01: case 0x04: case 0x06: case TEXT_SAGA_WINDOW: case TEXT_SAGA_FIELD:
        return 2;
    case 0x02: case 0x07: case 0x08: case TEXT_SAGA_NUMBER: case TEXT_SAGA_PORTRAIT:
        return 3;
    default:
        return 1;
    }
}

/* Draws one span in the shared core format, or only measures it; a field fixes the line box. */
static int run(u8 id, const u8 *text, unsigned length, int wrap, int draw_glyphs, const Field *field)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = prepare(window);
    LayoutWork *work = WORK;
    const u16 *name = (const u16 *)0x0200758C;
    unsigned count = 0;
    unsigned i;
    int start = pen->pen;
    TextStatus status;
    for (i = 0; i < 8 && name[i]; ++i) {
        work->player[count++] = name[i] >> 8;
        work->player[count++] = name[i];
    }
    work->player[count++] = 0;
    TextProfile *profile = &work->profile;
    profile->left = field ? field->left : inset(window);
    profile->top = 0;
    profile->right = field ? field->right : draw_glyphs ? window->columns * 8 - inset(window) : 0x7FFF;
    profile->bottom = 0x7FFF;
    profile->baseline = BASELINE;
    profile->line_height = 16;
    profile->line_gap = 0;
    profile->face = TEXT_FACE_TALL;
    profile->position_scale = 8;
    profile->alignment = field ? field->alignment : TEXT_ALIGN_LEFT;
    profile->alignment_bound = TEXT_BOUND_INK;
    profile->wrap = wrap && draw_glyphs ? TEXT_WRAP_WORD : TEXT_WRAP_NONE;
    profile->overflow = TEXT_OVERFLOW_CLIP;
    profile->tabular_advance = field ? digit_advance() : 0;
    profile->paginate = 0;
    profile->compact_tabular_advance = 0;
    text_decoder_init_raw(&work->decoder, text, length, work->player, count, 1);
    work->state.x = field ? field->left : pen->pen > inset(window) ? pen->pen : inset(window);
    work->state.y = 0;
    work->state.previous = field ? -1 : pen->previous;
    work->state.before = -1;
    work->state.color = 0;
    work->state.initialized = 1;
    u8 row = window->cursor_y;
    status = text_layout_begin(profile, &work->decoder, &work->state, &work->cursor, &work->result);
    while (status == TEXT_OK) {
        TextEvent *event = &work->event;
        status = text_layout_peek(&text_font, profile, &work->decoder, &work->state,
                                  &work->cursor, &work->result, event);
        if (status != TEXT_OK)
            break;
        if (event->kind == TEXT_EVENT_GLYPH || event->kind == TEXT_EVENT_LINE_BEGIN) {
            if (event->kind == TEXT_EVENT_GLYPH && draw_glyphs) {
                Shape shape;
                shape_for_metric(event->glyph.metric, event->glyph.code, &shape);
                draw(window, pen, &shape, event->glyph.ink_x);
                pen->pen = event->glyph.pen_x + shape.advance;
                window->cursor_x = (pen->pen + 7) / 8;
                if (window->flags & 1) {
                    window_command(id, 4);
                    GAME_CALL(0x08041101);
                }
            }
            status = text_layout_commit(&text_font, profile, &work->decoder, &work->state,
                                        &work->cursor, &work->result, event);
            continue;
        }
        status = text_layout_commit(&text_font, profile, &work->decoder, &work->state,
                                    &work->cursor, &work->result, event);
        if (!draw_glyphs)
            continue;
        if (event->kind == TEXT_EVENT_LINE || event->kind == TEXT_EVENT_PAGE) {
            text_engine_newline(id);
            row = window->cursor_y;
        } else if (event->kind == TEXT_EVENT_POSITION) {
            window->cursor_y = row + event->y / 16;
            pen->cleared_to = 0;
        }
    }
    if (!draw_glyphs)
        return work->state.x - start;
    pen->pen = field ? field->right : work->state.x;
    pen->previous = field ? -1 : work->state.previous;
    finish(window, pen);
    return 0;
}

void text_engine_layout(u8 id, const u8 *text, unsigned length, int wrap)
{
    run(id, text, length, wrap, 1, 0);
}

/* Native number fields: tabular digits in width cells from the current cell, as in Legacy. */
void text_engine_number(u8 id, s32 value, unsigned width, unsigned style)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = prepare(window);
    u8 text[20];
    unsigned length = 0;
    unsigned digits[7];
    unsigned sign = value < 0 || (style & TEXT_NUMBER_SIGN);
    unsigned rest = value < 0 ? -value : value;
    unsigned i;
    Field field;
    if (width > 7)
        width = 7;
    if (!width)
        width = 1;
    if (rest > 9999999)
        rest = 9999999;
    static const u32 powers[7] = {1000000, 100000, 10000, 1000, 100, 10, 1};
    for (i = 0; i < width; ++i) {
        u32 power = powers[7 - width + i];
        digits[i] = 0;
        while (rest >= power * 10 && i == 0)
            rest -= power * 10;
        while (rest >= power) {
            rest -= power;
            ++digits[i];
        }
    }
    unsigned first = 0;
    if (!(style & TEXT_NUMBER_ZERO))
        while (first + 1 < width && !digits[first])
            ++first;
    if (sign) {
        text[length++] = 0x81;
        text[length++] = value < 0 ? 0x7C : 0x7B;
    }
    for (i = first; i < width; ++i) {
        text[length++] = 0x82;
        text[length++] = 0x4F + digits[i];
    }
    field.left = (pen->pen + 7) / 8 * 8;
    field.right = field.left + (sign + (style & TEXT_NUMBER_FIT ? width - first : width)) * 8;
    field.alignment = style & TEXT_NUMBER_RIGHT ? TEXT_ALIGN_RIGHT : TEXT_ALIGN_LEFT;
    run(id, text, length, 0, 1, &field);
}

void text_engine_signed_number(s32 value, u8 width, u16 flags)
{
    /* Flag 8 is ours: a maximum after a slash starts at the slash but keeps its column width. */
    unsigned style = flags & 1 ? TEXT_NUMBER_FIT : flags & 8 ? 0
        : flags & 2 ? TEXT_NUMBER_ZERO | TEXT_NUMBER_RIGHT : TEXT_NUMBER_RIGHT;
    text_engine_number(**(const u8 **)0x02009118, value, width, style | (flags & 4 ? TEXT_NUMBER_SIGN : 0));
}

void text_engine_window_number(u8 id, u16 value, u8 width, u8 format)
{
    static const u8 styles[4] = {
        TEXT_NUMBER_FIT, TEXT_NUMBER_ZERO | TEXT_NUMBER_RIGHT, 0, TEXT_NUMBER_RIGHT,
    };
    text_engine_number(id, value, width > 5 ? 5 : width, styles[format & 3]);
}

unsigned text_engine_pen(u8 id)
{
    return prepare(&text_windows[id])->pen;
}

unsigned text_engine_measure(u8 id, const u8 *text, unsigned length)
{
    int width = run(id, text, length, 0, 0, 0);
    return width > 0 ? width : 0;
}

void text_engine_release(u8 id)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = pen_for(window);
    unsigned extent = (pen->valid & 0xFE) == 0xA0 ? CELL_CAPACITY : 192;
    unsigned i;
    for (i = 0; i < CELL_CAPACITY; ++i) {
        unsigned slot = window->glyph_slots[i];
        if (i < extent && slot < 192)
            text_tile_usage[slot] = 0xFF;
        window->glyph_slots[i] = 0xFF;
    }
    pen->valid = 0xA0;
    message_log(id)->busy = 0;
    message_log(id)->figures = 0;
}

/* Native number printing keeps the original 8-pixel cells so columns line up. */
void text_engine_figure(u8 id, u16 code)
{
    TextPen *pen = pen_for(&text_windows[id]);
    Shape shape;
    int before;
    int start;
    if (code == 0x8140) {
        text_engine_pad(id, 8);
        return;
    }
    shape_for(code, &shape);
    before = shape.width < 8 ? (8 - shape.width) >> 1 : 0;
    text_engine_pad(id, 0);
    start = pen->pen;
    text_engine_pad(id, before > shape.bearing_x ? before - shape.bearing_x : 0);
    text_engine_emit(id, code);
    if (pen->pen < start + 8)
        text_engine_pad(id, start + 8 - pen->pen);
}

int text_engine_window_callback(u8 id, u8 command, u8 high, u8 low)
{
    TextWindow *window = &text_windows[id];
    TextPen *pen = pen_for(window);
    if (command == 3) {
        MessageLog *log = message_log(id);
        u16 code = ((u16)high << 8) | low;
        if ((log->figures == 1 || log->busy != 1) && (code == 0x8140 || (code >= 0x824F && code <= 0x8258)
                                              || code == 0x817B || code == 0x817C))
            text_engine_figure(id, code);
        else
            text_engine_emit(id, code);
        return 0;
    }
    if (command == 7) {
        text_engine_release(id);
    } else if (command == 5) {
        pen->valid = 0xA0;
    }
    return GAME_CALL(original_window_callback, id, command, high, low);
}
