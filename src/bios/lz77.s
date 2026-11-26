@ TODO: check if source address is valid (>= 0x02000000)

@ ROM fetches are slow, so each routine runs from a copy on the IWRAM stack.
.macro run_from_stack name, body, end
\name:
    stmfd sp!, {r4 - r6, lr}
    adr r2, \body
    adr r3, \end
    sub sp, sp, #(\end - \body)
    mov r4, sp
1:
    ldr r5, [r2], #4
    str r5, [r4], #4
    cmp r2, r3
    blo 1b
    mov lr, pc
    mov pc, sp
    add sp, sp, #(\end - \body)
    ldmfd sp!, {r4 - r6, lr}
    bx lr
.endm

run_from_stack swi_LZ77UnCompWrite8bit, .lz77_8bit_body, .lz77_8bit_end
run_from_stack swi_LZ77UnCompWrite16bit, .lz77_16bit_body, .lz77_16bit_end

.lz77_8bit_body:
    stmfd sp!, {r3 - r6}

    @ Read header word:
    @ bit0-3:  reserved
    @ bit4-7:  compressed type (1 for LZ77)
    @ bit8-31: size of compressed data
    ldr r2, [r0], #4
    lsrs r2, r2, #8
    @ ignore zero-length decompression requests
    beq .lz77_8bit_done

.lz77_8bit_loop:
    @ read encoder byte, shift to MSB for easier access.
    ldrb r3, [r0], #1
    orr r3, #0x01000000
.lz77_8bit_encoder_loop:
    tst r3, #0x80
    bne .lz77_8bit_copy_window
.lz77_8bit_copy_byte:
    @ copy byte from current source to current destination
    ldrb r4, [r0], #1
    strb r4, [r1], #1

    @ check if decompressed length has been reached.
    subs r2, #1
    beq .lz77_8bit_done

    @ read next encoder or process next block
    lsls r3, #1
    bcc .lz77_8bit_encoder_loop
    b .lz77_8bit_loop
.lz77_8bit_copy_window:
    @ read window tuple {displacement, size}
    ldrb r4, [r0], #1
    ldrb r5, [r0], #1

    @ r5 = window displacement
    orr r5, r5, r4, lsl #8
    bic r5, #0xF000
    add r5, #1

    @ r4 = window size
    lsr r4, #4
    add r4, #3
.lz77_8bit_copy_window_loop:
    @ copy byte from window to current destination
    ldrb r6, [r1, -r5]
    strb r6, [r1], #1

    @ check if decompressed length has been reached
    subs r2, #1
    beq .lz77_8bit_done

    @ check if window has been fully copied
    subs r4, #1
    bne .lz77_8bit_copy_window_loop

    @ read next encoder or process next block
    lsls r3, #1
    bcc .lz77_8bit_encoder_loop
    b .lz77_8bit_loop

.lz77_8bit_done:
    ldmfd sp!, {r3 - r6}
    bx lr
.balign 4
.lz77_8bit_end:

.lz77_16bit_body:
    stmfd sp!, {r3 - r9}

    @ r2 = bytes left, r7 = byte waiting for its odd partner
    ldr r2, [r0], #4
    lsrs r2, r2, #8
    beq .l16_done
    mov r7, #0
.l16_block:
    ldrb r3, [r0], #1
    orr r3, #0x01000000
.l16_token:
    tst r3, #0x80
    bne .l16_window
    ldrb r8, [r0], #1
    tst r1, #1
    orrne r7, r7, r8, lsl #8
    strneh r7, [r1, #-1]
    moveq r7, r8
    add r1, #1
    subs r2, #1
    beq .l16_done
.l16_next:
    lsls r3, #1
    bcc .l16_token
    b .l16_block

.l16_window:
    @ r4 = length, r5 = displacement, r6 = window source
    ldrb r4, [r0], #1
    ldrb r5, [r0], #1
    orr r5, r5, r4, lsl #8
    bic r5, #0xF000
    add r5, #1
    lsr r4, #4
    add r4, #3
    cmp r4, r2
    movhi r4, r2
    sub r2, r2, r4
    sub r6, r1, r5
    cmp r5, #1
    beq .l16_run
    eor r9, r1, r6
    tst r9, #1
    bne .l16_bytes

    @ same parity: align the destination, then copy halfwords
    tst r1, #1
    beq .l16_halves
    ldrb r8, [r6], #1
    orr r7, r7, r8, lsl #8
    strh r7, [r1, #-1]
    add r1, #1
    subs r4, #1
    beq .l16_window_end
.l16_halves:
    subs r4, #2
    bmi .l16_halves_tail
.l16_halves_loop:
    ldrh r8, [r6], #2
    strh r8, [r1], #2
    subs r4, #2
    bpl .l16_halves_loop
.l16_halves_tail:
    adds r4, #2
    beq .l16_window_end
    ldrb r7, [r6]
    add r1, #1
    b .l16_window_end

.l16_bytes:
    ldrb r8, [r6], #1
    tst r1, #1
    orrne r7, r7, r8, lsl #8
    strneh r7, [r1, #-1]
    moveq r7, r8
    add r1, #1
    subs r4, #1
    bne .l16_bytes
    b .l16_window_end

.l16_run:
    @ a displacement of one repeats the previous byte
    tst r1, #1
    andne r8, r7, #0xFF
    ldreqb r8, [r1, #-1]
    beq .l16_run_even
    orr r7, r7, r8, lsl #8
    strh r7, [r1, #-1]
    add r1, #1
    subs r4, #1
    beq .l16_window_end
.l16_run_even:
    orr r9, r8, r8, lsl #8
    subs r4, #2
    bmi .l16_run_tail
.l16_run_loop:
    strh r9, [r1], #2
    subs r4, #2
    bpl .l16_run_loop
.l16_run_tail:
    adds r4, #2
    beq .l16_window_end
    mov r7, r8
    add r1, #1
.l16_window_end:
    cmp r2, #0
    bne .l16_next
.l16_done:
    ldmfd sp!, {r3 - r9}
    bx lr
.balign 4
.lz77_16bit_end:
