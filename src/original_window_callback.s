.syntax unified
.cpu arm7tdmi
.thumb
.text
.global original_window_callback
.type original_window_callback, %function
.thumb_func
original_window_callback:
    push {r4, r5, r6, r7, lr}
    mov r7, r8
    push {r7}
    sub sp, #4
    lsls r0, r0, #24
    lsrs r6, r0, #24
    lsls r1, r1, #24
    lsrs r1, r1, #24
    ldr r4, resume
    bx r4
.align 2
resume:
    .word 0x0803E261
.size original_window_callback, .-original_window_callback
