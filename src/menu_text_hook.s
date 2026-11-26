.syntax unified
.cpu arm7tdmi
.thumb
.text
.global menu_text_hook
.type menu_text_hook, %function
.thumb_func
menu_text_hook:
    movs r0, r7
    add r1, sp, #0x2c
    bl stacked_menu_translate
    cmp r0, #0
    beq original_text
    movs r7, r0
    ldr r0, next_command
    bx r0
original_text:
    ldr r0, begin_text
    bx r0
.align 2
next_command:
    .word 0x080404A5
begin_text:
    .word 0x080400EB
.size menu_text_hook, .-menu_text_hook
