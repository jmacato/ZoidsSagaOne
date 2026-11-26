.syntax unified
.cpu arm7tdmi
.thumb
.text
.global database_menu_hook
.type database_menu_hook, %function
.thumb_func
database_menu_hook:
    push {r0, lr}
    bl stacked_database_menu
    pop {r0, r1}
    strh r4, [r6, #4]
    ldr r0, next_command
    bx r0
.align 2
next_command:
    .word 0x08043429
.size database_menu_hook, .-database_menu_hook
