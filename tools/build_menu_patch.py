"""Build the English text ROM from the verified Japanese ROM."""

import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

import credits
from ds_rom import ROM_SHA256 as DS_ROM_SHA256, decompress_lz10, rom_files
from render_text import Font, ROOT, character_code
import text_core
from script_text import read_text
from rom import BASE, read_rom, run


TOKEN = re.compile(r"(\{[^}]+\}|\n|%[0-9]+\$s)")
ALIGNMENTS = {"left": 0, "center": 1, "right": 2}
HEX = re.compile(r"\{([0-9A-F]{2}(?: [0-9A-F]{2})*)\}")
# Text uses the shared core format; these bytes stay with Saga's glue.
GLUE_END, GLUE_PAGE, GLUE_FIELD, GLUE_PORTRAIT, GLUE_WINDOW, GLUE_NUMBER = 0x00, 0x0E, 0x10, 0x14, 0x1C, 0x1F
GLUE = (GLUE_END, GLUE_PAGE, GLUE_FIELD, GLUE_PORTRAIT, GLUE_WINDOW, GLUE_NUMBER)
SCRATCH = 0x03007B00
CONTROLS = re.compile(r"\{(?:window:[0-9A-F]{2}|number:[0-9A-F]{2}:[0-9A-F]{2}|player)\}")
WINDOW_LAYOUT = {
    0x6C035D: (9, 11), 0x6C0395: (13, 14), 0x6C8973: (10, 12),
    0x672E53: (8, 9), 0x673488: (8, 9),
    0x743FAD: (13, 14), 0x7443AC: (13, 14),
    0x755F89: (11, 13), 0x755F8B: (19, 17),
    # Leave room for four-digit HP and three-digit EP on every party row.
    0x04EBCC: (11, 9),
    # Keep the two-cell level value inside the narrower EXP panel.
    0x04E7AC: (15, 13), 0x04E8F0: (15, 13),
    # The rack selector includes the slot number, mount type, and weapon name.
    0x756925: (1, 0), 0x756927: (18, 30),
    0x756999: (16, 15), 0x75699B: (14, 15),
    0x7569A1: (16, 15), 0x7569A3: (14, 15),
    0x6C8A87: (7, 10), 0x6C8AEC: (8, 10),
    0x6C8B53: (8, 10), 0x6C8BBA: (12, 16),
}


def controls(text):
    return CONTROLS.findall(text)


def translation_controls(text):
    result = []
    previous_speaker = None
    for page in text.split("{page}"):
        speaker, separator, body = page.partition("\n")
        if separator and speaker == previous_speaker == "{player}":
            result.extend(controls(body))
        else:
            result.extend(controls(page))
        previous_speaker = speaker if separator else None
    return result


def imported_source(source, entry):
    bodies = []
    for part in entry["source"]:
        text = part["text"]
        if not text.startswith("{player}\n"):
            raise ValueError("An imported Athle line must be spoken by Athle.")
        bodies.append(text.split("\n", 1)[1])
    if not bodies:
        raise ValueError("An Athle import needs at least one DS source message.")
    text = "{player}\n" + "\n".join(bodies)
    if entry["position"] == "replace":
        if not source.startswith("{player}\n"):
            raise ValueError("Athle dialogue can replace only an Athle message.")
        return text
    if entry["position"] == "before":
        return text + "\n" + source
    if entry["position"] == "after":
        return source + "\n" + text
    raise ValueError("The Athle import has an invalid position.")


def pack_glyph(pixels):
    if len(pixels) != 128 or any(pixel > 15 for pixel in pixels):
        raise ValueError("A glyph must contain 128 four-bit pixels.")
    return bytes(pixels[i] | (pixels[i + 1] << 4) for i in range(0, 128, 2))


def glyphs(text):
    return b"".join(character_code(char).to_bytes(2, "big") for char in text)


def core_text(text):
    return b"".join(b"\x03" if part == "{player}" else glyphs(part)
                    for part in re.split(r"(\{player\})", text) if part)


def widest_player(engine):
    # Eight of the widest glyph, the worst case the old cell-based check also assumed.
    return engine.checker.widest_player


def line_width(engine, text):
    output = engine.layout(core_text(text) + b"\0", text_core.profile(
        0x7FFF, 16, face=text_core.TEXT_FACE_TALL, wrap=text_core.TEXT_WRAP_NONE), player=widest_player(engine))
    return output.state[0]


def wrap(engine, text, width):
    result = []
    line = ""
    for word in text.split(" "):
        candidate = line + (" " if line else "") + word
        if line and line_width(engine, candidate) > width:
            result.append(line)
            line = word
        else:
            line = candidate
        if line and line_width(engine, line) > width:
            raise ValueError(f"Word exceeds {width}px: {word!r}.")
    result.append(line)
    return "\n".join(result)


def unit_size(text, cursor):
    byte = text[cursor]
    if 0x80 <= byte <= 0x9F:
        return 2
    if byte in (0x01, 0x04, 0x06, GLUE_WINDOW, GLUE_FIELD):
        return 2
    if byte in (0x02, 0x07, 0x08, GLUE_NUMBER, GLUE_PORTRAIT):
        return 3
    return 1


def number_width(engine, count):
    # The runtime draws native numbers as fields of eight-pixel cells.
    return count * 8


def check_text_layout(engine, text, width, widths=None, rows=None, page_rows=None):
    widths = widths or {}
    rows = rows or {}
    window = None
    positions = {}
    cursor = 0
    while text[cursor] != GLUE_END:
        x, row, previous = positions.get(window, (0, 1, -1))
        limit = widths.get(window, width)
        control = text[cursor]
        row_limit = rows.get(window, page_rows)
        if control == GLUE_WINDOW:
            window = f"{text[cursor + 1]:02X}"
            cursor += 2
            continue
        if control == GLUE_PAGE:
            positions[window] = (0, 1, -1)
            cursor += 1
            continue
        if control in (GLUE_PORTRAIT, GLUE_FIELD):
            cursor += unit_size(text, cursor)
            continue
        if control == GLUE_NUMBER:
            x = (x + 7) // 8 * 8 + number_width(engine, min(text[cursor + 2] & 7, 5))
            if x > limit:
                raise ValueError(f"Combined text exceeds {limit}px in window {window}.")
            if row_limit is not None and row > row_limit:
                raise ValueError(f"Text page exceeds {row_limit} rows in window {window}.")
            positions[window] = (x, row, -1)
            cursor += 3
            continue
        end = cursor
        while text[end] not in GLUE:
            end += unit_size(text, end)
        profile = text_core.profile(limit, 0x7FFF, face=text_core.TEXT_FACE_TALL, wrap=text_core.TEXT_WRAP_NONE,
                                    alignment_bound=text_core.TEXT_BOUND_INK)
        output = engine.layout(text[cursor:end] + b"\0", profile, player=widest_player(engine),
                               initial=(x, 0, previous, 0))
        if output.status != "end":
            raise ValueError(f"Combined text exceeds {limit}px in window {window} ({output.status}).")
        if output.missing:
            raise ValueError(f"The font has no glyph for {', '.join(f'{code:04X}' for code in output.missing)}.")
        lines = [sum(1 for item in output.breaks if item["next_consumed"] <= glyph["consumed"])
                 for glyph in output.placements]
        if row_limit is not None and lines and row + max(lines) > row_limit:
            raise ValueError(f"Text page exceeds {row_limit} rows in window {window}.")
        positions[window] = (output.state[0], row + len(output.breaks), output.state[2])
        cursor = end


def dialogue_lines(engine, body, width):
    """Lines the runtime word wrap produces for one dialogue page body."""
    encoded = core_text(body)
    output = engine.layout(encoded + b"\0", text_core.profile(
        width, 0x7FFF, face=text_core.TEXT_FACE_TALL, wrap=text_core.TEXT_WRAP_WORD,
        overflow=text_core.TEXT_OVERFLOW_CLIP, alignment_bound=text_core.TEXT_BOUND_INK), player=widest_player(engine))
    if output.status != "end":
        raise ValueError(f"Dialogue does not fit {width}px ({output.status}).")
    # Count the words on each line and rebuild the lines from the original text.
    counts, start = [], 0
    for end in [item["consumed"] for item in output.breaks] + [len(encoded)]:
        counts.append(len(encoded[start:end].replace(b"\x81\x40", b" ").split()))
        start = next((item["next_consumed"] for item in output.breaks if item["consumed"] == end), end)
    words, lines = body.split(), []
    for count in counts:
        lines.append(" ".join(words[:count]))
        words = words[count:]
    if words:
        lines[-1] = " ".join([lines[-1], *words]).strip()
    return lines


def balance_widow(engine, body, width):
    """Moves a word down when the runtime wrap would leave one word alone on the last line."""
    if "{player}" in body:
        return body
    lines = dialogue_lines(engine, body, width)
    if len(lines) < 2 or len(lines[-1].split()) != 1 or len(lines[-2].split()) < 3:
        return body
    head = lines[-2].split()
    lines[-2:] = [" ".join(head[:-1]), head[-1] + " " + lines[-1]]
    if any(line_width(engine, line) > width for line in lines):
        return body
    return "\n".join(lines)


SENTENCE_END = re.compile(r"(?:[.!?~]|--)[\"')\]]*$")


def page_fits(engine, body, width, page_rows):
    return len(dialogue_lines(engine, body, width)) <= page_rows - 1


def pack(engine, pieces, width, page_rows):
    """Greedily fills pages with whole pieces; a piece that fits no page alone is returned for splitting."""
    pages, current = [], ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if page_fits(engine, candidate, width, page_rows):
            current = candidate
            continue
        if current:
            pages.append(current)
        current = piece
    if current:
        pages.append(current)
    return pages


def split_body(engine, body, width, page_rows):
    """Packs a dialogue run into pages at sentence ends, or returns None when a sentence needs a mid-page break."""
    sentences = re.split(r"(?<=[.!?])\s+", body)
    if not all(page_fits(engine, sentence, width, page_rows) for sentence in sentences):
        return None
    return pack(engine, sentences, width, page_rows)


def split_to_fit(engine, body, width, page_rows):
    """Splits one page that overflows at sentence ends, then clause ends, then between words."""
    if page_fits(engine, body, width, page_rows):
        return [body]
    pages = []
    for sentence in pack(engine, re.split(r"(?<=[.!?])\s+", body), width, page_rows):
        if page_fits(engine, sentence, width, page_rows):
            pages.append(sentence)
            continue
        for clause in pack(engine, re.split(r"(?<=[,;:])\s+|(?<=--)\s*", sentence), width, page_rows):
            pages.extend([clause] if page_fits(engine, clause, width, page_rows)
                         else pack(engine, clause.split(), width, page_rows))
    return pages


