#ifndef ZOIDS_SAGA_TEXT_ENGINE_H
#define ZOIDS_SAGA_TEXT_ENGINE_H

#include "text.h"
#include "text_core.h"

/* Translated text uses the shared text core format plus these Saga controls. */
#define TEXT_SAGA_END 0x00
#define TEXT_SAGA_PAGE 0x0E
#define TEXT_SAGA_WINDOW 0x1C
#define TEXT_SAGA_NUMBER 0x1F
#define TEXT_SAGA_PORTRAIT 0x14
#define TEXT_SAGA_FIELD 0x10

/* Window 0 also holds the pieces captured for a message template. */
typedef struct {
    u32 values[3];
    u16 indexes[3];
    u16 order[6];
    u16 template_record;
    u8 kinds[3];
    u8 fields;
    u8 count;
    u8 field;
    u8 busy;
    u8 figures;
    u8 suppress;
    u8 template_slot;
    u8 state;
    u8 reserved;
} MessageLog;

#define MESSAGE_CAPTURE 0xA6
#define MESSAGE_FIELD 0x8000
#define MESSAGE_DIGITS 0x01000000
#define MESSAGE_PLUS 0x40000000
#define MESSAGE_MINUS 0x80000000

extern const TextFont text_font;
extern const u8 text_glyph_origins[];
extern u8 *text_tile_buffer;
extern u8 text_tile_usage[192];

unsigned text_unit_size(const u8 *text);
MessageLog *message_log(u8 window);
void text_engine_emit(u8 window, u16 code);
void text_engine_pad(u8 window, u8 pixels);
void text_engine_keyboard_glyph(u8 window, u16 code);
void text_engine_figure(u8 window, u16 code);
int text_engine_fits(u8 window, const u16 *codes, unsigned count, unsigned reserve);
unsigned text_engine_pen(u8 window);
unsigned text_engine_measure(u8 window, const u8 *text, unsigned length);
void text_engine_layout(u8 window, const u8 *text, unsigned length, int wrap);
void text_engine_newline(u8 window);
#define TEXT_NUMBER_RIGHT 1
#define TEXT_NUMBER_ZERO 2
#define TEXT_NUMBER_SIGN 4
#define TEXT_NUMBER_FIT 8
void text_engine_number(u8 window, s32 value, unsigned width, unsigned style);
void text_engine_signed_number(s32 value, u8 width, u16 flags);
void text_engine_window_number(u8 window, u16 value, u8 width, u8 format);
void text_engine_release(u8 window);
int text_engine_window_callback(u8 window, u8 command, u8 high, u8 low);
int original_window_callback(u8 window, u8 command, u8 high, u8 low);

/* Game code runs on the calling task stack; see text_stack.s. */
#define TEXT_STACK_TARGET (*(volatile u32 *)0x0300788C)
u32 text_stack_out(u32 a, u32 b, u32 c, u32 d);
static inline u32 game_call(u32 address, u32 a, u32 b, u32 c, u32 d)
{
    TEXT_STACK_TARGET = address;
    return text_stack_out(a, b, c, d);
}
#define GAME_CALL(...) GAME_CALL_(__VA_ARGS__, 0, 0, 0, 0)
#define GAME_CALL_(address, a, b, c, d, ...) game_call((u32)(address), (u32)(a), (u32)(b), (u32)(c), (u32)(d))

#endif
