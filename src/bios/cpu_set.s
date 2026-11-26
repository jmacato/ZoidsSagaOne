swi_CpuSet:
    stmfd sp!, {r3 - r7}

    @ r3 = unit count
    lsl r3, r2, #11
    lsrs r3, r3, #11
    beq .swi_CpuSet_done

    @ like the real BIOS, ignore sources that start or end in the BIOS area
    tst r0, #0x0E000000
    beq .swi_CpuSet_done
    tst r2, #(1 << 26)
    addeq r4, r0, r3, lsl #1
    addne r4, r0, r3, lsl #2
    tst r4, #0x0E000000
    beq .swi_CpuSet_done

    @ fill value, widened to a word for halfword fills
    tst r2, #(1 << 26)
    ldrne r5, [r0]
    ldreqh r5, [r0]
    orreq r5, r5, r5, lsl #16
    mov r12, #0
    bne .words

    @ halfword units move as words when the addresses allow it
    tst r2, #(1 << 24)
    moveq r4, r0
    movne r4, #0
    orr r4, r4, r1
    tst r4, #3
    bne .halves
    and r12, r3, #1
    movs r3, r3, lsr #1
    beq .tail
.words:
    tst r2, #(1 << 24)
    bne .fill_words
    subs r3, r3, #4
    blt .copy_rest
.copy_block:
    ldmia r0!, {r4 - r7}
    stmia r1!, {r4 - r7}
    subs r3, r3, #4
    bge .copy_block
.copy_rest:
    adds r3, r3, #4
    beq .tail
.copy_word:
    ldmia r0!, {r4}
    stmia r1!, {r4}
    subs r3, r3, #1
    bne .copy_word
    b .tail
.fill_words:
    mov r6, r5
    mov r7, r5
    mov r4, r5
    subs r3, r3, #4
    blt .fill_rest
.fill_block:
    stmia r1!, {r4 - r7}
    subs r3, r3, #4
    bge .fill_block
.fill_rest:
    adds r3, r3, #4
    beq .tail
.fill_word:
    str r5, [r1], #4
    subs r3, r3, #1
    bne .fill_word
.tail:
    cmp r12, #0
    ldrneh r4, [r0]
    strneh r4, [r1]
    ldmfd sp!, {r3 - r7}
    bx lr
.halves:
    tst r2, #(1 << 24)
    bne .fill16
.copy16:
    ldrh r4, [r0], #2
    strh r4, [r1], #2
    subs r3, r3, #1
    bne .copy16
    ldmfd sp!, {r3 - r7}
    bx lr
.fill16:
    strh r5, [r1], #2
    subs r3, r3, #1
    bne .fill16
.swi_CpuSet_done:
    ldmfd sp!, {r3 - r7}
    bx lr

swi_CpuFastSet:
    stmfd sp!, {r3 - r11}

    @ r3 = word count
    lsl r3, r2, #11
    lsrs r3, #11
    beq .swi_CpuFastSet_done
    tst r0, #0x0E000000
    beq .swi_CpuFastSet_done
    add r4, r0, r3, lsl #2
    tst r4, #0x0E000000
    beq .swi_CpuFastSet_done

    @ perform copy or fill operation depending on bit24 or r2.
    tst r2, #(1 << 24)
    bne .fill_fast32
.copy_fast32:
    ldmia r0!, {r4 - r11}
    stmia r1!, {r4 - r11}
    subs r3, #8
    bgt .copy_fast32
    ldmfd sp!, {r3 - r11}
    bx lr
.fill_fast32:
    ldr r4, [r0]
    mov r5, r4
    mov r6, r4
    mov r7, r4
    mov r8, r4
    mov r9, r4
    mov r10, r4
    mov r11, r4
.fill_fast32_loop:
    stmia r1!, {r4 - r11}
    subs r3, #8
    bgt .fill_fast32_loop
.swi_CpuFastSet_done:
    ldmfd sp!, {r3 - r11}
    bx lr