def repaginate(engine, text, portraits, width, page_rows):
    """Moves page breaks that fall inside a sentence to sentence or clause ends."""
    pages = text.split("{page}")
    selectors = portraits if portraits is not None else [None] * len(pages)
    result, chosen = [], []
    index = 0
    while index < len(pages):
        speaker, _, body = pages[index].partition("\n")
        run = [" ".join(body.split())]
        end = index + 1
        while (end < len(pages) and not SENTENCE_END.search(run[-1])
               and pages[end].partition("\n")[0] == speaker and selectors[end] == selectors[index]):
            run.append(" ".join(pages[end].partition("\n")[2].split()))
            end += 1
        bodies = (len(run) > 1 and split_body(engine, " ".join(run), width, page_rows)) or run
        bodies = [part for body in bodies for part in split_to_fit(engine, body, width, page_rows)]
        result.extend(f"{speaker}\n{body}" for body in bodies)
        chosen.extend([selectors[index]] * len(bodies))
        index = end
    return "{page}".join(result), chosen if portraits is not None else None


def encode(engine, text, width, auto_wrap=False, widths=None, page_rows=None, rows=None, fields=0,
           template=False, portraits=None, reflow=False):
    if reflow:
        pages = []
        for body in text.split("{page}"):
            speaker, separator, body = body.partition("\n")
            if not separator:
                raise ValueError("Reflowed dialogue needs a speaker header on every page.")
            body = balance_widow(engine, " ".join(body.split()), width)
            lines = body.split("\n") if "\n" in body else dialogue_lines(engine, body, width)
            if page_rows is not None and len(lines) + 1 > page_rows:
                raise ValueError(f"Text page exceeds {page_rows} rows in window None.")
            pages.append(speaker + "\n" + body)
        text = "{page}".join(pages)
    encoded = bytearray()
    page = 0
    if portraits is not None:
        if page_rows != 3 or not isinstance(portraits, list) or len(portraits) != text.count("{page}") + 1:
            raise ValueError("Dialogue needs one portrait selector per page and three text rows.")
        for selector in portraits:
            if selector is not None and (not isinstance(selector, list) or len(selector) != 2
                    or any(type(value) is not int for value in selector)
                    or not 0 <= selector[0] < 255 or not 0 <= selector[1] < 9):
                raise ValueError("A portrait selector needs a character and an expression, or null.")
        encoded.extend((GLUE_PORTRAIT, *(portraits[page] or [0xFF, 0])))
    default_width = width
    widths = widths or {}
    region = False
    # Each runtime span starts at row 0, and anchors count rows from there.
    row = 0
    for part in TOKEN.split(text):
        if not part:
            continue
        if re.fullmatch(r"%[0-9]+\$s", part):
            field = int(part[1:-2])
            if not 1 <= field <= fields:
                raise ValueError(f"Template field {part} is outside 1-{fields}.")
            encoded.extend((GLUE_FIELD, field))
            row = 0
        elif part == "\n":
            encoded.append(0x0A)
            region = False
            row += 1
        elif part == "{page}":
            if page_rows is None:
                raise ValueError("Page breaks require a dialogue row limit.")
            encoded.append(GLUE_PAGE)
            region = False
            row = 0
            page += 1
            if portraits is not None:
                encoded.extend((GLUE_PORTRAIT, *(portraits[page] or [0xFF, 0])))
        elif part == "{player}":
            encoded.append(0x03)
        elif re.fullmatch(r"\{window:[0-7][0-9A-F]\}", part):
            window = int(part[8:-1], 16)
            if window >= 8:
                raise ValueError("The text window must be between zero and seven.")
            encoded.extend((GLUE_WINDOW, window))
            width = widths.get(f"{window:02X}", default_width)
            region = False
            row = 0
        elif re.fullmatch(r"\{number:[0-9A-F]{2}:[0-9A-F]{2}\}", part):
            index, format_byte = (int(value, 16) for value in part[8:-1].split(":"))
            if index >= 8:
                raise ValueError("The number slot must be between zero and seven.")
            encoded.extend((GLUE_NUMBER, index, format_byte))
            row = 0
        elif re.fullmatch(r"\{align:(left|center|right)\}", part):
            encoded.extend((0x04, ALIGNMENTS[part[7:-1]]))
        elif re.fullmatch(r"\{region:[0-9]+:[0-9]+\}", part):
            left, right = (int(value) for value in part[8:-1].split(":"))
            if not left < right <= min(width, 255):
                raise ValueError("A text region must fit the window and its one-byte arguments.")
            encoded.extend((0x08, left, right))
            region = True
        elif re.fullmatch(r"\{x:[0-9]+\}", part):
            position = int(part[3:-1])
            if position >= width or position % 8:
                raise ValueError("A text position must be a whole cell inside the window.")
            encoded.extend((0x07, position // 8, row))
            region = False
        elif re.fullmatch(r"\{anchor:[0-9]+:[0-9]+\}", part):
            x, y = (int(value) for value in part[8:-1].split(":"))
            encoded.extend((0x07, x, y))
        elif re.fullmatch(r"\{pad:[0-9]+\}", part):
            padding = int(part[5:-1])
            if padding > min(width, 255):
                raise ValueError("Padding must fit the window and its one-byte argument.")
            encoded.extend((0x06, padding))
        elif HEX.fullmatch(part):
            encoded.extend(bytes.fromhex(part[1:-1]))
        elif "{" in part or "}" in part:
            raise ValueError(f"Unknown text token: {part!r}.")
        else:
            lines = wrap(engine, part, width).split("\n") if auto_wrap and not reflow else [part]
            for i, line in enumerate(lines):
                if i:
                    encoded.append(0x0A)
                    region = False
                    row += 1
                encoded.extend(glyphs(line))
    encoded.append(GLUE_END)
    if not (fields or template or reflow):
        check_text_layout(engine, bytes(encoded), default_width, widths, rows, page_rows)
    return bytes(encoded)


def message_template(key, template, entries):
    sequence = template.get("sequence")
    if (not isinstance(sequence, list) or not 1 <= len(sequence) <= 6
            or any(item != "*" and item not in entries for item in sequence)):
        raise ValueError(f"{key}: a template sequence must list up to six records or * fields.")
    count = sequence.count("*")
    if count > 3:
        raise ValueError(f"{key}: a template can have at most three * fields.")
    fields = [int(field) for field in re.findall(r"%([0-9]+)\$s", template["text"])]
    if sorted(fields) != list(range(1, count + 1)):
        raise ValueError(f"{key}: the template must use each field from %1$s to %{count}$s once.")
    if controls(template["text"]) or "{page}" in template["text"]:
        raise ValueError(f"{key}: a template cannot contain window, number, or page controls.")
    return sequence, count


def check_layout(engine, entry):
    parts = re.split(r"(\{window:[0-9A-F]{2}\})", entry["text"])
    for index in range(1, len(parts), 2):
        window = parts[index][8:-1]
        limit = entry.get("rows", {}).get(window)
        if limit is None:
            continue
        width = entry.get("widths", {}).get(window, entry["width"])
        count = sum(len(wrap(engine, line, width).split("\n")) for line in parts[index + 1].split("\n"))
        if count > limit:
            raise ValueError(f"Window {window} needs {count} rows; only {limit} fit.")
    encode(engine, entry["text"], entry["width"], entry.get("wrap", False), entry.get("widths"),
           entry.get("page_rows"), entry.get("rows"), portraits=entry.get("portraits"),
           reflow=entry.get("page_rows") == 3)


def preceding_window_width(rom, offset):
    if offset < 8 or rom[offset - 8] != 1:
        return None
    window, style, x, y, width, height, flags = rom[offset - 7:offset]
    if window >= 8 or x + width > 30 or y + height > 20 or flags > 7:
        return None
    if width < 5 or height < 4 or style & 15 not in (0, 1, 4, 8):
        return None
    return (width - (4 if style & 1 else 2)) * 8


def metric_index(codes):
    if len(codes) > 255:
        raise ValueError("The metric index holds at most 255 glyphs.")
    low = codes[0]
    table = bytearray(codes[-1] - low + 1)
    for position, code in enumerate(codes):
        table[code - low] = position + 1
    return struct.pack("<HH", low, len(table)) + bytes(table)


def pair_starts(codes, pairs):
    previous = sorted(int(key.split(":")[0], 16) for key in pairs)
    return struct.pack(f"<{len(codes) + 1}H", *(bisect.bisect_left(previous, code) for code in codes),
                       len(previous))


def append(data, block, alignment=4):
    data.extend(bytes((-len(data)) % alignment))
    pointer = BASE + len(data)
    data.extend(block)
    return pointer


def command_editor_layout(rom, result):
    table = bytearray(rom[0x675BEC:0x675BEC + 18 * 4])
    for index, offset, original, replacement in (
        (2, 0x6734C9, "01012000101e0400040122", "010120000e1e0600040122"),
        (16, 0x672E8B, "01022100010f0e0422", "01022100000f0e0422"),
        (17, 0x672E94, "0103210f010f0e0422", "0103210f000f0e0422"),
    ):
        expected = bytes.fromhex(original)
        if (rom[offset:offset + len(expected)] != expected
                or struct.unpack_from("<I", table, index * 4)[0] != BASE + offset):
            raise ValueError("The command editor window script does not match the original ROM.")
        struct.pack_into("<I", table, index * 4, append(result, bytes.fromhex(replacement)))
    if struct.unpack_from("<I", rom, 0x3B114)[0] != BASE + 0x675BEC:
        raise ValueError("The command editor script table does not match the original ROM.")
    struct.pack_into("<I", result, 0x3B114, append(result, table))


MESSAGE_HOOKS = {
    0x0803E488: ("message_run_table", "00b5031c0804000c"),
    0x08001848: ("message_print_number", "f0b557464e464546"),
    0x08032818: ("message_print_pilot", "00b50006010e0029"),
    0x08032800: ("message_print_zoid", "00b5011c0906090e"),
    0x080339C4: ("message_print_unit", "10b5041ca07bfef7"),
    0x08032840: ("message_print_item", "00b5011c0906090e"),
    0x08032858: ("message_print_zi_item", "00b5011c0906090e"),
    0x08032870: ("message_print_item_effect", "00b5011c0906090e"),
    0x08032888: ("message_print_command", "00b5011c0906090e"),
    0x080328E4: ("message_print_construction_part", "00b5011c0906090e"),
}
MESSAGE_ROLES = {"parts": 1, "fields": 2}
MESSAGE_TABLE_HOOKS = {0x0803E488, 0x08032840, 0x08032858, 0x08032870, 0x08032888, 0x080328E4}
# Decompression and memory copies stay with the emulator's own BIOS.
BIOS_STUBS = {
    0x0805D280: ("swi_Div", 0xDF06), 0x0805D290: ("swi_ObjAffineSet", 0xDF0F),
}
# print_signed_number is replaced at its entry, so its old body holds the BIOS veneers.
BIOS_VENEERS = (0x01850, 0x0196C)


def bl_target(rom, offset):
    high, low = struct.unpack_from("<HH", rom, offset)
    if high & 0xF800 != 0xF000 or low & 0xF800 != 0xF800:
        return None
    displacement = ((high & 0x7FF) << 12) | ((low & 0x7FF) << 1)
    if displacement & 0x400000:
        displacement -= 0x800000
    return BASE + offset + 4 + displacement


def encode_bl(offset, target):
    displacement = target - (BASE + offset + 4)
    if displacement & 1 or not -(1 << 22) <= displacement < (1 << 22):
        raise ValueError(f"Thumb BL target is out of range at {BASE + offset:08X}.")
    return struct.pack("<HH", 0xF000 | ((displacement >> 12) & 0x7FF), 0xF800 | ((displacement >> 1) & 0x7FF))


def bios_call_sites(rom):
    """Find every Thumb call to a BIOS stub outside literal pools."""
    sites = []
    for line in (ROOT / "regions.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[1].startswith("0x") or fields[-1].startswith("gcc-arm"):
            continue
        start, end = int(fields[1], 16), int(fields[2], 16)
        pools = set()
        for offset in range(start, end - 1, 2):
            halfword = struct.unpack_from("<H", rom, offset)[0]
            if halfword & 0xF800 == 0x4800:
                pool = ((offset + 4) & ~3) + (halfword & 0xFF) * 4
                pools.update(range(pool, pool + 4))
        offset = start
        while offset + 4 <= end:
            if offset in pools:
                offset += 2
                continue
            target = bl_target(rom, offset)
            if target is None:
                offset += 2
                continue
            if target in BIOS_STUBS:
                if offset + 2 in pools:
                    raise ValueError(f"A BIOS call at {BASE + offset:08X} overlaps a literal pool.")
                sites.append((offset, target))
            offset += 4
    for offset in range(0, len(rom) - 3, 4):
        if struct.unpack_from("<I", rom, offset)[0] & ~1 in BIOS_STUBS:
            raise ValueError(f"A pointer to a BIOS stub at {BASE + offset:08X} cannot be redirected.")
    return sites


def replace_bios(rom, result, symbols):
    start, end = BIOS_VENEERS
    if 0x08001848 not in MESSAGE_HOOKS:
        raise ValueError("The BIOS veneers need print_signed_number to stay hooked.")
    if len(BIOS_STUBS) * 12 > end - start:
        raise ValueError("The BIOS veneers do not fit.")
    veneers = {}
    for index, (stub, (name, swi)) in enumerate(sorted(BIOS_STUBS.items())):
        if struct.unpack_from("<HH", rom, stub - BASE) != (swi, 0x4770):
            raise ValueError(f"The {name} stub does not match the original ROM.")
        offset = start + index * 12
        target = int(next(line.split()[0] for line in symbols.splitlines() if line.endswith(" " + name)), 16)
        struct.pack_into("<HHII", result, offset, 0x4778, 0x46C0, 0xE51FF004, target)
        veneers[stub] = BASE + offset
    sites = bios_call_sites(rom)
    for offset, stub in sites:
        result[offset:offset + 4] = encode_bl(offset, veneers[stub])
    return len(sites)


def message_sites(rom, groups):
    sites = {}
    for name, group in groups.items():
        for role, addresses in group.items():
            for address in addresses:
                offset = int(address, 16) - BASE
                target = bl_target(rom, offset)
                if target not in MESSAGE_HOOKS:
                    raise ValueError(f"{name}: {address} is not a call to a message printer.")
                if role in ("parts", "closers") and target not in MESSAGE_TABLE_HOOKS:
                    raise ValueError(f"{name}: {address} must run a text script.")
                if role == "closers":
                    continue
                if role not in MESSAGE_ROLES:
                    raise ValueError(f"{name}: unknown site role {role!r}.")
                if offset + 4 + BASE in sites:
                    raise ValueError(f"{name}: {address} is listed twice.")
                sites[offset + 4 + BASE] = MESSAGE_ROLES[role]
    return b"".join(struct.pack("<IBBH", call, role, 0, 0) for call, role in sorted(sites.items()))


def king_portraits(ds_rom):
    if hashlib.sha256(ds_rom).hexdigest() != DS_ROM_SHA256:
        raise ValueError("The portrait source must be Zoids Saga DS (Japan).")
    names = {f"face/fc012{expression}_narc_LZ.bin" for expression in "ab"}
    files = {file.name: file.data for file in rom_files(ds_rom) if file.name in names}
    portraits = []
    for name in sorted(names):
        archive = decompress_lz10(files[name])
        if archive[:4] != b"NARC" or struct.unpack_from("<H", archive, 24)[0] != 2:
            raise ValueError("The King's portrait archive does not match its source layout.")
        cursor = 16
        blocks = {}
        for _ in range(3):
            size = struct.unpack_from("<I", archive, cursor + 4)[0]
            blocks[archive[cursor:cursor + 4]] = archive[cursor:cursor + size]
            cursor += size
        bounds = struct.unpack_from("<4I", blocks[b"BTAF"], 12)
        payload = blocks[b"GMIF"][8:]
        tiles = payload[bounds[0]:bounds[1]]
        palette = payload[bounds[2]:bounds[3]]
        if (len(tiles) != 1200 or tiles[:4] != b"RGCN" or tiles[16:20] != b"RAHC"
                or len(palette) != 90 or palette[:4] != b"RLCN" or palette[16:20] != b"TTLP"):
            raise ValueError("The King's portrait needs 36 four-bit tiles and a 16-color palette.")
        # DS fc012a/b use the GBA's four-sprite layout: 32x32, 32x16, 16x32, and 16x16.
        portraits.append((tiles[48:], palette[40:72]))
    return portraits


def import_king_portraits(rom, result, portraits):
    def compressed(data):
        return (bytes([0x10]) + len(data).to_bytes(3, "little")
                + b"".join(b"\0" + data[i:i + 8] for i in range(0, len(data), 8)))

    table = bytearray(rom[0x6D08C0:0x6D39B0])
    frames = []
    for tiles, palette in portraits:
        frames.append(struct.pack("<4I", append(result, compressed(tiles)), append(result, compressed(palette)),
                                  0x083ED2D0, 0x083ED2C4))
    for expression in range(9):
        table.extend(frames[1 if expression == 1 else 0])
    pointer = append(result, table)
    for offset in (0x40574, 0x406D4):
        if struct.unpack_from("<I", rom, offset)[0] != BASE + 0x6D08C0:
            raise ValueError("The original portrait table reference does not match.")
        struct.pack_into("<I", result, offset, pointer)
    # Full-width dialogue must close a face added by the translated message.
    for index, offset, original, replacement in (
            (31, 0x70666B, "010110000c1e080104ff22", "0300010110000c1e080104ff22"),
            (34, 0x706682, "0500030122", "05000300030122")):
        if (rom[offset:offset + len(bytes.fromhex(original))] != bytes.fromhex(original)
                or struct.unpack_from("<I", rom, 0x74FA90 + index * 4)[0] != BASE + offset):
            raise ValueError("The original full-width dialogue script does not match.")
        struct.pack_into("<I", result, 0x74FA90 + index * 4, append(result, bytes.fromhex(replacement)))


def fix_dialogue_pages(rom, result):
    slot = 0x74FA90 + 1017 * 4
    if struct.unpack_from("<I", rom, slot)[0]:
        raise ValueError("The extra dialogue script slot is not empty.")
    script = append(result, bytes.fromhex("21200021230022"))
    struct.pack_into("<I", result, slot, script)
    for source in (0x7437BE, 0x7437DD):
        if rom[source - 12:source] != bytes.fromhex("09000200090104000b212300"):
            raise ValueError("The final battle's speaker transition does not match.")
        # Wait and clear before the native portrait reload, while the previous face is still visible.
        struct.pack_into("<H", result, source - 2, 1017)
    result[0x7437DD - 10] = 3


def build(rom, entries, imports=None, portraits=None):
    choices = json.loads((ROOT / "kerning-choices.json").read_text())
    font = Font(rom, choices)
    engine = text_core.TextEngine(rom, choices)
    translations = []
    imports = imports or {}
    if imports.keys() - entries.keys():
        raise ValueError("An imported Athle scene has no English translation.")
    layout_rom = bytearray(rom)
    if rom[0x706676:0x70667B] != b"\x05\x00\x0e\x01\x22":
        raise ValueError("The dialogue page script does not match the original ROM.")
    for offset, (before, after) in WINDOW_LAYOUT.items():
        if rom[offset] != before:
            raise ValueError(f"The window layout does not match at {offset:06X}.")
        layout_rom[offset] = after
    for key, entry in sorted(entries.items(), key=lambda item: int(item[0], 16)):
        offset = int(key, 16)
        source = read_text(rom, offset)
        source_text = imported_source(source.text, imports[key]) if key in imports else source.text
        if controls(source_text) != translation_controls(entry["text"]):
            raise ValueError(f"Translation changes script controls at {key}.")
        if "full_text" in entry and controls(source_text) != translation_controls(entry["full_text"]):
            raise ValueError(f"Full translation changes script controls at {key}.")
        try:
            window_width = preceding_window_width(layout_rom, offset)
            width = min(entry["width"], window_width or entry["width"])
            dialogue, selectors = entry["text"], entry.get("portraits")
            if entry.get("page_rows") == 3:
                dialogue, selectors = repaginate(engine, dialogue, selectors, width, 3)
            text = encode(engine, dialogue, width,
                          entry.get("wrap", False), entry.get("widths"), entry.get("page_rows"), entry.get("rows"),
                          portraits=selectors, reflow=entry.get("page_rows") == 3)
        except ValueError as error:
            raise ValueError(f"{key}: {error}") from error
        compact = encode(engine, entry["compact"], 64) if "compact" in entry else None
        message = []
        for template in entry.get("templates", []):
            sequence, fields = message_template(key, template, entries)
            try:
                message.append((encode(engine, template["text"], width, fields=fields, template=True), sequence))
            except ValueError as error:
                raise ValueError(f"{key}: {error}") from error
        translations.append((offset, source.end, text, compact, message))

    result = layout_rom
    fix_dialogue_pages(rom, result)
    if portraits is not None:
        import_king_portraits(rom, result, portraits)
    elif any(selector and selector[0] == 87 for entry in entries.values() for selector in entry.get("portraits", [])):
        raise ValueError("The King's dialogue requires the DS portrait source.")
    sites = message_sites(rom, json.loads((ROOT / "message-sites.json").read_text()))
    sites_pointer = append(result, sites)
    site_count_pointer = append(result, struct.pack("<I", len(sites) // 8))
    count_pointer = append(result, struct.pack("<I", len(translations)))
    records_pointer = append(result, bytes(len(translations) * 20))
    for index, (start, end, text, compact, message) in enumerate(translations):
        pointer = append(result, text, 2)
        compact_pointer = append(result, compact, 2) if compact else 0
        message_pointer = 0
        if message:
            table = bytearray()
            for template, sequence in message:
                items = [0 if item == "*" else BASE + int(item, 16) for item in sequence]
                table.extend(struct.pack("<II6I", append(result, template, 2), len(items),
                                         *items, *[0] * (6 - len(items))))
            message_pointer = append(result, bytes(table) + bytes(32))
        struct.pack_into("<IIIII", result, records_pointer - BASE + index * 20,
                         BASE + start, BASE + end, pointer, compact_pointer, message_pointer)

    database_sources = [0] * 256
    if rom[0x6F976A:0x6F9772] != bytes.fromhex("09050000201c001d"):
        raise ValueError("The unknown database entry does not match the original ROM.")
    database_id = 0
    for start, _, _, _, _ in translations:
        if not 0x6F9772 <= start < 0x70067C:
            continue
        if rom[start - 4:start - 2] == b"\x09\x05":
            database_id = struct.unpack_from("<H", rom, start - 2)[0]
        if rom[start + 1:start + 3] == b"\x1C\x02":
            if database_id is None or database_id >= 256 or database_sources[database_id]:
                raise ValueError("The database description selector does not match the original ROM.")
            database_sources[database_id] = BASE + start
    database_pointer = append(result, struct.pack("<256I", *database_sources))

    command_editor_layout(rom, result)
    credit_scenes, credit_animations, credit_frames = credits.build(rom, font, lambda block: append(result, block))
    struct.pack_into("<II", result, credits.DEFINITION + 8, credit_animations, credit_frames)

    packed = text_core.pack_font(engine.data)
    font_tables = {
        "text_font_metrics": append(result, packed["metrics"]),
        "text_font_ranges": append(result, packed["ranges"]),
        "text_font_pairs": append(result, packed["pairs"] or bytes(6)),
        "text_font_pair_offsets": append(result, packed["pair_offsets"]),
        "text_font_triplets": append(result, packed["triplets"] or bytes(6)),
        "text_font_triplet_offsets": append(result, packed["triplet_offsets"]),
        "text_glyph_origins": append(result, bytes(value for origin in engine.data["origins"] for value in origin)),
    }
    font_source = (
        '#include "text_core.h"\n'
        "extern const TextMetric text_font_metrics[];\nextern const TextRange text_font_ranges[];\n"
        "extern const TextPair text_font_pairs[];\nextern const uint16_t text_font_pair_offsets[];\n"
        "extern const TextPair text_font_triplets[];\nextern const uint16_t text_font_triplet_offsets[];\n"
        "const TextFont text_font = {text_font_metrics, %d, text_font_ranges, %d, text_font_pairs, %d,\n"
        "                            text_font_pair_offsets, 0, 4, %d, %d,\n"
        "                            text_font_triplets, %d, text_font_triplet_offsets};\n"
        % (len(engine.data["metrics"]), len(engine.data["ranges"]), len(engine.data["pairs"]),
           engine.data["tall_fallback"], engine.data["compact_fallback"], len(engine.data["triplets"])))

    with tempfile.TemporaryDirectory(prefix="saga-menu-build-") as directory:
        work = Path(directory)
        objects = []
        (work / "font_data.c").write_text(font_source)
        sources = [ROOT / "src" / name for name in (
            "menu_text_hook.s", "menu_translate.c", "initialize_player_name.c", "text_engine.c", "text_core.c",
            "text_memory.s", "text_stack.s", "original_window_callback.s", "database_menu_hook.s", "credits_scene.c",
            "bios/bios.s")] + [work / "font_data.c"]
        for path in sources:
            output = work / (path.parent.name + "_" + path.name + ".o")
            run(["arm-none-eabi-gcc", "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork",
                 "-Os", "-ffreestanding", "-fno-builtin", "-fno-jump-tables", "-fno-unwind-tables",
                 "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra", "-Werror",
                 f"-DTEXT_RUNTIME_SCRATCH=0x{SCRATCH:08X}",
                 "-I", str(ROOT / "src"), "-c", str(path), "-o", str(output)])
            objects.append(str(output))
        result.extend(bytes((-len(result)) % 4))
        linker = work / "patch.ld"
        linker.write_text(f"database_descriptions = 0x{database_pointer:08X};\n"
                          f"menu_records = 0x{records_pointer:08X};\n"
                          f"menu_record_count = 0x{count_pointer:08X};\n"
                          f"message_sites = 0x{sites_pointer:08X};\n"
                          f"message_site_count = 0x{site_count_pointer:08X};\n"
                          + "".join(f"{name} = 0x{pointer:08X};\n" for name, pointer in font_tables.items()) +
                          f"credit_scenes = 0x{credit_scenes:08X};\n"
                          "text_windows = 0x020075A0;\ntext_tile_buffer = 0x02009040;\n"
                          "text_tile_usage = 0x0200904B;\nSECTIONS {\n"
                          f".patch 0x{BASE + len(result):08X} : {{ *(.text .text.* .rodata .rodata.*) }}\n"
                          "/DISCARD/ : { *(.ARM.* .comment .note*) }\n}\n")
        elf = work / "patch.elf"
        binary = work / "patch.bin"
        run(["arm-none-eabi-ld", "--entry=menu_text_hook", "-T", str(linker), *objects, "-o", str(elf)])
        run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".patch", str(elf), str(binary)])
        symbols = subprocess.run(["arm-none-eabi-nm", str(elf)], check=True,
                                 capture_output=True, text=True).stdout
        hook = int(next(line.split()[0] for line in symbols.splitlines()
                        if line.endswith(" menu_text_hook")), 16) & ~1
        initializer = int(next(line.split()[0] for line in symbols.splitlines()
                               if line.endswith(" initialize_player_name")), 16) | 1
        renderer = int(next(line.split()[0] for line in symbols.splitlines()
                            if line.endswith(" stacked_text_engine_window_callback")), 16) | 1
        release = int(next(line.split()[0] for line in symbols.splitlines()
                           if line.endswith(" stacked_text_engine_release")), 16) | 1
        database_hook = int(next(line.split()[0] for line in symbols.splitlines()
                                 if line.endswith(" database_menu_hook")), 16) | 1
        result.extend(binary.read_bytes())
    replace_bios(rom, result, symbols)
    for address, (name, original) in MESSAGE_HOOKS.items():
        offset = address - BASE
        if rom[offset:offset + 8] != bytes.fromhex(original):
            raise ValueError(f"The {name} hook does not match the original ROM.")
        target = int(next(line.split()[0] for line in symbols.splitlines() if line.endswith(" stacked_" + name)), 16)
        struct.pack_into("<HHI", result, offset, 0x4B00, 0x4718, target | 1)
    if rom[0x4341E:0x43428] != bytes.fromhex("03480221fbf731f8b480"):
        raise ValueError("The database menu call does not match the original ROM.")
    struct.pack_into("<HHHI", result, 0x4341E, 0x4801, 0x4700, 0x46C0, database_hook)
    if rom[0x41498:0x414A0] != bytes.fromhex("f0b50006000e0022"):
        raise ValueError("The window tile cleanup does not match the original ROM.")
    struct.pack_into("<HHI", result, 0x41498, 0x4B00, 0x4718, release)
    credit_scene = int(next(line.split()[0] for line in symbols.splitlines()
                            if line.endswith(" credits_scene")), 16) | 1
    struct.pack_into("<HHI", result, credits.SCENE_FUNCTION, 0x4B00, 0x4718, credit_scene)
    if rom[0x3E250:0x3E260] != bytes.fromhex("f0b5474680b481b00006060e0906090e"):
        raise ValueError("The window callback does not match the original ROM.")
    struct.pack_into("<6HI", result, 0x3E250, 0xB408, 0x4B02, 0x469C, 0xBC08, 0x4760, 0x46C0, renderer)
    if rom[0x40F44:0x40F4C] != bytes.fromhex("f0b557464e464546"):
        raise ValueError("The window number printer does not match the original ROM.")
    # Keep r3, the format argument: push {r3,lr}; ldr r3,=target; str r3,[sp,#4]; pop {r3,pc}.
    window_number = int(next(line.split()[0] for line in symbols.splitlines()
                             if line.endswith(" stacked_text_engine_window_number")), 16) | 1
    struct.pack_into("<HHHHI", result, 0x40F44, 0xB508, 0x4B01, 0x9301, 0xBD08, window_number)
    # Maximums after a slash: movs r2,#0 becomes movs r2,#8 so "85/1200" keeps every column in place.
    for site in (0x4EBFE, 0x4EC3A, 0x4F114, 0x4F14C, 0x526D8, 0x52710, 0x57EF0, 0x57F50, 0x57FA6, 0x58004):
        if rom[site - 2:site] != b"\x00\x22" or bl_target(rom, site) != 0x08001848:
            raise ValueError(f"The maximum value call at {site:06X} does not match the original ROM.")
        result[site - 2] = 8
    # Interrupt handlers that do work get a veneer that keeps them off the text stack.
    table = 0x663BA8
    for slot, (name, handler) in {0: ("serial", 0x03001FE9), 1: ("vblank", 0x08001E0D),
                                  3: ("hblank", 0x08001F6D), 7: ("timer3", 0x03001FE9)}.items():
        if struct.unpack_from("<I", rom, table + slot * 4)[0] != handler:
            raise ValueError(f"The {name} interrupt handler does not match the original ROM.")
        veneer = int(next(line.split()[0] for line in symbols.splitlines()
                          if line.endswith(" text_stack_irq_" + name)), 16)
        struct.pack_into("<I", result, table + slot * 4, veneer)
    if struct.unpack_from("<I", rom, 0x3E288)[0] != 0x0803E29C:
        raise ValueError("The glyph drawing branch does not match the original ROM.")
    struct.pack_into("<I", result, 0x3E288, 0x0803E43C)
    for offset, before, after in ((0x4BE62, b"\x01\x20", b"\x03\x20"),
                                  (0x4BE68, b"\x04\x20", b"\x05\x20"),
                                  (0x4CA94, b"\x1b\x20", b"\x1d\x20")):
        if rom[offset:offset + 2] != before:
            raise ValueError(f"The name entry instruction does not match at {offset:06X}.")
        result[offset:offset + 2] = after
    if rom[0x4BCDC:0x4BCE4] != b"\xf0\xb5\x47\x46\x80\xb4\x00\x23":
        raise ValueError("The player name initializer does not match the original ROM.")
    struct.pack_into("<HHI", result, 0x4BCDC, 0x4B00, 0x4718, initializer)
    switch = 0x3E4E0 + 0x20 * 4
    if struct.unpack_from("<I", rom, switch)[0] != 0x080400EA:
        raise ValueError("The text command does not match the original switch table.")
    struct.pack_into("<I", result, switch, hook)
    if len(result) > 0x2000000:
        raise ValueError("The patch exceeds the GBA ROM address space.")
    return result, len(engine.data["metrics"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ds-rom", type=Path,
                        default=ROOT / "0235 - Zoids Saga DS - Legend of Arcadia (Japan).nds")
    args = parser.parse_args()
    if args.rom.resolve() == args.output.resolve():
        parser.error("Use a separate output ROM.")
    entries = json.loads((ROOT / "menu-en.json").read_text())
    story = json.loads((ROOT / "story-en.json").read_text())
    if entries.keys() & story.keys():
        raise ValueError("Menu and story translations contain the same source offset.")
    entries.update(story)
    imports = json.loads((ROOT / "athle-ds-imports.json").read_text())
    result, glyphs = build(read_rom(args.rom), entries, imports, king_portraits(args.ds_rom.read_bytes()))
    args.output.write_bytes(result)
    print(f"Built {len(entries)} translated records with {glyphs} font glyphs: {args.output}")


if __name__ == "__main__":
    main()
