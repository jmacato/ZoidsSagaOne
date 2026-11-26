#include "text_engine.h"

typedef struct {
    const u8 *text;
    u32 count;
    const u8 *sequence[6];
} MessageTemplate;

typedef struct {
    const u8 *source;
    const u8 *end;
    const u8 *text;
    const u8 *compact;
    const MessageTemplate *message;
} MenuTranslation;

/* Return addresses of print calls whose output a message template replaces. */
typedef struct {
    u32 call;
    u8 role;
    u8 kind;
    u16 reserved;
} MessageSite;

extern const MenuTranslation menu_records[];
extern const u32 menu_record_count;
extern const MessageSite message_sites[];
extern const u32 message_site_count;
extern const u8 *const database_descriptions[256];

/* Free IWRAM after the core scratch and layout workspace. */
#define COMPOSED ((u8 *)0x03007D90)
#define COMPOSED_CAPACITY 0x160

static void emit(u8 window, u16 code, int keyboard)
{
    if (keyboard)
        text_engine_keyboard_glyph(window, code);
    else
        GAME_CALL(0x08041699, window, code);
    if (text_windows[window].flags & 1) {
        GAME_CALL(0x080416C9, window, 4, 0, 0);
        GAME_CALL(0x08041101);
    }
}

static int glue_control(u8 byte)
{
    return byte == TEXT_SAGA_END || byte == TEXT_SAGA_PAGE || byte == TEXT_SAGA_WINDOW
        || byte == TEXT_SAGA_NUMBER || byte == TEXT_SAGA_PORTRAIT || byte == TEXT_SAGA_FIELD;
}

static const u8 *span_end(const u8 *text)
{
    while (!glue_control(*text))
        text += text_unit_size(text);
    return text;
}

/* Expands glyphs and the player name up to the first other control. */
static unsigned text_codes(const u8 *text, u16 *codes, unsigned capacity)
{
    unsigned count = 0;
    while (count < capacity) {
        if (*text >= 0x80 && *text <= 0x9F) {
            codes[count++] = (text[0] << 8) | text[1];
            text += 2;
        } else if (*text == 0x03) {
            const u16 *name = (const u16 *)0x0200758C;
            unsigned i;
            for (i = 0; i < 8 && name[i] && count < capacity; ++i)
                codes[count++] = name[i];
            ++text;
        } else {
            break;
        }
    }
    return count;
}

static int fits(u8 window, const u8 *text, unsigned reserve)
{
    unsigned width = text_engine_measure(window, text, span_end(text) - text);
    return text_engine_pen(window) + width + reserve <= text_windows[window].columns * 8U;
}

/* The battle HUD copies only 32 pixels, so long names end with three dots. */
static int __attribute__((noinline)) battle_header(u8 window, const u8 *text)
{
    u16 codes[35];
    unsigned count = text_codes(text, codes, 32);
    unsigned reserve = text_windows[window].columns * 8 - 32;
    unsigned i;
    if (text_engine_fits(window, codes, count, reserve))
        return 0;
    do {
        --count;
        codes[count] = codes[count + 1] = codes[count + 2] = 0x8144;
    } while (count && !text_engine_fits(window, codes, count + 3, reserve));
    for (i = 0; i < count + 3; ++i)
        emit(window, codes[i], 0);
    return 1;
}

#define RENDER_DIGITS 2
#define RENDER_DATABASE 4
#define RENDER_FIELDS 8

#define SITE_PART 1
#define SITE_FIELD 2
#define FIELD_SCRIPT 1
#define FIELD_NUMBER 2
#define FIELD_PILOT 3
#define FIELD_ZOID 4
#define FIELD_UNIT 5

static MessageLog *capture_log(void)
{
    return message_log(0);
}

/* The buffer shares RAM with glyph slots, so only a marked buffer holds pieces. */
static unsigned pending(const MessageLog *log)
{
    return log->state == MESSAGE_CAPTURE && log->count <= 6 && log->fields <= 3 ? log->count : 0;
}

