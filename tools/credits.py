"""Render the English staff roll as per-scene sprite graphics."""

import json
import struct

from render_text import ROOT


DEFINITION = 0x6F7FD0 + 0x7F * 16
ORIGINAL_SCENE = bytes.fromhex("f0b557464e464546")
SCENE_FUNCTION = 0x4394C
TILE_LIMIT = 708
INK = 5
OUTLINE = 6
SHADES = {2: 2, 3: 3, 4: 4}
NEIGHBORS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
COPYRIGHT = (
    "...#####...",
    "..#.....#..",
    ".#.......#.",
    "#...####..#",
    "#..#......#",
    "#..#......#",
    "#..#......#",
    "#..#......#",
    "#...####..#",
    ".#.......#.",
    "..#.....#..",
    "...#####...",
)


def text_rows(font, text):
    parts = text.split("©")
    rows = [bytearray(row) for row in font.line(parts[0])] if parts[0] else [bytearray() for _ in range(16)]
    for part in parts[1:]:
        for y, row in enumerate(rows):
            mark = COPYRIGHT[y - 1] if 1 <= y <= len(COPYRIGHT) else "." * len(COPYRIGHT[0])
            row.extend(15 if pixel == "#" else 1 for pixel in mark + ".")
        if part:
            for row, extra in zip(rows, font.line(part)):
                row.extend(extra)
    return rows


def render(font, text, limit):
    rows = text_rows(font, text)
    if len(rows[0]) > limit:
        raise ValueError(f"The staff roll line {text!r} is wider than {limit} pixels.")
    blank = [0] * (len(rows[0]) + 2)
    ink = [blank] + [[0] + [SHADES.get(pixel, INK) if pixel != 1 else 0 for pixel in row] + [0]
                     for row in rows] + [blank] * 7
    bitmap = [row[:] for row in ink]
    for y, row in enumerate(ink):
        for x, pixel in enumerate(row):
            if not pixel and any(0 <= y + dy < len(ink) and 0 <= x + dx < len(row) and ink[y + dy][x + dx]
                                 for dx, dy in NEIGHBORS):
                bitmap[y][x] = OUTLINE
    return bitmap


def chunk_tiles(bitmap, left, top, height):
    data = bytearray()
    for tile_row in range(height // 8):
        for tile_column in range(4):
            for row in range(8):
                line = bitmap[top + tile_row * 8 + row]
                for column in range(0, 8, 2):
                    x = left + tile_column * 8 + column
                    first = line[x] if x < len(line) else 0
                    second = line[x + 1] if x + 1 < len(line) else 0
                    data.append(first | second << 4)
    return bytes(data)


def build(rom, font, append):
    if rom[SCENE_FUNCTION:SCENE_FUNCTION + 8] != ORIGINAL_SCENE:
        raise ValueError("The staff roll scene function does not match the original ROM.")
    scenes = json.loads((ROOT / "credits-en.json").read_text())
    piece_lists = []
    scene_records = []
    for lines in scenes:
        if not 1 <= len(lines) <= 10:
            raise ValueError("A staff roll scene needs one to ten lines.")
        tiles = bytearray()
        chunks = {}
        first = len(piece_lists)
        for index, text in enumerate(lines):
            bitmap = render(font, text, 232 if index == 0 else 224)
            pieces = bytearray()
            for left in range(0, len(bitmap[0]), 32):
                for top, height in ((0, 16), (16, 8)):
                    data = chunk_tiles(bitmap, left, top, height)
                    if not any(data):
                        continue
                    if data not in chunks:
                        chunks[data] = len(tiles) // 32
                        tiles.extend(data)
                    pieces.extend(struct.pack("<HHhhHHhhHH", chunks[data], 0, left - 129, top - 1, 32, height,
                                              0x100, 0x100, 0xFF, 0))
            pieces.extend(struct.pack("<HHhhHHhhHH", 0xFFFF, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            piece_lists.append(bytes(pieces))
        if len(tiles) // 32 > TILE_LIMIT:
            raise ValueError(f"The staff roll scene {lines[0]!r} needs more than {TILE_LIMIT} tiles.")
        scene_records.append((len(lines), first, append(bytes(tiles)), len(tiles) // 4))
    if len(piece_lists) > 255:
        raise ValueError("The staff roll has more than 255 lines.")
    frames = append(b"".join(struct.pack("<I", append(pieces)) for pieces in piece_lists))
    animations = append(b"".join(struct.pack("<I", append(struct.pack("<HHHH", kind, 1, 0xFFFF, 0)))
                                 for kind in range(len(piece_lists))))
    table = b"".join(struct.pack("<BBHiII", count, first, 0, 0x8000, tiles, words)
                     for count, first, tiles, words in scene_records)
    scene_table = append(table + struct.pack("<BBHiII", 0xFF, 0, 0, 0, 0, 0))
    return scene_table, animations, frames
