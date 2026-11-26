#include "types.h"

typedef struct {
    u16 flags;
    u8 pad_2[4];
    u16 attr;
    u8 pad_8[4];
    s32 y;
    u8 pad_10[0xC];
    u16 tile;
    u8 pad_1E[0x2E];
    u16 active;
    u8 pad_4E[0x3A];
} CreditSprite;

typedef struct {
    u8 count;
    u8 first;
    u16 pad;
    s32 speed;
    const u32 *tiles;
    u32 words;
} CreditScene;

extern const CreditScene credit_scenes[];

#define SPRITES ((CreditSprite *)0x03004BBC)
#define TILE_SOURCE 0x1D

u8 credits_scene(u16 scene)
{
    const CreditScene *entry = &credit_scenes[scene];
    volatile u32 *dma = (volatile u32 *)0x040000D4;
    u8 count = entry->count;
    s32 speed = entry->speed;
    u8 done = 0;
    u8 i;

    dma[0] = (u32)entry->tiles;
    dma[1] = 0x06010000 + SPRITES[TILE_SOURCE].tile * 32;
    dma[2] = 0x84000000 | entry->words;
    (void)dma[2];
    for (i = 0; i < count; i++) {
        u16 x = i == 0 ? 8 : 16;
        u8 kind = entry->first + i;

        ((void (*)(u8, u8, u8, u8, u16, u16, s32))0x080490C1)(
            i, 0x7F, TILE_SOURCE, kind, x + 0x80, i * 16 + 0xA0, 0x80000000);
        SPRITES[i].active = 1;
    }
    while (done != count) {
        for (i = 0; i < count; i++) {
            if (!SPRITES[i].active)
                continue;
            SPRITES[i].y -= speed;
            if ((u16)(SPRITES[i].y >> 16) <= 0x9F)
                SPRITES[i].flags |= 0x8000;
            if ((u16)(SPRITES[i].y >> 16) == 0) {
                SPRITES[i].flags &= 0x7FFF;
                SPRITES[i].active = 0;
                done++;
            }
        }
        if (credit_scenes[scene + 1].count == 0xFF && SPRITES[0].y == 0x400000)
            return 1;
        ((void (*)(s32))0x0805EE79)(1);
    }
    return 0;
}
