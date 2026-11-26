@ BIOS call replacements, called as ordinary ARM functions from Thumb veneers.
@ The routines come from Cult-of-GBA/BIOS (MIT, see LICENSE in this folder).
.arm
.syntax divided
.text
.balign 4

.macro check_invalid_decomp src_len, unused_reg, src_addr, return_branch
    cmp \src_len, #0
    movnes \unused_reg, \src_addr, lsr #25
    beq \return_branch
.endm

.global swi_CpuSet, swi_CpuFastSet, swi_Div, swi_ObjAffineSet
.global swi_LZ77UnCompWrite8bit, swi_LZ77UnCompWrite16bit, swi_HuffUnCompReadNormal
.global swi_RLUnCompReadNormalWrite8bit, swi_RLUnCompReadNormalWrite16bit
.global swi_Diff8bitUnfilterWrite8bit, swi_Diff8bitUnfilterWrite16bit, swi_Diff16bitUnfilter
.global swi_SoundGetJumpList

.include "bios/cpu_set.s"
.include "bios/lz77.s"
.include "bios/huffman.s"
.include "bios/running_length.s"
.include "bios/diff_unfilter.s"
.include "bios/division.s"
.include "bios/rotation_scaling.s"

@ The game keeps its own copy of the sound engine jump table.
swi_SoundGetJumpList:
    ldr r1, =0x08566080
    mov r2, #36
.sound_jump_list_loop:
    ldr r3, [r1], #4
    str r3, [r0], #4
    subs r2, r2, #1
    bne .sound_jump_list_loop
    bx lr

.ltorg
