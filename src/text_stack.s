.syntax unified
.cpu arm7tdmi
.thumb
.text
@ Task stacks are 640 bytes, so runtime frames live on IWRAM no trace has touched and
@ game code called from the runtime still runs on the task's own stack.
.equ TEXT_STACK_TOP, 0x03007880
.equ STATE, 0x03007880
.equ OWNER, 0
.equ TASK_SP, 4
.equ RESUME_SP, 8
.equ TARGET, 12
.equ CURRENT_TASK, 0x030005C8

.macro STACKED name
.global stacked_\name
.type stacked_\name, %function
.thumb_func
stacked_\name:
    push {r4-r7, lr}
    ldr r4, =\name
    b text_stack_enter
.size stacked_\name, .-stacked_\name
.endm

@ Message hooks receive the game's call site in r3.
.macro STACKED_SITE name
.global stacked_\name
.type stacked_\name, %function
.thumb_func
stacked_\name:
    push {r4-r7, lr}
    mov r3, lr
    ldr r4, =\name
    b text_stack_enter
.size stacked_\name, .-stacked_\name
.endm

.thumb_func
text_stack_enter:
    ldr r6, =STATE
    ldr r7, =CURRENT_TASK
    ldr r7, [r7]
    cmp r7, #0
    beq in_place
    ldr r5, [r6, #OWNER]
    cmp r5, #0
    beq claim
    cmp r5, r7
    bne in_place
    ldr r5, [r6, #RESUME_SP]
    cmp r5, #0
    beq in_place
    @ A game call re-entered the runtime: continue below the live runtime frames.
    @ TASK_SP must cover the live task frames whenever sp is on the text stack: interrupts run there.
    ldr r7, [r6, #TASK_SP]
    push {r7}
    mov r5, sp
    str r5, [r6, #TASK_SP]
    ldr r7, [r6, #RESUME_SP]
    mov sp, r7
    movs r7, #0
    str r7, [r6, #RESUME_SP]
    bl call_target
    ldr r6, =STATE
    mov r7, sp
    str r7, [r6, #RESUME_SP]
    mov sp, r5
    pop {r7}
    str r7, [r6, #TASK_SP]
    b done
claim:
    str r7, [r6, #OWNER]
    mov r5, sp
    str r5, [r6, #TASK_SP]
    ldr r7, =TEXT_STACK_TOP
    mov sp, r7
    bl call_target
    ldr r6, =STATE
    mov sp, r5
    movs r7, #0
    str r7, [r6, #OWNER]
    str r7, [r6, #TASK_SP]
    b done
in_place:
    bl call_target
done:
    pop {r4-r7}
    pop {r3}
    bx r3
call_target:
    bx r4
.size text_stack_enter, .-text_stack_enter

@ Calls the game function in STATE.TARGET with r0-r3, on the owner's task stack.
.global text_stack_out
.type text_stack_out, %function
.thumb_func
text_stack_out:
    push {r4-r7, lr}
    ldr r6, =STATE
    ldr r4, [r6, #TARGET]
    ldr r5, [r6, #OWNER]
    cmp r5, #0
    beq in_place
    ldr r7, =CURRENT_TASK
    ldr r7, [r7]
    cmp r5, r7
    bne in_place
    ldr r5, [r6, #RESUME_SP]
    cmp r5, #0
    bne in_place
    mov r5, sp
    str r5, [r6, #RESUME_SP]
    ldr r7, [r6, #TASK_SP]
    mov sp, r7
    bl call_target
    ldr r6, =STATE
    movs r7, #0
    str r7, [r6, #RESUME_SP]
    mov sp, r5
    b done
.size text_stack_out, .-text_stack_out
.ltorg

STACKED menu_translate
STACKED database_menu
STACKED text_engine_window_callback
STACKED text_engine_window_number
STACKED text_engine_release
STACKED_SITE message_run_table
STACKED_SITE message_print_number
STACKED_SITE message_print_pilot
STACKED_SITE message_print_zoid
STACKED_SITE message_print_unit
STACKED_SITE message_print_item
STACKED_SITE message_print_zi_item
STACKED_SITE message_print_item_effect
STACKED_SITE message_print_command
STACKED_SITE message_print_construction_part
.ltorg

@ Interrupt handlers run on the interrupted stack; on the text stack they use the owner task stack instead.
.equ TEXT_STACK_BOTTOM, 0x030075B0
.arm
.macro IRQ_STACKED name, handler
.global \name
.type \name, %function
\name:
    ldr r0, =\handler
    b text_stack_irq
.size \name, .-\name
.endm

IRQ_STACKED text_stack_irq_serial, 0x03001FE9
IRQ_STACKED text_stack_irq_vblank, 0x08001E0D
IRQ_STACKED text_stack_irq_hblank, 0x08001F6D
IRQ_STACKED text_stack_irq_timer3, 0x03001FE9

text_stack_irq:
    ldr r1, =STATE
    cmp sp, r1
    bxhi r0
    ldr r2, =TEXT_STACK_BOTTOM
    cmp sp, r2
    bxls r0
    ldr r2, [r1, #TASK_SP]
    cmp r2, #0
    bxeq r0
    mov r3, sp
    mov sp, r2
    push {r3, lr}
    mov lr, pc
    bx r0
    pop {r3, lr}
    mov sp, r3
    bx lr
.ltorg