static void reset_capture(MessageLog *log)
{
    log->count = 0;
    log->fields = 0;
    log->template_record = 0;
    log->state = MESSAGE_CAPTURE;
}

static const MenuTranslation *find_record(const u8 *source)
{
    u32 low = 0;
    u32 high = menu_record_count;
    while (low < high) {
        u32 middle = low + ((high - low) >> 1);
        if (menu_records[middle].source < source)
            low = middle + 1;
        else
            high = middle;
    }
    return low < menu_record_count && menu_records[low].source == source ? &menu_records[low] : 0;
}

/* Scripts may set up a window before their text command. */
static const MenuTranslation *script_record(const u8 *const *table, u32 index)
{
    const u8 *script = table[index];
    unsigned i;
    for (i = 0; i < 16; ++i) {
        const MenuTranslation *record = script[i] == 0x20 ? find_record(script + i) : 0;
        if (record)
            return record;
    }
    return 0;
}

static const MenuTranslation *field_record(u32 kind, u32 value, u32 index)
{
    if (kind == FIELD_PILOT)
        return value ? script_record((const u8 *const *)0x086760DC, value)
                     : script_record((const u8 *const *)0x08675C5C, 0x33);
    if (kind == FIELD_ZOID)
        return script_record((const u8 *const *)0x08675E78, value & 0xFF);
    if (kind == FIELD_UNIT)
        return script_record((const u8 *const *)0x08675E78, ((const u8 *)value)[0x0E]);
    return script_record((const u8 *const *)value, index);
}

static const u8 *trim(const u8 *text)
{
    while (text[0] == 0x81 && text[1] == 0x40)
        text += 2;
    return text;
}

static u32 number_value(const MessageLog *log, unsigned slot)
{
    const u32 *powers = (const u32 *)0x08663B8C;
    s32 value = log->values[slot];
    u32 sign = value < 0 ? MESSAGE_MINUS : (log->indexes[slot] >> 8) & 4 ? MESSAGE_PLUS : 0;
    u32 magnitude = value < 0 ? 0U - (u32)value : (u32)value;
    unsigned width = log->indexes[slot] & 0xFF;
    unsigned i;
    if (magnitude > 9999999)
        magnitude = 9999999;
    for (i = 0; width < 7 && i < 7 - width; ++i) {
        while (magnitude >= powers[i])
            magnitude -= powers[i];
    }
    return magnitude | sign | MESSAGE_DIGITS;
}

/* Fields resolve to a record text pointer or, for numbers, the captured value. */
static u32 __attribute__((noinline)) field_value(u8 window, unsigned field)
{
    MessageLog *log = capture_log();
    const MenuTranslation *record;
    if (!field || field > log->fields || log->state != MESSAGE_CAPTURE)
        return 0;
    log->field = field - 1;
    if (log->kinds[field - 1] == FIELD_NUMBER)
        return number_value(log, field - 1);
    record = field_record(log->kinds[field - 1], log->values[field - 1], log->indexes[field - 1]);
    if (!record)
        return 0;
    return (u32)trim(record->compact && (text_windows[window].style & 1) ? record->compact : record->text);
}

/* Enemy rank icons are native glyph records that follow the unit name. */
static u16 __attribute__((noinline)) field_rank(void)
{
    const MessageLog *log = capture_log();
    const u8 *unit;
    const u8 *icon;
    unsigned rank;
    if (log->state != MESSAGE_CAPTURE || log->field >= log->fields || log->kinds[log->field] != FIELD_UNIT)
        return 0;
    unit = (const u8 *)log->values[log->field];
    rank = unit[0xCF];
    if ((u8)(rank - 1) > 5)
        return 0;
    icon = ((const u8 *const *)0x08675DBC)[((const u8 *)0x0866B94C)[rank]];
    return icon[0] == 0x20 && icon[1] > 0x3F ? icon[1] | (icon[2] << 8) : 0;
}

