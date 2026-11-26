#include "text.h"

void initialize_player_name(void)
{
    static const u16 name[8] = {
        0x8260, 0x8294, 0x8288, 0x828C, 0x8285, 0x8140, 0x8140, 0x8140
    };
    volatile u16 *player = (volatile u16 *)0x0200758C;
    volatile u16 *saved = (volatile u16 *)0x02001874;
    unsigned i;

    for (i = 0; i < 8; ++i) {
        player[i] = name[i];
        saved[i] = name[i];
    }
}
