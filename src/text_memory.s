.syntax unified
.cpu arm7tdmi
.thumb
.text
.global memcpy
.type memcpy, %function
.thumb_func
memcpy:
    push {r4}
    movs r3, #0
    cmp r2, #0
    beq memcpy_done
memcpy_loop:
    ldrb r4, [r1, r3]
    strb r4, [r0, r3]
    adds r3, r3, #1
    cmp r3, r2
    bne memcpy_loop
memcpy_done:
    pop {r4}
    bx lr
.size memcpy, .-memcpy