static unsigned put_code(u8 *out, unsigned length, u16 code)
{
    out[length++] = code >> 8;
    out[length++] = code;
    return length;
}

/* Template fields become their text, digits, or rank icon so the core measures the whole line. */
static const u8 *__attribute__((noinline)) compose(u8 window, const u8 *text)
{
    static const u32 powers[7] = {1000000, 100000, 10000, 1000, 100, 10, 1};
    u8 *out = COMPOSED;
    unsigned length = 0;
    while (*text && length + 24 < COMPOSED_CAPACITY) {
        if (*text != TEXT_SAGA_FIELD) {
            unsigned size = text_unit_size(text);
            while (size--)
                out[length++] = *text++;
            continue;
        }
        u32 value = field_value(window, text[1]);
        text += 2;
        if (value >> 25 == 4) {
            const u8 *field = (const u8 *)value;
            unsigned start = length;
            while (!glue_control(*field) && length + 24 < COMPOSED_CAPACITY) {
                unsigned size = text_unit_size(field);
                while (size--)
                    out[length++] = *field++;
            }
            while (length >= start + 2 && out[length - 2] == 0x81 && out[length - 1] == 0x40)
                length -= 2;
            u16 rank = field_rank();
            if (rank)
                length = put_code(out, length, rank);
        } else if (value) {
            u32 magnitude = value & 0xFFFFFF;
            unsigned i;
            int started = 0;
            if (value & (MESSAGE_PLUS | MESSAGE_MINUS))
                length = put_code(out, length, value & MESSAGE_MINUS ? 0x817C : 0x817B);
            for (i = 0; i < 7; ++i) {
                unsigned digit = 0;
                while (magnitude >= powers[i]) {
                    magnitude -= powers[i];
                    ++digit;
                }
                if (digit || started || i == 6) {
                    started = 1;
                    length = put_code(out, length, 0x824F + digit);
                }
            }
        }
    }
    out[length] = 0;
    return out;
}

static void __attribute__((noinline)) page_break(u8 window)
{
    const u8 page[] = {4, 0xFF, 5, 0, 0x0E, window, 0x22};
    GAME_CALL(0x0803E4B9, page);
}

static void __attribute__((noinline)) portrait(u8 window, u8 character, u8 expression)
{
    if (character == 0xFF) {
        const u8 full[] = {3, 0, 3, window, 1, window, 0x10, 0, 12, 30, 8, 1, 4, 0xFF, 0x22};
        if (text_windows[0].active)
            GAME_CALL(0x0803E4B9, full);
    } else if (text_windows[0].active) {
        const u8 face[] = {0x0F, 0, character, expression, 4, 0xFF, 0x22};
        GAME_CALL(0x0803E4B9, face);
    } else {
        const u8 framed[] = {3, window, 1, 0, 0x10, 0, 12, 8, 8, 0,
                             0x0F, 0, character, expression,
                             1, window, 0x10, 7, 12, 23, 8, 1, 0x3B, window, 4, 0xFF, 0x22};
        GAME_CALL(0x0803E4B9, framed);
    }
    message_log(window)->busy = 1;
}

static void render(u8 window, const u8 *text, const u16 *numbers, u32 flags)
{
    if (flags & RENDER_FIELDS)
        text = compose(window, text);
    /* Native number formatting emits separate records for individual digits. */
    int wrap = !(flags & RENDER_DIGITS) && !(text_windows[window].style & 1);
    while (*text != TEXT_SAGA_END) {
        if (*text == TEXT_SAGA_WINDOW) {
            window = text[1];
            text += 2;
        } else if (*text == TEXT_SAGA_PAGE) {
            /* Database entries load before the native scene enables the display. */
            if (flags & RENDER_DATABASE)
                break;
            page_break(window);
            message_log(window)->busy = 1;
            ++text;
        } else if (*text == TEXT_SAGA_PORTRAIT) {
            portrait(window, text[1], text[2]);
            text += 3;
        } else if (*text == TEXT_SAGA_NUMBER) {
            message_log(window)->figures = 1;
            GAME_CALL(0x08040F45, window, numbers[text[1]], text[2] & 7, text[2] >> 4);
            message_log(window)->figures = 0;
            text += 3;
        } else if (*text == TEXT_SAGA_FIELD) {
            text += 2;
        } else if (flags & RENDER_DIGITS) {
            const u8 *end = span_end(text);
            for (; text < end; text += text_unit_size(text))
                if (*text >= 0x80 && *text <= 0x9F)
                    text_engine_figure(window, (text[0] << 8) | text[1]);
        } else {
            const u8 *end = span_end(text);
            text_engine_layout(window, text, end - text, wrap);
            text = end;
        }
    }
}

static const MenuTranslation *__attribute__((noinline)) choose(const MenuTranslation *record, u8 window,
                                                               const u8 **text)
{
    unsigned reserve = window == 5 && text_windows[window].columns == 17 ? 64 : 26;
    if (!record->compact)
        return record;
    if (text_windows[window].style & 1 ? !fits(window, *text, reserve) : window && !fits(window, *text, 0))
        *text = record->compact;
    return record;
}

static void run_table(const u8 *const *table, u32 index)
{
    *(volatile u16 *)0x04000008 = *(const u16 *)0x0200D91C;
    *(const u8 *const **)0x02007584 = table;
    GAME_CALL(0x0803E4B9, GAME_CALL(0x08041809, index));
}

static void print_pilot(u32 kind)
{
    kind &= 0xFF;
    if (kind)
        run_table((const u8 *const *)0x086760DC, kind);
    else
        run_table((const u8 *const *)0x08675C5C, 0x33);
}

static void print_zoid(u32 kind)
{
    run_table((const u8 *const *)0x08675E78, kind & 0xFF);
}

static void print_unit(const u8 *unit)
{
    print_zoid(unit[0x0E]);
    if ((u8)(unit[0xCF] - 1) <= 5)
        run_table((const u8 *const *)0x08675DBC, ((const u8 *)0x0866B94C)[unit[0xCF]]);
}

static const MessageSite *find_site(u32 call)
{
    u32 low = 0;
    u32 high = message_site_count;
    while (low < high) {
        u32 middle = low + ((high - low) >> 1);
        if (message_sites[middle].call < call)
            low = middle + 1;
        else
            high = middle;
    }
    return low < message_site_count && message_sites[low].call == call ? &message_sites[low] : 0;
}

static const u8 *translate(const u8 *source, const u16 *numbers, int description_tail);

/* Prints captured pieces in their original order when no template applies. */
static void __attribute__((noinline)) flush(MessageLog *log)
{
    unsigned count = pending(log);
    unsigned fields = log->fields;
    unsigned i;
    log->state = 0;
    for (i = 0; i < count; ++i) {
        unsigned entry = log->order[i];
        unsigned slot = entry & 3;
        if (!(entry & MESSAGE_FIELD)) {
            if (entry <= menu_record_count)
                translate(menu_records[entry - 1].source, 0, 0);
            continue;
        }
        if (slot >= fields)
            continue;
        if (log->kinds[slot] == FIELD_NUMBER)
            text_engine_signed_number(log->values[slot], log->indexes[slot] & 0xFF, log->indexes[slot] >> 8);
        else if (log->kinds[slot] == FIELD_PILOT)
            print_pilot(log->values[slot]);
        else if (log->kinds[slot] == FIELD_ZOID)
            print_zoid(log->values[slot]);
        else if (log->kinds[slot] == FIELD_UNIT)
            print_unit((const u8 *)log->values[slot]);
        else
            run_table((const u8 *const *)log->values[slot], log->indexes[slot]);
    }
}

static void capture(MessageLog *log, u32 kind, u32 value, u32 index)
{
    if (!pending(log))
        reset_capture(log);
    log->kinds[log->fields] = kind;
    log->values[log->fields] = value;
    log->indexes[log->fields] = index;
    log->order[log->count++] = MESSAGE_FIELD | log->fields++;
}

/* A closing record picks the first template whose sequence matches the captured pieces. */
static int __attribute__((noinline)) close_message(MessageLog *log, const u8 *const *table, u32 index)
{
    const MenuTranslation *record = script_record(table, index);
    const MessageTemplate *message;
    unsigned i;
    if (!record || !record->message)
        return 0;
    for (message = record->message; message->text; ++message) {
        if (message->count != log->count)
            continue;
        for (i = 0; i < log->count; ++i) {
            unsigned entry = log->order[i];
            if (!message->sequence[i] ? !(entry & MESSAGE_FIELD)
                    : (entry & MESSAGE_FIELD) || menu_records[entry - 1].source != message->sequence[i])
                break;
        }
        if (i == log->count) {
            log->template_record = record - menu_records + 1;
            log->template_slot = message - record->message;
            return 1;
        }
    }
    return 0;
}

#define CALL_RUN 0
#define CALL_DONE 1
#define CALL_PART 2
#define CALL_FLUSH 3
#define CALL_RETRY 4

/* Decides what a hooked print does; printing happens in the caller to keep the stack shallow. */
static int __attribute__((noinline)) begin_call(u32 call, u32 kind, u32 value, u32 index)
{
    const MessageSite *site = find_site(call);
    MessageLog *log = capture_log();
    /* Native label padding must not split a captured row. */
    if (call == 0x0804D63E && pending(log))
        return CALL_DONE;
    if (site && site->role == SITE_FIELD) {
        if (pending(log) >= 6 || (pending(log) && log->fields >= 3))
            return CALL_RETRY;
        capture(log, kind, value, index);
        return CALL_DONE;
    }
    if (site && site->role == SITE_PART) {
        if (pending(log) >= 6)
            return CALL_RETRY;
        if (!pending(log))
            reset_capture(log);
        return CALL_PART;
    }
    if (!pending(log))
        return CALL_RUN;
    return kind == FIELD_SCRIPT && close_message(log, (const u8 *const *)value, index) ? CALL_RUN : CALL_FLUSH;
}

static void __attribute__((noinline)) dispatch(u32 call, u32 kind, u32 value, u32 index)
{
    MessageLog *log = capture_log();
    int action;
    while ((action = begin_call(call, kind, value, index)) == CALL_RETRY)
        flush(log);
    if (action == CALL_DONE)
        return;
    if (action == CALL_PART) {
        log->suppress = 1;
        run_table((const u8 *const *)value, index);
        log->suppress = 0;
        return;
    }
    if (action == CALL_FLUSH)
        flush(log);
    if (kind == FIELD_NUMBER)
        text_engine_signed_number(value, index & 0xFF, index >> 8);
    else if (kind == FIELD_PILOT)
        print_pilot(value);
    else if (kind == FIELD_ZOID)
        print_zoid(value);
    else if (kind == FIELD_UNIT)
        print_unit((const u8 *)value);
    else
        run_table((const u8 *const *)value, index);
    log->state = 0;
}

int message_run_table(const u8 *const *table, u32 index, u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, (u32)table, index & 0xFFFF);
    return 0;
}

void message_print_number(s32 value, u32 width, u32 flags, u32 call)
{
    dispatch(call & ~1, FIELD_NUMBER, value, (width & 0xFF) | ((flags & 0xFF) << 8));
}

void message_print_pilot(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_PILOT, kind & 0xFF, 0);
}

void message_print_zoid(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_ZOID, kind & 0xFF, 0);
}

void message_print_unit(const u8 *unit, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_UNIT, (u32)unit, 0);
}

void message_print_item(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, 0x08676238, kind & 0xFF);
}

void message_print_zi_item(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, 0x08676350, kind & 0xFF);
}

void message_print_item_effect(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, 0x0867636C, kind & 0xFF);
}

void message_print_command(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, 0x08676388, kind & 0xFF);
}

void message_print_construction_part(u32 kind, u32 r1 __attribute__((unused)), u32 r2 __attribute__((unused)), u32 call)
{
    dispatch(call & ~1, FIELD_SCRIPT, 0x08676254, kind & 0xFF);
}

/* Name entry pages keep the original script bytes and an eight-pixel grid. */
static const u8 *keyboard(const u8 *text)
{
    u8 window = 6;
    while (*text != 0x1D) {
        u8 control = *text++;
        if (control > 0x3F) {
            u16 code = control | (*text++ << 8);
            emit(window, code, 1);
        } else if (control == 0x1C) {
            window = *text++;
        } else if (control == 0x0D) {
            text_engine_newline(window);
        }
    }
    return text;
}

static const u8 *translate(const u8 *source, const u16 *numbers, int description_tail)
{
    const MenuTranslation *record;
    const u8 *text;
    u8 window;
    unsigned flags = 0;

    if (source >= (const u8 *)0x086CCD30 && source < (const u8 *)0x086D0700
            && source[0] == 0x20 && source[1] == 0x1C && source[2] == 6)
        return keyboard(source + 3);
    record = find_record(source);
    if (!record)
        return 0;
    text = record->text;
    window = **(const u8 **)0x02009118;
    if (!description_tail) {
        MessageLog *log = capture_log();
        if (log->suppress == 1) {
            if (pending(log) < 6 && log->state == MESSAGE_CAPTURE)
                log->order[log->count++] = record - menu_records + 1;
            return record->end;
        }
        if (log->state == MESSAGE_CAPTURE && log->template_record == record - menu_records + 1) {
            if (text[0] == TEXT_SAGA_WINDOW)
                window = text[1];
            text = record->message[log->template_slot].text;
            flags = RENDER_FIELDS;
        }
    }
    if (!(flags & RENDER_FIELDS)) {
        if (source == (const u8 *)0x08755F6E && (text_windows[window].style & 1))
            return record->end;
        if (text_windows[window].height == 2 && text_windows[window].palette == 4
                && text_windows[window].columns >= 4 && battle_header(window, text))
            return record->end;
        choose(record, window, &text);
        if (description_tail) {
            while (*text != TEXT_SAGA_END && *text != TEXT_SAGA_PAGE)
                text += text_unit_size(text);
            if (*text == TEXT_SAGA_END)
                return record->end;
            ++text;
            window = 2;
            GAME_CALL(0x080416C9, window, 7, 0, 0);
        }
        if (source >= (const u8 *)0x08673504 && source <= (const u8 *)0x08673545)
            flags |= RENDER_DIGITS;
        if (!description_tail && source >= (const u8 *)0x086F9772 && source < (const u8 *)0x0870067C)
            flags |= RENDER_DATABASE;
    }
    message_log(window)->busy = 1;
    render(window, text, numbers, flags);
    message_log(window)->busy = 0;
    if (flags & RENDER_FIELDS)
        capture_log()->state = 0;
    return record->end;
}

const u8 *menu_translate(const u8 *source, const u16 *numbers)
{
    return translate(source, numbers, 0);
}

void database_menu(void)
{
    unsigned id = *(const u16 *)0x0200757E;
    if (id < 256 && database_descriptions[id]) {
        translate(database_descriptions[id], 0, 1);
        GAME_CALL(0x080416C9, 2, 4, 0, 0);
    }
    GAME_CALL(0x0803E489, (const u8 *)0x0870067C, 2);
}
